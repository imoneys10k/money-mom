"""Monthly report: what came in, what went out, where it went, and how it compares with the month before.

Everything here is computed from the ledger and the stored exchange rates; nothing is estimated and the network
is never used. Three rules keep the numbers honest:

- Pending entries are not counted (they are not decided yet); the report says how many there are.
- Amounts are converted at the rate of the last day of the period (or today, for a month still in progress). A
  currency with no stored rate is listed under `missing` and left out of the converted figures, which are then
  marked `partial`. A missing figure is `null`, never 0.
- Moving money between your own accounts, and exchanging currencies, is neither income nor spending: only
  `Income:*` and `Expenses:*` postings count.
"""

from __future__ import annotations

import calendar
import datetime as _dt
from decimal import Decimal
from typing import Any

from .errors import LedgerError
from .ledger import Ledger
from .rates import convert_balances, find_rate, round_money


def parse_month(text: str) -> tuple[_dt.date, _dt.date]:
    """`2026-09` -> (first day, last day)."""
    try:
        year_text, month_text = text.split("-")
        year, month = int(year_text), int(month_text)
        if len(year_text) != 4 or len(month_text) != 2:
            raise ValueError
        first = _dt.date(year, month, 1)
    except ValueError:
        raise LedgerError(f"a month looks like 2026-09, got {text!r}", code="usage_error") from None
    return first, _dt.date(year, month, calendar.monthrange(year, month)[1])


def previous_month(first: _dt.date) -> str:
    last = first - _dt.timedelta(days=1)
    return f"{last.year:04d}-{last.month:02d}"


def _pct(part: Decimal, whole: Decimal) -> str | None:
    if whole == 0:
        return None
    return format((part * 100 / whole).quantize(Decimal("0.1")), "f")


def _money(amount: Decimal, ccy: str) -> str:
    return format(round_money(amount, ccy), "f")


def _convert(ledger: Ledger, amount: Decimal, ccy: str, target: str, on: _dt.date) -> Decimal | None:
    info = find_rate(ledger.state, ccy, target, on, pivot=ledger.base_currency)
    return None if info is None else amount * info.rate


class _Sums:
    """Amounts per currency, with the converted total kept unrounded until the very end."""

    def __init__(self) -> None:
        self.by_ccy: dict[str, Decimal] = {}

    def add(self, ccy: str, amount: Decimal) -> None:
        self.by_ccy[ccy] = self.by_ccy.get(ccy, Decimal(0)) + amount

    def converted(self, ledger: Ledger, target: str, on: _dt.date) -> tuple[Decimal, list[str]]:
        total, missing = Decimal(0), []
        for ccy, amount in self.by_ccy.items():
            value = _convert(ledger, amount, ccy, target, on)
            if value is None:
                missing.append(ccy)
            else:
                total += value
        return total, sorted(missing)

    def shown(self) -> dict[str, str]:
        return {ccy: _money(amount, ccy) for ccy, amount in sorted(self.by_ccy.items()) if amount != 0}


def _collect(ledger: Ledger, first: _dt.date, last: _dt.date) -> dict[str, Any]:
    income, spending = _Sums(), _Sums()
    categories: dict[str, _Sums] = {}
    postings: list[dict[str, Any]] = []
    count = 0
    for rec in ledger.state.txns.values():
        if rec.status != "posted" or not (first <= rec.date <= last):
            continue
        touched = False
        for p in rec.postings:
            root, _, rest = p.account.partition(":")
            if root == "Income":
                income.add(p.ccy, -p.amount)
                touched = True
            elif root == "Expenses":
                spending.add(p.ccy, p.amount)
                categories.setdefault(rest.split(":", 1)[0], _Sums()).add(p.ccy, p.amount)
                postings.append({
                    "txn_id": rec.id, "date": rec.date.isoformat(), "account": p.account,
                    "payee": rec.event.payee, "narration": rec.event.narration, "amount": p.amount, "ccy": p.ccy,
                })
                touched = True
        count += touched
    return {"income": income, "spending": spending, "categories": categories, "postings": postings, "entries": count}


def _summary(ledger: Ledger, data: dict[str, Any], target: str, on: _dt.date) -> dict[str, Any]:
    inc_total, inc_missing = data["income"].converted(ledger, target, on)
    exp_total, exp_missing = data["spending"].converted(ledger, target, on)
    missing = sorted(set(inc_missing) | set(exp_missing))
    net = inc_total - exp_total
    return {
        "income": {"by_currency": data["income"].shown(), "total": _money(inc_total, target), "missing": inc_missing},
        "expenses": {"by_currency": data["spending"].shown(), "total": _money(exp_total, target), "missing": exp_missing},
        "net": _money(net, target),
        "partial": bool(missing),
        "missing": missing,
        "_raw": {"income": inc_total, "expenses": exp_total, "net": net},
        "entries": data["entries"],
    }


