"""`money-mom doctor`: is the install healthy, and is the ledger in good shape?

A missing ledger is not a failure: it is the normal state right after installing. A ledger
that exists but cannot be read, or whose assertions no longer hold, is.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .cache import open_cache, run_query
from .errors import LedgerError
from .ledger import CONFIG_NAME, Ledger, _exclusive_lock


def run_doctor(root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    check("python", sys.version_info >= (3, 11), f"{platform.python_version()} ({sys.executable})")
    check("sqlite", True, sqlite3.sqlite_version)

    result: dict[str, Any] = {
        "version": __version__,
        "path": str(root),
        "ledger": None,
        "next_step": None,
        "checks": checks,
    }
    if not (root / CONFIG_NAME).is_file():
        result["next_step"] = (
            f"No ledger at {root}. Create one with `money-mom init --template cn` "
            "(or `--template en`)."
        )
        result["ok"] = all(c["ok"] for c in checks)
        return result

    try:
        ledger = Ledger.open(root)
    except LedgerError as err:
        check("ledger", False, str(err))
        result["ok"] = False
        return result
    state = ledger.state
    check("ledger", True, f"{len(state.events)} events replayed")

    problems = state.unmet_assertions()
    check(
        "assertions",
        not problems,
        "all live balance assertions hold" if not problems else "; ".join(p.message for p in problems),
    )

    probe = ledger.ledger_dir / f".doctor-{os.getpid()}"
    try:
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        with _exclusive_lock(ledger.ledger_dir / ".lock"):
            pass
        check("writable", True, "the ledger directory is writable and the write lock works")
    except OSError as err:
        check("writable", False, f"cannot write to {ledger.ledger_dir}: {err.strerror or err}")

    try:
        conn = open_cache(root, state)
        try:
            count = run_query(conn, "SELECT COUNT(*) FROM txns")["rows"][0][0]
        finally:
            conn.close()
        check("cache", True, f"SQLite cache readable ({count} transactions)")
    except (LedgerError, sqlite3.Error, OSError) as err:
        check("cache", False, str(err))

    last = state.events[-1].ts.isoformat() if state.events else None
    prices = state.effective_prices()
    latest_rate = max((d for by_date in prices.values() for d in by_date), default=None)
    result["ledger"] = {
        "base_currency": ledger.base_currency,
        "tone": ledger.config.get("tone", "normal"),
        "auto_post_confidence": ledger.auto_post_confidence,
        "accounts": len(state.accounts),
        "events": len(state.events),
        "posted": sum(1 for r in state.txns.values() if r.status == "posted"),
        "pending": len(state.pending()),
        "aliases": len(ledger.aliases()),
        "currencies": sorted(state.currencies_in_use() | {ledger.base_currency}),
        "rates": {"pairs": len(prices), "latest": latest_rate.isoformat() if latest_rate else None},
        "last_event_at": last,
    }
    result["ok"] = all(c["ok"] for c in checks)
    return result
