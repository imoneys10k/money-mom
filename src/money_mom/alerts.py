"""Subscriptions and anomalies: what repeats, what changed, and what looks wrong, with the evidence for each.

Nothing here decides anything for you. The program lists what its rules found, shows the entries behind each
finding, and says which rule fired, so an alert can be checked and dismissed. When there is not enough history
to be sure, it says nothing rather than guessing.

Rules (also returned in the result under `rules`):

- A payee is *recurring* when the same payee and currency is charged at a steady interval: weekly (about 7
  days, at least 4 charges), monthly (about 30 days, at least 3) or yearly (about 365 days, at least 2), with at
  least 80% of the gaps inside the window. Only entries that have a payee can be recognised, and payees are
  matched exactly after case and spacing are normalised.
- `price_change`: a recurring payee that always charged the same amount (within 2%) now charges a different one.
- `overdue`: a recurring payee whose next charge was due more than a grace period ago. It may have been cancelled
  or paid another way; the program cannot tell.
- `large_expense`: in a month, an expense more than 3 times the usual (median) one for its category and larger
  than any in the six months before, given at least 5 earlier expenses in that category and currency.
- `category_spike`: a category's monthly total in one currency at least 1.5 times its average over the three
  months before (all three must have entries), and at least 5% of that month's spending in the currency.
- `possible_duplicate`: the same payee, currency and amount twice within 2 days. Two real purchases can look the
  same, so this is a question, not an accusation.
"""

from __future__ import annotations

import datetime as _dt
import statistics
import unicodedata
from decimal import Decimal
from typing import Any

from .errors import LedgerError
from .ledger import Ledger
from .rates import find_rate, round_money
from .report import _collect, month_before, parse_month

RULES = {
    "recurring": {
        "weekly": {"interval_days": [5, 9], "min_charges": 4},
        "monthly": {"interval_days": [24, 38], "min_charges": 3},
        "yearly": {"interval_days": [350, 380], "min_charges": 2},
        "gaps_in_window": 0.8,
    },
    "price_change": {"stable_within": 0.02},
    "overdue": {"grace_days": "max(5, 20% of the interval)", "lapsed_after_intervals": 2.5},
    "large_expense": {"times_median": 3, "min_earlier_entries": 5, "lookback_months": 6},
    "category_spike": {"times_average": 1.5, "months": 3, "min_share_of_spending": 0.05},
    "possible_duplicate": {"within_days": 2},
}
CADENCES = (("weekly", 5, 9, 4), ("monthly", 24, 38, 3), ("yearly", 350, 380, 2))
PER_MONTH = {"weekly": Decimal(52) / 12, "monthly": Decimal(1), "yearly": Decimal(1) / 12}