def _worth(ledger: Ledger, on: _dt.date, target: str) -> dict[str, Any]:
    totals: dict[str, Decimal] = {}
    for (account, ccy), amount in ledger.state.balances(on).items():
        if account.split(":", 1)[0] in ("Assets", "Liabilities"):
            totals[ccy] = totals.get(ccy, Decimal(0)) + amount
    result = convert_balances(
        ledger.state, {("net worth", c): a for c, a in totals.items() if a != 0}, target, on, pivot=ledger.base_currency
    )
    return {"as_of": on.isoformat(), "total": result["total"], "partial": result["partial"], "missing": result["missing"]}


def monthly_report(
    ledger: Ledger, month: str | None = None, *, target: str | None = None, today: _dt.date | None = None, top: int = 5
) -> dict[str, Any]:
    ledger.reload()
    today = today or _dt.date.today()
    first, last = parse_month(month or f"{today.year:04d}-{today.month:02d}")
    in_progress = first <= today <= last
    as_of = min(last, today)
    if first > today:
        raise LedgerError(f"{first:%Y-%m} has not started yet", code="usage_error")
    target = target or ledger.base_currency
    before_first, before_last = parse_month(previous_month(first))

    now = _collect(ledger, first, last)
    then = _collect(ledger, before_first, before_last)
    cur = _summary(ledger, now, target, as_of)
    prev = _summary(ledger, then, target, before_last)
    has_prev = then["entries"] > 0

    rows = []
    for name, sums in now["categories"].items():
        value, missing = sums.converted(ledger, target, as_of)
        old = then["categories"].get(name)
        old_value = old.converted(ledger, target, before_last) if old else (Decimal(0), [])
        usable = not missing and not old_value[1] and has_prev
        rows.append({
            "category": name,
            "by_currency": sums.shown(),
            "converted": None if len(missing) == len(sums.by_ccy) else _money(value, target),
            "share_percent": None if cur["expenses"]["missing"] else _pct(value, cur["_raw"]["expenses"]),
            "previous": _money(old_value[0], target) if usable else None,
            "change": _money(value - old_value[0], target) if usable else None,
            "partial": bool(missing),
            "missing": missing,
            "_sort": value,
        })
    rows.sort(key=lambda r: (-r["_sort"], r["category"]))
    for row in rows:
        del row["_sort"]
    gone = [  # categories you spent on last month but not this month: worth seeing, not hiding
        name for name in then["categories"] if name not in now["categories"]
    ]

    biggest = []
    for item in now["postings"]:
        value = _convert(ledger, item["amount"], item["ccy"], target, as_of)
        if value is not None and value > 0:
            biggest.append((value, item))
    biggest.sort(key=lambda pair: (-pair[0], pair[1]["date"], pair[1]["txn_id"]))
    top_spending = [
        {
            "date": item["date"], "account": item["account"], "payee": item["payee"], "narration": item["narration"],
            "amount": _money(item["amount"], item["ccy"]), "ccy": item["ccy"], "converted": _money(value, target),
            "txn_id": item["txn_id"],
        }
        for value, item in biggest[: max(top, 0)]
    ]

    worth_start = _worth(ledger, first - _dt.timedelta(days=1), target)
    worth_end = _worth(ledger, as_of, target)
    worth_known = not worth_start["partial"] and not worth_end["partial"]
    worth = {
        "start": worth_start, "end": worth_end,
        "change": format(Decimal(worth_end["total"]) - Decimal(worth_start["total"]), "f") if worth_known else None,
    }

    comparison: dict[str, Any] = {"month": f"{before_first:%Y-%m}", "has_data": has_prev}
    if has_prev:
        comparison.update(
            income=prev["income"]["total"], expenses=prev["expenses"]["total"], net=prev["net"], partial=prev["partial"],
            expenses_change=None, expenses_change_percent=None, income_change=None,
        )
        if not prev["partial"] and not cur["partial"]:
            delta = cur["_raw"]["expenses"] - prev["_raw"]["expenses"]
            comparison["expenses_change"] = _money(delta, target)
            comparison["expenses_change_percent"] = _pct(delta, prev["_raw"]["expenses"])
            comparison["income_change"] = _money(cur["_raw"]["income"] - prev["_raw"]["income"], target)
    comparison["dropped_categories"] = sorted(gone)

    rate = None
    if not cur["partial"] and cur["_raw"]["income"] > 0:
        rate = _pct(cur["_raw"]["net"], cur["_raw"]["income"])

    pending = [r for r in ledger.state.pending() if first <= r.date <= last]
    sealed = sorted({
        r.event.account for r in ledger.state.assertions.values() if not r.voided and first <= r.event.date <= last
    })
    stale = _stale(ledger, now, target, as_of)

    notes = []
    if in_progress:
        notes.append(f"{first:%Y-%m} is not over: figures are month to date ({first} to {as_of}), so comparing them with a full month is unfair.")
    if pending:
        many = len(pending) != 1
        notes.append(f"{len(pending)} pending {'entries' if many else 'entry'} in this month {'are' if many else 'is'} not counted until confirmed.")
    if cur["missing"]:
        notes.append(f"No exchange rate for {', '.join(cur['missing'])}: left out of the converted figures. Run `money-mom rates update`.")
    if stale:
        notes.append(f"Rates for {', '.join(stale)} are more than 7 days older than the figures they convert.")
    if not has_prev:
        notes.append(f"No entries in {before_first:%Y-%m}, so there is nothing to compare with.")
    if not now["entries"]:
        notes.append(f"No income or spending recorded in {first:%Y-%m}.")

    for part in (cur, prev):
        part.pop("_raw")
    return {
        "month": f"{first:%Y-%m}", "from": first.isoformat(), "to": last.isoformat(), "as_of": as_of.isoformat(),
        "in_progress": in_progress, "in": target,
        "income": cur["income"], "expenses": cur["expenses"], "net": cur["net"], "savings_rate_percent": rate,
        "partial": cur["partial"], "missing": cur["missing"], "entries": cur["entries"],
        "categories": rows, "top_spending": top_spending, "net_worth": worth, "previous": comparison,
        "checks": {
            "pending_in_month": len(pending), "pending_total": len(ledger.state.pending()),
            "accounts_with_assertion": sealed, "stale_rates": stale,
        },
        "notes": notes,
    }


