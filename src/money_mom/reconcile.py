"""Reconciliation: compare a bank or wallet statement with the ledger, one account at a time.

The statement is the outside evidence. For every statement row the ledger either has a matching entry or it does
not, and the report says exactly which rows are missing, which ledger entries the statement does not know about,
which pairs disagree on the amount, and which charges appear more often on the statement than in the ledger (a
possible double charge). The closing balance is checked too. Only a fully clean reconciliation may be sealed with a
balance assertion, which locks the period.

Nothing is changed by comparing. To fix what is missing, import the statement (`import run`); to correct a wrong
amount, void the entry and record it again.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from .errors import LedgerError
from .events import check_currency, parse_date
from .importing import StatementRow, _money, _statement_account, account_currency, row_hashes
from .ledger import Ledger

TOLERANCE_DAYS = 3
SIMILAR = 0.6  # how alike two descriptions must be to call an amount difference a probable typo


@dataclass(frozen=True)
class LedgerItem:
    txn_id: str
    date: _dt.date
    amount: Decimal
    text: str
    import_hash: str | None


def rows_from_json(items: Any) -> list[StatementRow]:
    """Statement rows an agent read from a PDF or a screenshot: [{"date", "amount", "payee", "description", "id"}]."""
    if not isinstance(items, list) or not items:
        raise LedgerError("expected a non-empty list of statement rows", code="invalid_statement")
    rows = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise LedgerError(f"row {index} must be an object", code="invalid_statement")
        extra = set(item) - {"date", "amount", "payee", "description", "id", "balance"}
        if extra:
            raise LedgerError(f"row {index}: unknown field(s) {sorted(extra)}", code="invalid_statement")
        try:
            day = parse_date(item.get("date"))
            amount = _money(str(item["amount"])) if isinstance(item.get("amount"), (str, int)) else None
            balance = _money(str(item["balance"])) if item.get("balance") not in (None, "") else None
        except (ValueError, KeyError, LedgerError):
            raise LedgerError(f"row {index}: needs a date (YYYY-MM-DD) and an amount as text, e.g. \"-38.00\"", code="invalid_statement") from None
        if amount is None or amount == 0:
            raise LedgerError(f"row {index}: needs a non-zero amount (positive = money into the account)", code="invalid_statement")
        rows.append(StatementRow(index, day, amount, str(item.get("payee") or ""), str(item.get("description") or ""),
                                 str(item.get("id") or ""), balance, {}))
    return rows


def _alike(a: str, b: str) -> float:
    a, b = a.strip().casefold(), b.strip().casefold()
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _fmt(value: Decimal) -> str:
    return format(value, "f")


def _row(row: StatementRow) -> dict[str, Any]:
    return {"line": row.line, "date": row.date.isoformat(), "amount": _fmt(row.amount),
            "payee": row.payee, "description": row.description}


def _item(item: LedgerItem) -> dict[str, Any]:
    return {"id": item.txn_id, "date": item.date.isoformat(), "amount": _fmt(item.amount), "text": item.text}


def reconcile(
    ledger: Ledger,
    account: str,
    rows: list[StatementRow],
    *,
    ccy: str | None = None,
    since: _dt.date | None = None,
    until: _dt.date | None = None,
    tolerance_days: int = TOLERANCE_DAYS,
    closing_balance: Decimal | None = None,
    closing_date: _dt.date | None = None,
) -> dict[str, Any]:
    ledger.reload()
    state = ledger.state
    rows = [r for r in rows if not ((since and r.date < since) or (until and r.date > until))]
    if not rows:
        raise LedgerError("no statement rows in the chosen range", code="invalid_statement")
    start, end = since or min(r.date for r in rows), until or max(r.date for r in rows)
    if closing_date is not None and until is None:
        end = max(end, closing_date)  # a closing balance says the statement covers that day, rows or not
    name = _statement_account(ledger, account, start)
    ccy = check_currency(ccy or account_currency(ledger, name))
    if (closing_balance is None) != (closing_date is None):
        raise LedgerError("give the closing balance and its date together", code="usage_error")

    window = tolerance_days
    items = [
        LedgerItem(rec.id, rec.date, p.amount, f"{rec.event.payee or ''} {rec.event.narration or ''}".strip(), rec.event.import_hash)
        for rec in state.txns.values() if rec.status == "posted"
        for p in rec.postings
        if p.account == name and p.ccy == ccy and start - _dt.timedelta(days=window) <= rec.date <= end + _dt.timedelta(days=window)
    ]
    pending = [
        {"id": rec.id, "date": rec.date.isoformat(), "amount": _fmt(p.amount), "text": f"{rec.event.payee or ''} {rec.event.narration or ''}".strip()}
        for rec in state.pending() for p in rec.postings
        if p.account == name and p.ccy == ccy and start <= rec.date <= end
    ]

    hashes = row_hashes(rows, name, ccy)
    by_hash: dict[str, int] = {}
    for index, item in enumerate(items):
        if item.import_hash:
            by_hash.setdefault(item.import_hash, index)
    matched: list[tuple[int, int]] = []
    used_rows, used_items = set(), set()
    for r_index, digest in enumerate(hashes):
        i_index = by_hash.get(digest)
        if i_index is not None and i_index not in used_items:
            matched.append((r_index, i_index))
            used_rows.add(r_index)
            used_items.add(i_index)
    by_hash_count = len(matched)

    candidates = sorted(
        (abs((rows[r].date - items[i].date).days), -_alike(rows[r].payee + " " + rows[r].description, items[i].text), r, i)
        for r in range(len(rows)) if r not in used_rows
        for i in range(len(items)) if i not in used_items
        if rows[r].amount == items[i].amount and abs((rows[r].date - items[i].date).days) <= tolerance_days
    )
    for _, _, r, i in candidates:
        if r not in used_rows and i not in used_items:
            matched.append((r, i))
            used_rows.add(r)
            used_items.add(i)

    left_rows = [r for r in range(len(rows)) if r not in used_rows]
    left_items = [i for i in range(len(items)) if i not in used_items and start <= items[i].date <= end]

    pairs = sorted(
        (-_alike(rows[r].payee + " " + rows[r].description, items[i].text), abs((rows[r].date - items[i].date).days), r, i)
        for r in left_rows for i in left_items
        if abs((rows[r].date - items[i].date).days) <= tolerance_days
        and _alike(rows[r].payee + " " + rows[r].description, items[i].text) >= SIMILAR
        and (rows[r].amount > 0) == (items[i].amount > 0)
    )
    mismatches, mismatch_idx, taken_rows, taken_items = [], [], set(), set()
    for _, _, r, i in pairs:
        if r in taken_rows or i in taken_items:
            continue
        taken_rows.add(r)
        taken_items.add(i)
        mismatch_idx.append((r, i))
        mismatches.append({"statement": _row(rows[r]), "ledger": _item(items[i]),
                           "difference": _fmt(rows[r].amount - items[i].amount)})
    missing_in_ledger_idx = [r for r in left_rows if r not in taken_rows]
    missing_in_statement_idx = [i for i in left_items if i not in taken_items]

    matched_keys: dict[tuple, int] = {}
    for r, _ in matched:
        key = (rows[r].date, rows[r].amount, rows[r].payee)
        matched_keys[key] = matched_keys.get(key, 0) + 1
    missing_in_ledger = []
    for r in missing_in_ledger_idx:
        entry = _row(rows[r])
        entry["possible_double_charge"] = (rows[r].date, rows[r].amount, rows[r].payee) in matched_keys
        missing_in_ledger.append(entry)

    balance: dict[str, Any] | None = None
    if closing_balance is not None and closing_date is not None:
        in_ledger = state.balance_of(name, closing_date).get(ccy, Decimal(0))
        difference = closing_balance - in_ledger
        expected = (
            sum((rows[r].amount for r in missing_in_ledger_idx if rows[r].date <= closing_date), Decimal(0))
            - sum((items[i].amount for i in missing_in_statement_idx if items[i].date <= closing_date), Decimal(0))
            + sum((rows[r].amount - items[i].amount for r, i in mismatch_idx if rows[r].date <= closing_date), Decimal(0))
        )
        balance = {
            "date": closing_date.isoformat(), "statement": _fmt(closing_balance), "ledger": _fmt(in_ledger),
            "difference": _fmt(difference), "ok": difference == 0,
            "explained_by_the_differences_listed": difference != 0 and difference == expected,
        }
    clean = not missing_in_ledger and not missing_in_statement_idx and not mismatches and (balance is None or balance["ok"])
    return {
        "account": name, "ccy": ccy, "from": start.isoformat(), "to": end.isoformat(), "tolerance_days": tolerance_days,
        "statement_rows": len(rows), "ledger_entries": len([i for i in items if start <= i.date <= end]),
        "matched": len(matched), "matched_by_import_hash": by_hash_count,
        "missing_in_ledger": missing_in_ledger,
        "missing_in_statement": [_item(items[i]) for i in missing_in_statement_idx],
        "amount_mismatches": mismatches,
        "pending_in_range": pending,
        "balance": balance, "clean": clean,
        "can_assert": clean and balance is not None,
    }


def seal(
    ledger: Ledger, report: dict[str, Any], *, actor: tuple[str, str] = ("human", "user")
) -> dict[str, Any]:
    """Write the balance assertion that locks the reconciled period, but only for a clean reconciliation."""
    if not report["can_assert"]:
        reasons = []
        if report["missing_in_ledger"]:
            reasons.append(f"{len(report['missing_in_ledger'])} statement row(s) are not in the ledger")
        if report["missing_in_statement"]:
            reasons.append(f"{len(report['missing_in_statement'])} ledger entr(ies) are not on the statement")
        if report["amount_mismatches"]:
            reasons.append(f"{len(report['amount_mismatches'])} amount(s) disagree")
        if report["balance"] is None:
            reasons.append("no closing balance was given")
        elif not report["balance"]["ok"]:
            reasons.append(f"the closing balance differs by {report['balance']['difference']}")
        raise LedgerError(
            "not sealing: " + "; ".join(reasons) + ". Resolve these first, then reconcile again.",
            code="reconcile_not_clean", details={"reasons": reasons},
        )
    balance = report["balance"]
    event = ledger.assert_balance(
        report["account"], balance["date"], balance["statement"], report["ccy"], actor=actor,
    )
    return {"asserted": True, "id": event.id, "account": report["account"], "date": balance["date"], "balance": balance["statement"]}