def normalise(payee: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", payee).casefold().split())


def _spend_records(ledger: Ledger, upto: _dt.date) -> list[dict[str, Any]]:
    """One record per entry per payee-less or payee'd expense posting group, refunds left out."""
    out = []
    for rec in ledger.state.txns.values():
        if rec.status != "posted" or rec.date > upto:
            continue
        by_key: dict[tuple[str, str], Decimal] = {}
        for p in rec.postings:
            root, _, rest = p.account.partition(":")
            if root == "Expenses":
                key = (rest.split(":", 1)[0], p.ccy)
                by_key[key] = by_key.get(key, Decimal(0)) + p.amount
        for (category, ccy), amount in by_key.items():
            if amount > 0:
                out.append({
                    "txn_id": rec.id, "date": rec.date, "payee": rec.event.payee, "category": category,
                    "ccy": ccy, "amount": amount,
                })
    out.sort(key=lambda r: (r["date"], r["txn_id"]))
    return out


def _cadence(dates: list[_dt.date]) -> tuple[str, int] | None:
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    if not gaps:
        return None
    middle = statistics.median(gaps)
    for name, low, high, minimum in CADENCES:
        if low <= middle <= high and len(dates) >= minimum:
            inside = sum(1 for g in gaps if low <= g <= high)
            if inside >= max(1, 0.8 * len(gaps)):
                return name, round(middle)
    return None


def _next_due(last: _dt.date, cadence: str, interval: int) -> _dt.date:
    """When the next charge is due: the same day next month or year (clamped), or a week on."""
    if cadence == "weekly":
        return last + _dt.timedelta(days=interval)
    months = 1 if cadence == "monthly" else 12
    index = last.year * 12 + last.month - 1 + months
    year, month = index // 12, index % 12 + 1
    for day in range(last.day, 27, -1):
        try:
            return _dt.date(year, month, day)
        except ValueError:
            continue
    return _dt.date(year, month, min(last.day, 28))


def _money(amount: Decimal, ccy: str) -> str:
    return format(round_money(amount, ccy), "f")


def _convert(ledger: Ledger, amount: Decimal, ccy: str, target: str, on: _dt.date) -> Decimal | None:
    info = find_rate(ledger.state, ccy, target, on, pivot=ledger.base_currency)
    return None if info is None else amount * info.rate


def _subscriptions(ledger: Ledger, records: list[dict[str, Any]], as_of: _dt.date, target: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in records:
        if r["payee"]:
            groups.setdefault((normalise(r["payee"]), r["ccy"]), []).append(r)
    found = []
    for (_, ccy), items in groups.items():
        # one charge per entry: several expense lines of the same entry are one charge
        per_txn: dict[str, dict[str, Any]] = {}
        for r in items:
            merged = per_txn.setdefault(r["txn_id"], {**r, "amount": Decimal(0)})
            merged["amount"] += r["amount"]
        charges = sorted(per_txn.values(), key=lambda r: (r["date"], r["txn_id"]))
        shape = _cadence([c["date"] for c in charges])
        if shape is None:
            continue
        cadence, interval = shape
        amounts = [c["amount"] for c in charges]
        typical = statistics.median(amounts)
        earlier = amounts[:-1]
        base = statistics.median(earlier) if earlier else typical
        steady = all(abs(a - base) <= base * Decimal("0.02") for a in earlier) if earlier else False
        last = charges[-1]
        changed = steady and abs(last["amount"] - base) > base * Decimal("0.02")
        due = _next_due(last["date"], cadence, interval)
        grace = max(5, round(interval * 0.2))
        late = (as_of - due).days
        lapsed = (as_of - last["date"]).days > interval * 2.5
        status = "lapsed" if lapsed else "overdue" if late > grace else "active"
        monthly = _convert(ledger, last["amount"] * PER_MONTH[cadence], ccy, target, as_of)
        found.append({
            "payee": last["payee"], "ccy": ccy, "cadence": cadence, "interval_days": interval, "charges": len(charges),
            "typical": _money(typical, ccy), "latest": _money(last["amount"], ccy),
            "amount_is_steady": all(abs(a - typical) <= typical * Decimal("0.02") for a in amounts),
            "first_date": charges[0]["date"].isoformat(), "last_date": last["date"].isoformat(),
            "next_expected": due.isoformat(), "days_late": max(late, 0), "status": status,
            "per_month": None if monthly is None else _money(monthly, target),
            "price_change": (
                {"from": _money(base, ccy), "to": _money(last["amount"], ccy),
                 "percent": format(((last["amount"] - base) * 100 / base).quantize(Decimal("0.1")), "f")}
                if changed else None),
            "entries": [c["txn_id"] for c in charges[-6:]],
            "_raw_per_month": monthly,
        })
    order = {"active": 0, "overdue": 1, "lapsed": 2}
    found.sort(key=lambda s: (order[s["status"]], -(s["_raw_per_month"] or Decimal(0)), s["payee"] or ""))
    return found


def _anomalies(
    ledger: Ledger, records: list[dict[str, Any]], first: _dt.date, last: _dt.date
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    in_month = [r for r in records if first <= r["date"] <= last]
    # large expenses
    start = month_before(first, 6)
    for r in in_month:
        before = [x["amount"] for x in records if x["category"] == r["category"] and x["ccy"] == r["ccy"]
                  and start <= x["date"] < first]
        if len(before) < 5:
            continue
        usual = statistics.median(before)
        if r["amount"] > 3 * usual and r["amount"] > max(before):
            alerts.append({
                "type": "large_expense", "date": r["date"].isoformat(), "category": r["category"], "payee": r["payee"],
                "amount": _money(r["amount"], r["ccy"]), "ccy": r["ccy"], "usual": _money(usual, r["ccy"]),
                "times_usual": format((r["amount"] / usual).quantize(Decimal("0.1")), "f"), "entries": [r["txn_id"]],
            })
    # category spikes
    months = [month_before(first, back) for back in (1, 2, 3)]
    history = []
    for m in months:
        _, end = parse_month(f"{m:%Y-%m}")
        history.append(_collect(ledger, m, end))
    if all(h["entries"] for h in history):
        spend_by_ccy: dict[str, Decimal] = {}
        per_cat: dict[tuple[str, str], Decimal] = {}
        for r in in_month:
            spend_by_ccy[r["ccy"]] = spend_by_ccy.get(r["ccy"], Decimal(0)) + r["amount"]
            per_cat[(r["category"], r["ccy"])] = per_cat.get((r["category"], r["ccy"]), Decimal(0)) + r["amount"]
        for (category, ccy), total in sorted(per_cat.items()):
            past = [h["categories"][category].by_ccy.get(ccy, Decimal(0)) if category in h["categories"] else Decimal(0)
                    for h in history]
            average = sum(past, Decimal(0)) / 3
            if average > 0 and total >= average * Decimal("1.5") and total >= spend_by_ccy[ccy] * Decimal("0.05"):
                alerts.append({
                    "type": "category_spike", "category": category, "ccy": ccy, "amount": _money(total, ccy),
                    "average_before": _money(average, ccy),
                    "times_average": format((total / average).quantize(Decimal("0.1")), "f"),
                    "entries": [r["txn_id"] for r in in_month if r["category"] == category and r["ccy"] == ccy][:8],
                })
    # possible duplicates
    seen: set[tuple[str, str]] = set()
    paid = [r for r in in_month if r["payee"]]
    for i, a in enumerate(paid):
        for b in paid[i + 1:]:
            if (b["date"] - a["date"]).days > 2:
                break
            if (normalise(a["payee"]) == normalise(b["payee"]) and a["ccy"] == b["ccy"] and a["amount"] == b["amount"]
                    and a["txn_id"] != b["txn_id"] and (a["txn_id"], b["txn_id"]) not in seen):
                seen.add((a["txn_id"], b["txn_id"]))
                alerts.append({
                    "type": "possible_duplicate", "payee": a["payee"], "amount": _money(a["amount"], a["ccy"]),
                    "ccy": a["ccy"], "dates": [a["date"].isoformat(), b["date"].isoformat()],
                    "days_apart": (b["date"] - a["date"]).days, "entries": [a["txn_id"], b["txn_id"]],
                })
    return alerts


def summarise(a: dict[str, Any]) -> str:
    kind = a["type"]
    if kind == "price_change":
        return f"{a['payee']} now charges {a['to']} {a['ccy']}, was {a['from']} ({a['percent']}%), on {a['date']}."
    if kind == "overdue":
        return (f"{a['payee']} ({a['cadence']}) was expected around {a['expected']} and has not been charged; "
                f"last charge {a['last_date']}.")
    if kind == "large_expense":
        return (f"{a['amount']} {a['ccy']} on {a['category']} on {a['date']} is {a['times_usual']} times the usual "
                f"{a['usual']}, and the largest in that category in six months.")
    if kind == "category_spike":
        return (f"{a['category']} came to {a['amount']} {a['ccy']} this month, {a['times_average']} times its average "
                f"of {a['average_before']} over the three months before.")
    return (f"{a['payee']} charged {a['amount']} {a['ccy']} twice, {a['days_apart']} day(s) apart "
            f"({a['dates'][0]}, {a['dates'][1]}): check it was not charged twice.")


def find_alerts(
    ledger: Ledger, month: str | None = None, *, target: str | None = None, today: _dt.date | None = None
) -> dict[str, Any]:
    ledger.reload()
    today = today or _dt.date.today()
    first, last = parse_month(month or f"{today.year:04d}-{today.month:02d}")
    if first > today:
        raise LedgerError(f"{first:%Y-%m} has not started yet", code="usage_error")
    as_of = min(last, today)
    target = target or ledger.base_currency
    records = _spend_records(ledger, as_of)
    subscriptions = _subscriptions(ledger, records, as_of, target)
    alerts = _anomalies(ledger, records, first, as_of)
    for s in subscriptions:
        if s["price_change"] and first.isoformat() <= s["last_date"] <= last.isoformat():
            alerts.append({
                "type": "price_change", "payee": s["payee"], "ccy": s["ccy"], "date": s["last_date"],
                **s["price_change"], "cadence": s["cadence"], "entries": s["entries"][-2:],
            })
        if s["status"] == "overdue":
            alerts.append({
                "type": "overdue", "payee": s["payee"], "ccy": s["ccy"], "last_date": s["last_date"],
                "expected": s["next_expected"], "days_late": s["days_late"], "cadence": s["cadence"],
                "entries": s["entries"][-1:],
            })
    order = {"price_change": 0, "possible_duplicate": 1, "large_expense": 2, "category_spike": 3, "overdue": 4}
    for a in alerts:
        a["summary"] = summarise(a)
    alerts.sort(key=lambda a: (order[a["type"]], a.get("date") or a.get("last_date") or "", a.get("payee") or a.get("category") or ""))

    active = [s for s in subscriptions if s["status"] in ("active", "overdue")]
    missing = sorted({s["ccy"] for s in active if s["per_month"] is None})
    monthly_total = sum((s["_raw_per_month"] for s in active if s["_raw_per_month"] is not None), Decimal(0))
    for s in subscriptions:
        del s["_raw_per_month"]

    notes = []
    months_of_history = len({r["date"].strftime("%Y-%m") for r in records})
    if months_of_history < 3:
        notes.append("Less than three months of spending is recorded, so recurring charges and spikes cannot be recognised yet.")
    if not any(r["payee"] for r in records):
        notes.append("No entry has a payee, and recurring charges are recognised by payee. Imported statements usually carry one.")
    if missing:
        notes.append(f"No exchange rate for {', '.join(missing)}: left out of the monthly total. Run `money-mom rates update`.")
    if any(s["status"] == "overdue" for s in subscriptions):
        notes.append("An overdue charge may be cancelled, paid another way, or not recorded yet; the program cannot tell which.")
    return {
        "month": f"{first:%Y-%m}", "as_of": as_of.isoformat(), "in": target,
        "alerts": alerts, "subscriptions": subscriptions,
        "subscription_total": {
            "per_month": _money(monthly_total, target), "partial": bool(missing), "missing": missing,
            "count": len(active), "varying": sum(1 for s in active if not s["amount_is_steady"]),
        },
        "rules": RULES, "notes": notes,
    }