def _stale(ledger: Ledger, data: dict[str, Any], target: str, on: _dt.date) -> list[str]:
    seen = set(data["income"].by_ccy) | set(data["spending"].by_ccy)
    out = []
    for ccy in sorted(seen):
        info = find_rate(ledger.state, ccy, target, on, pivot=ledger.base_currency)
        if info is not None and info.stale:
            out.append(ccy)
    return out


def month_before(first: _dt.date, back: int) -> _dt.date:
    """The first day of the month `back` months before the month starting on `first`."""
    index = first.year * 12 + first.month - 1 - back
    return _dt.date(index // 12, index % 12 + 1, 1)


def monthly_series(
    ledger: Ledger, months: int = 12, *, end: str | None = None, target: str | None = None,
    today: _dt.date | None = None,
) -> dict[str, Any]:
    """Income, spending, net and net worth for each of the last `months` months, oldest first.

    Months before the first posted entry are left out. A month with no income or spending has `null` figures
    (it is not a month of zero). Each month is converted at its own last day (today, for the current month).
    """
    if not 1 <= months <= 60:
        raise LedgerError("months must be between 1 and 60", code="usage_error")
    ledger.reload()
    today = today or _dt.date.today()
    last_first, _ = parse_month(end or f"{today.year:04d}-{today.month:02d}")
    if last_first > today:
        raise LedgerError(f"{last_first:%Y-%m} has not started yet", code="usage_error")
    target = target or ledger.base_currency
    earliest = min((r.date for r in ledger.state.txns.values() if r.status == "posted"), default=None)
    rows = []
    for back in range(months - 1, -1, -1):
        first = month_before(last_first, back)
        _, last = parse_month(f"{first:%Y-%m}")
        if earliest is None or last < earliest:
            continue
        as_of = min(last, today)
        data = _collect(ledger, first, last)
        summary = _summary(ledger, data, target, as_of)
        worth = _worth(ledger, as_of, target)
        has = data["entries"] > 0
        rows.append({
            "month": f"{first:%Y-%m}", "as_of": as_of.isoformat(), "in_progress": first <= today <= last,
            "entries": data["entries"],
            "income": summary["income"]["total"] if has else None,
            "expenses": summary["expenses"]["total"] if has else None,
            "net": summary["net"] if has else None,
            "partial": summary["partial"] if has else False, "missing": summary["missing"] if has else [],
            "net_worth": worth["total"], "net_worth_partial": worth["partial"], "net_worth_missing": worth["missing"],
        })
    return {"in": target, "months": rows}
