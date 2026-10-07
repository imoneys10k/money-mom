"""SQLite read cache derived from the event stream.

The cache is disposable: it is rebuilt whenever its fingerprint no longer matches the
ledger, and deleting it loses nothing. It exists so agents can ask questions in SQL.

Amounts are stored twice: `amount_text` is exact, `amount` is a REAL for convenient
`SUM(...)`. Balances, reports and assertions are always computed with `Decimal` in
Python, never from these floats.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .errors import LedgerError
from .state import LedgerState

CACHE_NAME = "cache.sqlite"
CACHE_LAYOUT = 3

SCHEMA_SQL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE accounts (
  name TEXT PRIMARY KEY, root TEXT NOT NULL, opened TEXT NOT NULL,
  closed TEXT, currencies TEXT
);

CREATE TABLE txns (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, status TEXT NOT NULL,
  narration TEXT, payee TEXT, import_hash TEXT,
  source_type TEXT, source_ref TEXT, confidence REAL,
  actor_type TEXT NOT NULL, actor_name TEXT NOT NULL, ts TEXT NOT NULL,
  confirmed_by TEXT, voided_by TEXT
);

CREATE TABLE postings (
  txn_id TEXT NOT NULL, idx INTEGER NOT NULL, account TEXT,
  amount_text TEXT NOT NULL, amount REAL NOT NULL, ccy TEXT NOT NULL,
  cost_per_unit TEXT, cost_ccy TEXT, cost_date TEXT,
  PRIMARY KEY (txn_id, idx)
);

CREATE TABLE assertions (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, account TEXT NOT NULL,
  amount_text TEXT NOT NULL, amount REAL NOT NULL, ccy TEXT NOT NULL,
  voided_by TEXT
);

CREATE TABLE prices (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, base TEXT NOT NULL, quote TEXT NOT NULL,
  rate_text TEXT NOT NULL, rate REAL NOT NULL, source_type TEXT, source_ref TEXT, recorded_at TEXT NOT NULL
);

CREATE TABLE events (
  seq INTEGER PRIMARY KEY, id TEXT NOT NULL, kind TEXT NOT NULL, ts TEXT NOT NULL,
  actor_type TEXT NOT NULL, actor_name TEXT NOT NULL, target TEXT
);

CREATE INDEX postings_account ON postings (account);
CREATE INDEX txns_date ON txns (date);

CREATE VIEW v_postings AS
  SELECT p.txn_id, p.idx, p.account, p.amount_text, p.amount, p.ccy,
         p.cost_per_unit, p.cost_ccy, p.cost_date,
         t.date, t.narration, t.payee, t.source_type, t.source_ref, t.confidence,
         t.actor_type, t.actor_name
  FROM postings p JOIN txns t ON t.id = p.txn_id
  WHERE t.status = 'posted';

CREATE VIEW v_pending AS
  SELECT t.id, t.date, t.payee, t.narration, t.confidence, t.source_type, t.source_ref,
         t.actor_type, t.actor_name,
         (SELECT group_concat(COALESCE(p.account, '?') || ' ' || p.amount_text || ' ' || p.ccy, '; ')
            FROM postings p WHERE p.txn_id = t.id) AS postings
  FROM txns t WHERE t.status = 'pending';

CREATE VIEW v_balances AS
  SELECT account, ccy, SUM(amount) AS amount FROM v_postings GROUP BY account, ccy;

CREATE VIEW v_rates AS
  SELECT p.base, p.quote, p.date, p.rate_text, p.rate, p.source_ref
  FROM prices p
  WHERE p.date = (SELECT MAX(q.date) FROM prices q WHERE q.base = p.base AND q.quote = p.quote);

CREATE VIEW v_monthly AS
  SELECT substr(date, 1, 7) AS month, account, ccy, SUM(amount) AS amount
  FROM v_postings GROUP BY month, account, ccy;
"""


