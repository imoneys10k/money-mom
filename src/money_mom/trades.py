"""Importing trades and cash events: from a TradeGit journal, or from rows an agent read off a statement.

Money Mom does not parse broker files itself. IBKR and Schwab exports change, and a parser written without real
samples would be a guess; TradeGit already reads them, so its normalised journal (JSONL, append-only, with
`supersedes` and `void` records) is the input, read-only. For any other source the agent reads the statement and
hands over simple rows (see `read_rows`), the same way it does for a PDF bank statement.

What cannot be recorded safely is listed, never guessed: options and futures, short sales, zero-price events such
as expiries, corporate actions. A sale that takes more shares than the ledger holds stops the whole batch and says
which row, because the usual cause is a missing opening position, and recording around it would corrupt every
later lot. Importing the same file again is safe: each row has a stable hash.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .errors import LedgerError
from .events import Cost, Posting, check_currency
from .invest import _account, _fmt, investing_accounts, number, plan_buy, plan_sell, symbol_of
from .inventory import replay
from .ledger import Ledger

CASH_KINDS = {"DIVIDEND": "dividend", "INTEREST": "interest", "FEE": "fee", "TAX": "tax",
              "DEPOSIT": "deposit", "WITHDRAWAL": "withdrawal"}
ROW_TYPES = ("buy", "sell", "dividend", "interest", "fee", "tax", "deposit", "withdrawal")


@dataclass
class TradeRow:
    key: str
    date: _dt.date
    ts: str
    kind: str
    ccy: str
    symbol: str | None = None
    units: Decimal | None = None
    price: Decimal | None = None
    fee: Decimal = Decimal(0)
    amount: Decimal | None = None  # cash events, positive
    account: str | None = None  # the account name in the source, to be mapped
    where: str = ""


def _dec(value: Any, what: str) -> Decimal:
    """A number as written, without the noise a JSON float brings (100.0 is 100, 0.50 is 0.5)."""
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{what} is not a number: {value!r}") from None
    if not parsed.is_finite():
        raise ValueError(f"{what} is not a number: {value!r}")
    return parsed.quantize(Decimal(1)) if parsed == parsed.to_integral_value() else parsed.normalize()


def _date_of(ts: str) -> _dt.date:
    return _dt.date.fromisoformat(ts[:10])


def read_tradegit(path: Path) -> tuple[list[TradeRow], list[dict[str, Any]], int]:
    """Rows from a TradeGit journal: a `.jsonl` file or a folder of them. Returns (rows, unsupported, records read)."""
    files = sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]
    if not files:
        raise LedgerError(f"no .jsonl files under {path}", code="invalid_statement")
    records: dict[str, tuple[dict[str, Any], str]] = {}
    seen = 0
    for file in files:
        for number_, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as err:
                raise LedgerError(f"{file.name}:{number_} is not valid JSON: {err.msg}", code="invalid_statement") from None
            if not isinstance(obj, dict):
                raise LedgerError(f"{file.name}:{number_} is not a record", code="invalid_statement")
            seen += 1
            if obj.get("kind") == "void":
                records.pop(str(obj.get("voids")), None)
                continue
            if obj.get("supersedes"):
                records.pop(str(obj["supersedes"]), None)
            records[str(obj.get("id") or f"{file.name}:{number_}")] = (obj, f"{file.name}:{number_}")
    rows: list[TradeRow] = []
    unsupported: list[dict[str, Any]] = []

    def skip(where: str, what: str, why: str) -> None:
        unsupported.append({"where": where, "what": what, "reason": why})

    for obj, where in records.values():
        kind, symbol = obj.get("kind"), str(obj.get("symbol") or "")
        try:
            ts = str(obj["ts"])
            day = _date_of(ts)
            ccy = str(obj.get("currency") or "").upper()
            key = str(obj.get("dedup_key") or obj.get("id") or where)
            if kind == "trade":
                label = f"{obj.get('side')} {obj.get('quantity')} {symbol}"
                if obj.get("asset_class") not in (None, "STK", "ETF"):
                    skip(where, label, f"asset class {obj.get('asset_class')} is not supported (shares and ETFs only)")
                    continue
                if obj.get("side") not in ("BUY", "SELL"):
                    skip(where, label, "short selling is not supported")
                    continue
                if _dec(obj.get("multiplier", 1), "multiplier") != 1:
                    skip(where, label, "a contract multiplier other than 1 is not supported")
                    continue
                if not ccy:
                    skip(where, label, "the record has no currency")
                    continue
                units, price = _dec(obj["quantity"], "quantity"), _dec(obj["price"], "price")
                if units <= 0 or price <= 0:
                    skip(where, label, "a zero price or quantity (for example an expiry) is never guessed")
                    continue
                fees = obj.get("fees_total")
                if fees is None:
                    fees = sum((abs(_dec(v, "fee")) for v in (obj.get("fees") or {}).values()), Decimal(0))
                rows.append(TradeRow(key, day, ts, obj["side"].lower(), ccy, symbol_of(symbol), units, price,
                                     abs(_dec(fees, "fees")), account=obj.get("account"), where=where))
            elif kind == "cash":
                cash = CASH_KINDS.get(str(obj.get("cash_type")))
                label = f"{obj.get('cash_type')} {obj.get('amount')}"
                if cash is None:
                    skip(where, label, f"cash type {obj.get('cash_type')} is not supported")
                    continue
                if not ccy:
                    skip(where, label, "the record has no currency")
                    continue
                amount = _dec(obj["amount"], "amount")
                if amount == 0:
                    skip(where, label, "a zero amount")
                    continue
                if cash in ("dividend", "interest") and amount < 0:
                    skip(where, label, "a negative dividend or interest (a reversal) is never guessed")
                    continue
                if cash in ("fee", "tax") and amount > 0:
                    skip(where, label, "a refunded fee or tax is never guessed")
                    continue
                if cash == "dividend" and not symbol:
                    skip(where, label, "a dividend without a symbol")
                    continue
                rows.append(TradeRow(key, day, ts, cash, ccy, symbol or None, amount=abs(amount),
                                     account=obj.get("account"), where=where))
            else:
                skip(where, str(kind), "not a trade or cash record")
        except (KeyError, ValueError, LedgerError) as err:
            skip(where, str(kind), f"unreadable: {err}")
    return rows, unsupported, seen


def read_rows(items: Any) -> tuple[list[TradeRow], list[dict[str, Any]], int]:
    """Rows an agent read off a statement: [{"date", "type", "symbol", "units", "price", "ccy", "fee", "amount", "id"}].
    `type` is one of buy, sell, dividend, interest, fee, tax, deposit, withdrawal."""
    if not isinstance(items, list) or not items:
        raise LedgerError("expected a non-empty JSON list of rows", code="invalid_statement")
    rows: list[TradeRow] = []
    for index, item in enumerate(items, start=1):
        where = f"row {index}"
        if not isinstance(item, dict):
            raise LedgerError(f"{where} is not an object", code="invalid_statement")
        extra = set(item) - {"date", "type", "symbol", "units", "price", "ccy", "fee", "amount", "id", "account"}
        if extra:
            raise LedgerError(f"{where}: unknown field(s) {sorted(extra)}", code="invalid_statement")
        try:
            kind = str(item.get("type", "")).lower()
            if kind not in ROW_TYPES:
                raise ValueError(f"type must be one of {', '.join(ROW_TYPES)}")
            day = _dt.date.fromisoformat(str(item["date"]))
            ccy = check_currency(str(item.get("ccy", "")).upper(), "ccy")
            row = TradeRow(str(item.get("id") or ""), day, f"{day.isoformat()}T00:00:00Z", kind, ccy,
                           account=item.get("account"), where=where)
            if kind in ("buy", "sell"):
                row.symbol = symbol_of(str(item["symbol"]))
                row.units = number(item["units"], "units")
                row.price = number(item["price"], "price")
                row.fee = abs(number(item.get("fee", 0), "fee", positive=False))
            else:
                row.amount = abs(number(item["amount"], "amount"))
                if kind == "dividend":
                    row.symbol = symbol_of(str(item["symbol"]))
        except (KeyError, ValueError, LedgerError) as err:
            raise LedgerError(f"{where}: {err}", code="invalid_statement") from None
        if not row.key:
            digest = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            row.key = f"content:{digest[:24]}"
        rows.append(row)
    return rows, [], len(rows)


def import_trades(
    ledger: Ledger,
    rows: list[TradeRow],
    unsupported: list[dict[str, Any]],
    seen: int,
    *,
    label: str,
    sha256: str | None,
    account: str | None,
    account_map: dict[str, str] | None = None,
    cash: str | None = None,
    since: _dt.date | None = None,
    until: _dt.date | None = None,
    actor: tuple[str, str] = ("human", "user"),
    confidence: float | None = None,
    dry_run: bool = False,
    lot_method: str = "fifo",
) -> dict[str, Any]:
    ledger.reload()
    if actor[0] == "agent":
        if confidence is None:
            raise LedgerError("an agent must state a confidence (0 to 1) for an import", code="confidence_required")
        if confidence < ledger.auto_post_confidence:
            raise LedgerError(
                f"confidence {confidence:g} is below the auto-post threshold {ledger.auto_post_confidence:g}; "
                "trades are not held as pending (a held sale would break the lots after it): ask the user first",
                code="confidence_too_low",
            )
    live = ledger.state.live_import_hashes()
    source = {"type": "import", "ref": label, **({"sha256": sha256} if sha256 else {})}
    entries = list(ledger.state.lot_entries())
    events: list[dict[str, Any]] = []
    planned: list[TradeRow] = []
    problems: list[dict[str, Any]] = []
    counts = {"buy": 0, "sell": 0, "dividend": 0, "other_cash": 0}
    duplicates = out_of_range = 0
    for row in sorted(rows, key=lambda r: (r.ts, r.key)):
        if (since and row.date < since) or (until and row.date > until):
            out_of_range += 1
            continue
        digest = "trd-" + hashlib.sha256(f"{row.key}|{row.kind}".encode()).hexdigest()[:32]
        if digest in live:
            duplicates += 1
            continue
        try:
            term = (account_map or {}).get(row.account or "", account)
            if not term:
                raise LedgerError(
                    f"the source account {row.account!r} is not mapped: give --account, or --account-map {row.account}=ACCOUNT",
                    code="unresolved_account",
                )
            holding = _account(ledger, term, ("Assets",), row.date, "account")
            cash_account = holding if cash is None else _account(ledger, cash, ("Assets", "Liabilities"), row.date, "cash")
            status, status_note = "posted", None
            if row.kind == "buy":
                postings, note = plan_buy(ledger, units=row.units, symbol=row.symbol, price=row.price, ccy=row.ccy,  # type: ignore[arg-type]
                                          account=holding, cash=cash_account, date=row.date, fee=row.fee)
                counts["buy"] += 1
            elif row.kind == "sell":
                lots = replay(entries, row.date)[0]
                postings, note = plan_sell(ledger, units=row.units, symbol=row.symbol, price=row.price, ccy=row.ccy,  # type: ignore[arg-type]
                                           account=holding, cash=cash_account, date=row.date, fee=row.fee, method=lot_method, lots=lots)
                counts["sell"] += 1
            else:
                postings, note, status = _cash_postings(ledger, row, cash_account)
                counts["dividend" if row.kind == "dividend" else "other_cash"] += 1
        except LedgerError as err:
            problems.append({"where": row.where, "date": row.date.isoformat(), "what": f"{row.kind} {row.units or row.amount} {row.symbol or ''}".strip(),
                             "code": err.code, "message": str(err)})
            continue
        raw = ledger.new_event(
            "txn", actor=actor, source=source, confidence=confidence, date=row.date.isoformat(), status=status,
            postings=postings, narration=_narration(row), import_hash=digest,
            meta={"trade": note, "import": {"file": label, "where": row.where}},
        )
        events.append(raw)
        planned.append(row)
        if status == "posted":
            entries.append((row.date, raw["id"], _as_postings(postings)))
    written = False
    if problems and not dry_run:
        raise LedgerError(
            f"{len(problems)} row(s) cannot be recorded, so nothing was imported. First: {problems[0]['where']}: {problems[0]['message']}",
            code="invalid_trades", details={"problems": problems[:20]},
        )
    if events and not dry_run:
        try:
            ledger.append_many(events, clamp_ts=True)
        except LedgerError as err:
            match = re.search(r"event #(\d+)", err.where or "")
            if match:
                row = planned[int(match.group(1)) - 1]
                err.message = f"{err.message} ({row.where}: {row.date} {row.kind} {row.symbol or ''}); nothing was imported"
            raise
        written = True
    return {
        "source": label, "records_read": seen, "rows": len(rows), "imported": len(events), "buys": counts["buy"],
        "sells": counts["sell"], "dividends": counts["dividend"], "other_cash": counts["other_cash"],
        "duplicates_skipped": duplicates, "out_of_range": out_of_range, "unsupported": unsupported[:25],
        "unsupported_count": len(unsupported), "problems": problems[:20], "written": written, "dry_run": dry_run,
    }


def _as_postings(postings: list[dict[str, Any]]) -> tuple[Posting, ...]:
    out = []
    for p in postings:
        cost = None
        if p.get("cost"):
            c = p["cost"]
            cost = Cost(Decimal(c["per_unit"]), c["ccy"], _dt.date.fromisoformat(c["date"]) if c.get("date") else None)
        out.append(Posting(p["account"], Decimal(p["amount"]), p["ccy"], cost))
    return tuple(out)


def _narration(row: TradeRow) -> str:
    return f"{row.kind} {row.units or row.amount} {row.symbol or ''}".strip()


def _cash_postings(ledger: Ledger, row: TradeRow, cash_account: str):
    names = investing_accounts(ledger)
    amount = row.amount  # positive
    ccy = row.ccy
    if row.kind == "dividend":
        note = {"kind": "dividend", "symbol": row.symbol, "ccy": ccy, "gross": _fmt(amount), "tax": "0"}
        return ([{"account": cash_account, "amount": _fmt(amount), "ccy": ccy},
                 {"account": names["dividends"], "amount": _fmt(-amount), "ccy": ccy}], note, "posted")
    note = {"kind": row.kind, "ccy": ccy, "amount": _fmt(amount)}
    if row.kind == "interest":
        return ([{"account": cash_account, "amount": _fmt(amount), "ccy": ccy},
                 {"account": names["interest"], "amount": _fmt(-amount), "ccy": ccy}], note, "posted")
    if row.kind in ("fee", "tax"):
        return ([{"account": cash_account, "amount": _fmt(-amount), "ccy": ccy},
                 {"account": names["fees" if row.kind == "fee" else "tax"], "amount": _fmt(amount), "ccy": ccy}], note, "posted")
    sign = amount if row.kind == "deposit" else -amount  # the other side is the user's own bank: unknown, so pending
    return ([{"account": cash_account, "amount": _fmt(sign), "ccy": ccy},
             {"account": None, "amount": _fmt(-sign), "ccy": ccy}], note, "pending")