def fingerprint(state: LedgerState) -> str:
    digest = hashlib.sha256()
    for event in state.events:
        digest.update(
            json.dumps(event.raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _build(conn: sqlite3.Connection, state: LedgerState, fp: str) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        "INSERT INTO meta VALUES (?, ?)",
        [("layout", str(CACHE_LAYOUT)), ("fingerprint", fp), ("events", str(len(state.events)))],
    )
    conn.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
        [
            (
                a.name,
                a.name.split(":", 1)[0],
                a.opened.isoformat(),
                a.closed.isoformat() if a.closed else None,
                ",".join(a.currencies) if a.currencies else None,
            )
            for a in state.accounts.values()
        ],
    )
    for rec in state.txns.values():
        ev = rec.event
        source = ev.source or {}
        conn.execute(
            "INSERT INTO txns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ev.id, ev.date.isoformat(), rec.status, ev.narration, ev.payee, ev.import_hash,
                source.get("type"), source.get("ref"), ev.confidence,
                ev.actor_type, ev.actor_name, ev.ts.isoformat(),
                rec.confirm_event_id, rec.void_event_id,
            ),
        )
        conn.executemany(
            "INSERT INTO postings VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (ev.id, i, p.account, format(p.amount, "f"), float(p.amount), p.ccy,
                 format(p.cost.per_unit, "f") if p.cost else None, p.cost.ccy if p.cost else None,
                 (p.cost.date or ev.date).isoformat() if p.cost else None)
                for i, p in enumerate(rec.postings)
            ],
        )
    conn.executemany(
        "INSERT INTO assertions VALUES (?,?,?,?,?,?,?)",
        [
            (
                r.event.id, r.event.date.isoformat(), r.event.account,
                format(r.event.amount, "f"), float(r.event.amount), r.event.ccy, r.void_event_id,
            )
            for r in state.assertions.values()
        ],
    )
    conn.executemany(
        "INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                r.event.id, r.event.date.isoformat(), r.event.base, r.event.quote,
                format(r.event.rate, "f"), float(r.event.rate),
                (r.event.source or {}).get("type"), (r.event.source or {}).get("ref"), r.event.ts.isoformat(),
            )
            for by_date in state.effective_prices().values()
            for r in by_date.values()
        ],
    )
    conn.executemany(
        "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
        [
            (i, e.id, e.kind, e.ts.isoformat(), e.actor_type, e.actor_name, getattr(e, "target", None))
            for i, e in enumerate(state.events, start=1)
        ],
    )
    conn.commit()


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _is_fresh(path: Path, fp: str) -> bool:
    if not path.is_file():
        return False
    try:
        conn = _open_readonly(path)
        try:
            rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return rows.get("layout") == str(CACHE_LAYOUT) and rows.get("fingerprint") == fp


def open_cache(root: Path, state: LedgerState) -> sqlite3.Connection:
    """Return a connection to an up-to-date cache, rebuilding it when stale or damaged.

    If the cache file cannot be written (read-only disk, a locked file on Windows), an
    in-memory database is used for this call instead, so queries still work.
    """
    fp = fingerprint(state)
    path = root / CACHE_NAME
    if _is_fresh(path, fp):
        return _open_readonly(path)
    tmp = root / f"{CACHE_NAME}-tmp-{os.getpid()}"
    try:
        tmp.unlink(missing_ok=True)
        conn = sqlite3.connect(tmp)
        try:
            _build(conn, state, fp)
        finally:
            conn.close()
        os.replace(tmp, path)
        return _open_readonly(path)
    except (OSError, sqlite3.Error):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        memory = sqlite3.connect(":memory:")
        _build(memory, state, fp)
        return memory


_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


def _authorizer(action: int, arg1: Any, arg2: Any, dbname: Any, source: Any) -> int:
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() == "load_extension":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def run_query(
    conn: sqlite3.Connection, sql: str, *, limit: int = 1000, timeout: float = 5.0
) -> dict[str, Any]:
    """Run one read-only statement. Anything that is not a plain SELECT is refused."""
    if limit < 1:
        raise LedgerError("limit must be at least 1", code="usage_error")
    conn.set_authorizer(_authorizer)
    deadline = time.monotonic() + timeout
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cursor = conn.execute(sql)
        if cursor.description is None:
            raise LedgerError("only SELECT queries are allowed", code="query_error")
        rows = cursor.fetchmany(limit + 1)
        columns = [d[0] for d in cursor.description]
    except sqlite3.Error as err:
        message = str(err)
        if "interrupted" in message:
            raise LedgerError(
                f"query took longer than {timeout:g}s and was stopped", code="query_timeout"
            ) from None
        if "not authorized" in message:
            message = "only plain read-only SELECT statements are allowed"
        raise LedgerError(message, code="query_error") from None
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)
    truncated = len(rows) > limit
    return {"columns": columns, "rows": [list(r) for r in rows[:limit]], "truncated": truncated}
