"""Investing: buy, sell, dividends, what is held, and what was gained.

A purchase creates a *lot* (units, the price paid, the day). A sale names the lots it takes from, first in first
out unless told otherwise, so the cost basis of every sale is written into the entry and the realised gain is a
posting of that entry, not a figure computed later. Fees and withholding tax are expenses, not part of the cost
basis, and are shown separately.

Nothing here moves money or talks to a broker. It records what the user says happened.
Not covered: short selling, options and futures, stock splits and spin-offs (they change the cost of every earlier
lot and are never guessed), average-cost accounting.
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import LedgerError
from .events import ROOTS, check_currency
from .intents import resolve_account
from .inventory import METHODS, Lot, choose_lots, open_lots, replay
from .ledger import Ledger
from .rates import find_rate, round_money

NAMES = {
    "cn": {"gains": "Income:投资:已实现盈亏", "dividends": "Income:投资:股息", "interest": "Income:投资:利息",
           "fees": "Expenses:投资:手续费", "tax": "Expenses:投资:税"},
    "en": {"gains": "Income:Investing:RealizedGains", "dividends": "Income:Investing:Dividends",
           "interest": "Income:Investing:Interest", "fees": "Expenses:Investing:Fees", "tax": "Expenses:Investing:Tax"},
}
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _fmt(value: Decimal) -> str:
    return format(value, "f")


def number(text: Any, what: str, *, positive: bool = True) -> Decimal:
    """A plain number from text such as 150, 1,234.50 or 0.5. Never a float."""
    raw = str(text).strip()
    if not _NUMBER.fullmatch(raw):
        raise LedgerError(f"{what} must be a plain number, got {text!r}", code="usage_error")
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        raise LedgerError(f"{what} must be a plain number, got {text!r}", code="usage_error") from None
    if positive and value <= 0:
        raise LedgerError(f"{what} must be greater than zero", code="usage_error")
    return value


def symbol_of(text: str) -> str:
    symbol = text.strip().upper()
    check_currency(symbol, "symbol")
    return symbol


def investing_accounts(ledger: Ledger) -> dict[str, str]:
    """The ledger's accounts for gains, dividends, interest, fees and tax, in the language the ledger uses."""
    for names in NAMES.values():
        if names["gains"] in ledger.state.accounts:
            return names
    raise LedgerError(
        "this ledger has no investing accounts yet; create them with `money-mom invest init`",
        code="no_investment_accounts",
    )


def setup_investing(ledger: Ledger, date: _dt.date, actor: tuple[str, str]) -> dict[str, Any]:
    ledger.reload()
    language = "cn" if "Equity:期初余额" in ledger.state.accounts or any(
        any("一" <= c <= "鿿" for c in a) for a in ledger.state.accounts) else "en"
    wanted = list(NAMES[language].values())
    missing = [a for a in wanted if a not in ledger.state.accounts]
    if missing:
        ledger.append_many(
            [ledger.new_event("open", actor=actor, account=a, date=date.isoformat()) for a in missing], clamp_ts=True
        )
    return {"language": language, "opened": missing, "already_open": [a for a in wanted if a not in missing]}


def _account(ledger: Ledger, term: str | None, roots: tuple[str, ...], when: _dt.date, what: str) -> str:
    res = resolve_account(ledger.state, ledger.aliases(), term, roots, when)
    if res.ok:
        return res.account  # type: ignore[return-value]
    raise LedgerError(
        f"{what}: {res.message or res.reason}", code="unresolved_account",
        details={"slot": what, "term": term, "candidates": res.candidates},
    )


def _opening_equity(ledger: Ledger, when: _dt.date) -> str:
    res = resolve_account(ledger.state, ledger.aliases(), "opening", ("Equity",), when)
    if res.ok:
        return res.account  # type: ignore[return-value]
    for name in ("Equity:期初余额", "Equity:Opening-Balances"):
        if name in ledger.state.accounts:
            return name
    raise LedgerError("there is no opening-balances equity account; open one such as Equity:期初余额", code="unresolved_account")


def _write(ledger: Ledger, *, actor, confidence, source, meta, date, postings, narration, payee=None, import_hash=None):
    raw = ledger.new_event(
        "txn", actor=actor, confidence=confidence, source=source, meta=meta, date=date.isoformat(), status="posted",
        postings=postings, narration=narration, payee=payee, import_hash=import_hash,
    )
    return ledger.append(raw, clamp_ts=True)


def plan_buy(
    ledger: Ledger, *, units: Decimal, symbol: str, price: Decimal, ccy: str, account: str, cash: str, date: _dt.date,
    fee: Decimal = Decimal(0), opening: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The postings and trade note for a purchase, without writing anything."""
    names = None if opening else investing_accounts(ledger) if fee else None
    cost = {"per_unit": _fmt(price), "ccy": ccy, "date": date.isoformat()}
    held = {"account": account, "amount": _fmt(units), "ccy": symbol, "cost": cost}
    if opening:
        return [held, {"account": _opening_equity(ledger, date), "amount": _fmt(-(units * price)), "ccy": ccy}], {
            "kind": "opening", "symbol": symbol, "units": _fmt(units), "price": _fmt(price), "ccy": ccy,
        }
    postings = [held, {"account": cash, "amount": _fmt(-(units * price + fee)), "ccy": ccy}]
    if fee:
        postings.append({"account": names["fees"], "amount": _fmt(fee), "ccy": ccy})  # type: ignore[index]
    return postings, {"kind": "buy", "symbol": symbol, "units": _fmt(units), "price": _fmt(price), "ccy": ccy, "fee": _fmt(fee)}


def record_buy(
    ledger: Ledger, *, units: Any, symbol: str, price: Any, ccy: str, account: str, cash: str | None = None,
    date: _dt.date, fee: Any = 0, opening: bool = False, actor: tuple[str, str] = ("human", "user"),
    confidence: float | None = None, source: dict | None = None, meta: dict | None = None,
    import_hash: str | None = None, narration: str | None = None,
) -> dict[str, Any]:
    ledger.reload()
    sym, qty, px = symbol_of(symbol), number(units, "units"), number(price, "price")
    check_currency(ccy, "ccy")
    fee_amount = number(fee, "fee", positive=False)
    holding = _account(ledger, account, ("Assets",), date, "account")
    cash_account = holding if cash is None else _account(ledger, cash, ("Assets", "Liabilities"), date, "cash")
    postings, note = plan_buy(
        ledger, units=qty, symbol=sym, price=px, ccy=ccy, account=holding, cash=cash_account, date=date,
        fee=fee_amount, opening=opening,
    )
    event = _write(
        ledger, actor=actor, confidence=confidence, source=source, meta={**(meta or {}), "trade": note}, date=date,
        postings=postings, narration=narration or f"{'opening position' if opening else 'buy'} {_fmt(qty)} {sym}",
        import_hash=import_hash,
    )
    return {"id": event.id, "kind": note["kind"], "date": date.isoformat(), "symbol": sym, "units": _fmt(qty),
            "price": _fmt(px), "ccy": ccy, "cost": _fmt(qty * px), "fee": _fmt(fee_amount), "account": holding,
            "postings": postings}


def plan_sell(
    ledger: Ledger, *, units: Decimal, symbol: str, price: Decimal, ccy: str, account: str, cash: str, date: _dt.date,
    fee: Decimal, method: str, lots: dict | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    names = investing_accounts(ledger)
    pool = open_lots(lots if lots is not None else ledger.state.lots(date))
    try:
        taken = choose_lots(pool, account, symbol, units, method)
    except ValueError as err:
        held = sum((l.units for l in pool if l.account == account and l.symbol == symbol), Decimal(0))
        raise LedgerError(str(err), code="not_enough_shares", details={"held": _fmt(held), "wanted": _fmt(units)}) from None
    postings: list[dict[str, Any]] = []
    cost_total = Decimal(0)
    for lot, use in taken:
        if lot.cost_ccy != ccy:
            raise LedgerError(
                f"these {symbol} were bought in {lot.cost_ccy} but the sale is in {ccy}", code="currency_mismatch"
            )
        cost_total += use * lot.per_unit
        postings.append({"account": account, "amount": _fmt(-use), "ccy": symbol,
                         "cost": {"per_unit": _fmt(lot.per_unit), "ccy": lot.cost_ccy, "date": lot.date.isoformat()}})
    proceeds = units * price
    postings.append({"account": cash, "amount": _fmt(proceeds - fee), "ccy": ccy})
    if fee:
        postings.append({"account": names["fees"], "amount": _fmt(fee), "ccy": ccy})
    postings.append({"account": names["gains"], "amount": _fmt(-(proceeds - cost_total)), "ccy": ccy})
    note = {"kind": "sell", "symbol": symbol, "units": _fmt(units), "price": _fmt(price), "ccy": ccy, "fee": _fmt(fee),
            "method": method}
    return postings, note


def record_sell(
    ledger: Ledger, *, units: Any, symbol: str, price: Any, ccy: str, account: str, cash: str | None = None,
    date: _dt.date, fee: Any = 0, method: str = "fifo", actor: tuple[str, str] = ("human", "user"),
    confidence: float | None = None, source: dict | None = None, meta: dict | None = None,
    import_hash: str | None = None, narration: str | None = None,
) -> dict[str, Any]:
    ledger.reload()
    if method not in METHODS:
        raise LedgerError(f"--lots must be one of {', '.join(METHODS)}", code="usage_error")
    sym, qty, px = symbol_of(symbol), number(units, "units"), number(price, "price")
    check_currency(ccy, "ccy")
    fee_amount = number(fee, "fee", positive=False)
    holding = _account(ledger, account, ("Assets",), date, "account")
    cash_account = holding if cash is None else _account(ledger, cash, ("Assets", "Liabilities"), date, "cash")
    postings, note = plan_sell(
        ledger, units=qty, symbol=sym, price=px, ccy=ccy, account=holding, cash=cash_account, date=date,
        fee=fee_amount, method=method,
    )
    event = _write(
        ledger, actor=actor, confidence=confidence, source=source, meta={**(meta or {}), "trade": note}, date=date,
        postings=postings, narration=narration or f"sell {_fmt(qty)} {sym}", import_hash=import_hash,
    )
    return {"id": event.id, **_sale_summary(postings, note, date), "account": holding, "postings": postings}


def _sale_summary(postings: list[dict[str, Any]], note: dict[str, Any], date: _dt.date) -> dict[str, Any]:
    units, price = Decimal(note["units"]), Decimal(note["price"])
    cost = sum((-Decimal(p["amount"]) * Decimal(p["cost"]["per_unit"]) for p in postings if p.get("cost")), Decimal(0))
    return {
        "kind": "sell", "date": date.isoformat(), "symbol": note["symbol"], "units": note["units"], "price": note["price"],
        "ccy": note["ccy"], "proceeds": _fmt(units * price), "cost_basis": _fmt(cost), "realized_gain": _fmt(units * price - cost),
        "fee": note["fee"], "method": note["method"],
        "lots": [{"units": _fmt(-Decimal(p["amount"])), "per_unit": p["cost"]["per_unit"], "bought": p["cost"]["date"]}
                 for p in postings if p.get("cost")],
    }


def record_dividend(
    ledger: Ledger, *, amount: Any, symbol: str, ccy: str, to: str, date: _dt.date, tax: Any = 0,
    actor: tuple[str, str] = ("human", "user"), confidence: float | None = None, source: dict | None = None,
    meta: dict | None = None, import_hash: str | None = None, narration: str | None = None,
) -> dict[str, Any]:
    ledger.reload()
    sym, gross = symbol_of(symbol), number(amount, "amount")
    check_currency(ccy, "ccy")
    withheld = number(tax, "tax", positive=False)
    if withheld >= gross:
        raise LedgerError("the tax withheld must be less than the dividend", code="usage_error")
    names = investing_accounts(ledger)
    account = _account(ledger, to, ("Assets", "Liabilities"), date, "to")
    postings = [{"account": account, "amount": _fmt(gross - withheld), "ccy": ccy}]
    if withheld:
        postings.append({"account": names["tax"], "amount": _fmt(withheld), "ccy": ccy})
    postings.append({"account": names["dividends"], "amount": _fmt(-gross), "ccy": ccy})
    note = {"kind": "dividend", "symbol": sym, "ccy": ccy, "gross": _fmt(gross), "tax": _fmt(withheld)}
    event = _write(
        ledger, actor=actor, confidence=confidence, source=source, meta={**(meta or {}), "trade": note}, date=date,
        postings=postings, narration=narration or f"dividend {sym}", import_hash=import_hash,
    )
    return {"id": event.id, "kind": "dividend", "date": date.isoformat(), "symbol": sym, "ccy": ccy,
            "gross": _fmt(gross), "tax": _fmt(withheld), "net": _fmt(gross - withheld), "account": account}


# ---------------------------------------------------------------- reading it back


def _price_for(ledger: Ledger, symbol: str, on: _dt.date, marks: dict[str, tuple[Decimal, str]]):
    """(price, currency, date, where it came from) for a symbol: a mark the user gave, a stored price, or the last
    price it traded at. Never looks past `on`."""
    if symbol in marks:
        price, ccy = marks[symbol]
        return price, ccy, on, "mark"
    best = None
    for (base, quote), by_date in ledger.state.effective_prices().items():
        if base != symbol:
            continue
        days = [d for d in by_date if d <= on]
        if days and (best is None or max(days) > best[0]):
            best = (max(days), quote, by_date[max(days)].event.rate)
    if best is not None:
        return best[2], best[1], best[0], "stored price"
    last = None
    for rec in ledger.state.txns.values():
        note = rec.event.meta.get("trade")
        if rec.status == "posted" and isinstance(note, dict) and note.get("symbol") == symbol and note.get("price") \
                and rec.date <= on and (last is None or rec.date >= last[0]):
            last = (rec.date, Decimal(note["price"]), note["ccy"])
    if last is not None:
        return last[1], last[2], last[0], "last trade"
    return None


def parse_marks(items: list[str] | None) -> dict[str, tuple[Decimal, str]]:
    out: dict[str, tuple[Decimal, str]] = {}
    for item in items or []:
        match = re.fullmatch(r"([A-Za-z0-9._-]+)=([\d.,]+)(?::([A-Za-z]{3}))?", item.strip())
        if not match:
            raise LedgerError(f"--mark must look like AAPL=190.5 or AAPL=190.5:USD, got {item!r}", code="usage_error")
        out[match.group(1).upper()] = (number(match.group(2), "mark"), (match.group(3) or "").upper())
    return out


def holdings(
    ledger: Ledger, *, as_of: _dt.date | None = None, target: str | None = None, account: str | None = None,
    show_lots: bool = False, marks: dict[str, tuple[Decimal, str]] | None = None,
) -> dict[str, Any]:
    """What is held on a day: units, cost, a price and its source, market value, and the unrealised gain. A
    position with no price is listed with `null` values and marks the totals partial, never valued at zero."""
    ledger.reload()
    on = as_of or _dt.date.today()
    target = target or ledger.base_currency
    marks = marks or {}
    lots = [l for l in open_lots(ledger.state.lots(on)) if account is None or l.account == account or l.account.startswith(account + ":")]
    positions: dict[tuple[str, str], dict[str, Any]] = {}
    for lot in lots:
        pos = positions.setdefault((lot.account, lot.symbol), {"account": lot.account, "symbol": lot.symbol, "units": Decimal(0), "cost": {}, "lots": []})
        pos["units"] += lot.units
        pos["cost"][lot.cost_ccy] = pos["cost"].get(lot.cost_ccy, Decimal(0)) + lot.cost
        pos["lots"].append(lot)
    rows, missing, converted = [], [], {"cost": Decimal(0), "value": Decimal(0)}
    per_ccy: dict[str, dict[str, Decimal]] = {}
    for pos in positions.values():
        unit_total = pos["units"]
        cost = pos["cost"]
        priced = _price_for(ledger, pos["symbol"], on, marks)
        row: dict[str, Any] = {
            "account": pos["account"], "symbol": pos["symbol"], "units": _fmt(unit_total),
            "cost": {c: _fmt(v) for c, v in cost.items()},
            "average_cost": {c: _fmt(v / unit_total) for c, v in cost.items()},
            "price": None, "price_ccy": None, "price_date": None, "price_source": None, "stale": None,
            "market_value": None, "unrealized_gain": None, "unrealized_pct": None,
        }
        if priced is not None:
            price, price_ccy, price_day, how = priced
            price_ccy = price_ccy or next(iter(cost))
            value = unit_total * price
            row.update(price=_fmt(price), price_ccy=price_ccy, price_date=price_day.isoformat(), price_source=how,
                       stale=(on - price_day).days > 7, market_value=_fmt(value))
            if len(cost) == 1:
                cost_ccy, cost_amount = next(iter(cost.items()))
                comparable = value if price_ccy == cost_ccy else None
                if comparable is None:
                    info = find_rate(ledger.state, price_ccy, cost_ccy, on, pivot=ledger.base_currency)
                    comparable = value * info.rate if info else None
                if comparable is not None:
                    gain = comparable - cost_amount
                    row["unrealized_gain"] = {cost_ccy: _fmt(gain)}
                    row["unrealized_pct"] = _fmt((gain / cost_amount * 100).quantize(Decimal("0.01"))) if cost_amount else None
                    bucket = per_ccy.setdefault(cost_ccy, {"cost": Decimal(0), "value": Decimal(0), "unrealized": Decimal(0)})
                    bucket["cost"] += cost_amount
                    bucket["value"] += comparable
                    bucket["unrealized"] += gain
            # converted totals into the target currency
            cost_in, value_in = Decimal(0), None
            ok = True
            for c, amount in cost.items():
                info = find_rate(ledger.state, c, target, on, pivot=ledger.base_currency)
                if info is None:
                    ok = False
                else:
                    cost_in += amount * info.rate
            vinfo = find_rate(ledger.state, price_ccy, target, on, pivot=ledger.base_currency)
            if vinfo is not None:
                value_in = value * vinfo.rate
            if ok and value_in is not None:
                converted["cost"] += cost_in
                converted["value"] += value_in
            else:
                missing.append(pos["symbol"])
        else:
            missing.append(pos["symbol"])
        if show_lots:
            row["lots"] = [{"units": _fmt(l.units), "per_unit": _fmt(l.per_unit), "ccy": l.cost_ccy,
                            "bought": l.date.isoformat(), "days_held": (on - l.date).days} for l in pos["lots"]]
        rows.append(row)
    rows.sort(key=lambda r: (r["account"], r["symbol"]))
    return {
        "as_of": on.isoformat(), "in": target, "positions": rows,
        "totals_by_currency": {c: {k: _fmt(v) for k, v in b.items()} for c, b in sorted(per_ccy.items())},
        "converted": {
            "cost": _fmt(round_money(converted["cost"], target)), "market_value": _fmt(round_money(converted["value"], target)),
            "unrealized_gain": _fmt(round_money(converted["value"] - converted["cost"], target)),
            "partial": bool(missing), "missing": sorted(set(missing)),
        },
        "notes": _holding_notes(rows, missing),
    }


def _holding_notes(rows: list[dict[str, Any]], missing: list[str]) -> list[str]:
    notes = []
    if missing:
        notes.append(
            f"No price for {', '.join(sorted(set(missing)))}: they are left out of the converted totals. Record one with "
            "`money-mom rates set SYMBOL CCY PRICE --source TEXT`, or pass `--mark SYMBOL=PRICE`."
        )
    if any(r["price_source"] == "last trade" for r in rows):
        notes.append("Where the source is `last trade`, the price is what the last purchase or sale was done at, not a market quote.")
    if any(r["stale"] for r in rows):
        notes.append("Some prices are more than 7 days older than the day asked about.")
    return notes


def realized(
    ledger: Ledger, *, since: _dt.date | None = None, until: _dt.date | None = None, symbol: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Realised gains of every sale, with the lots each one took from, plus dividends and fees. Fees and tax are
    reported next to the gain, not inside it."""
    ledger.reload()
    target = target or ledger.base_currency
    sales, dividends = [], []
    for rec in ledger.state.txns.values():
        note = rec.event.meta.get("trade")
        if rec.status != "posted" or not isinstance(note, dict):
            continue
        if (since and rec.date < since) or (until and rec.date > until) or (symbol and note.get("symbol") != symbol.upper()):
            continue
        if note.get("kind") == "sell":
            lots = [p for p in rec.postings if p.cost is not None]
            cost = sum((-p.amount * p.cost.per_unit for p in lots), Decimal(0))
            units, price = Decimal(note["units"]), Decimal(note["price"])
            fee = Decimal(note.get("fee", "0"))
            gain = units * price - cost
            sales.append({
                "id": rec.id, "date": rec.date.isoformat(), "symbol": note["symbol"], "units": note["units"], "price": note["price"],
                "ccy": note["ccy"], "proceeds": _fmt(units * price), "cost_basis": _fmt(cost), "realized_gain": _fmt(gain),
                "fee": _fmt(fee), "gain_after_fee": _fmt(gain - fee), "account": lots[0].account if lots else None,
                "lots": [{"units": _fmt(-p.amount), "per_unit": _fmt(p.cost.per_unit), "bought": (p.cost.date or rec.date).isoformat(),
                          "days_held": (rec.date - (p.cost.date or rec.date)).days} for p in lots],
            })
        elif note.get("kind") == "dividend":
            dividends.append({"id": rec.id, "date": rec.date.isoformat(), "symbol": note["symbol"], "ccy": note["ccy"],
                              "gross": note["gross"], "tax": note["tax"], "net": _fmt(Decimal(note["gross"]) - Decimal(note["tax"]))})
    by_ccy: dict[str, dict[str, Decimal]] = {}
    by_symbol: dict[tuple[str, str], dict[str, Decimal]] = {}
    for s in sales:
        for bucket in (by_ccy.setdefault(s["ccy"], {"realized_gain": Decimal(0), "fees": Decimal(0), "dividends": Decimal(0), "dividend_tax": Decimal(0)}),
                       by_symbol.setdefault((s["symbol"], s["ccy"]), {"realized_gain": Decimal(0), "fees": Decimal(0), "dividends": Decimal(0), "dividend_tax": Decimal(0)})):
            bucket["realized_gain"] += Decimal(s["realized_gain"])
            bucket["fees"] += Decimal(s["fee"])
    for d in dividends:
        for bucket in (by_ccy.setdefault(d["ccy"], {"realized_gain": Decimal(0), "fees": Decimal(0), "dividends": Decimal(0), "dividend_tax": Decimal(0)}),
                       by_symbol.setdefault((d["symbol"], d["ccy"]), {"realized_gain": Decimal(0), "fees": Decimal(0), "dividends": Decimal(0), "dividend_tax": Decimal(0)})):
            bucket["dividends"] += Decimal(d["gross"])
            bucket["dividend_tax"] += Decimal(d["tax"])
    on = until or _dt.date.today()
    total, missing = Decimal(0), []
    for ccy, bucket in by_ccy.items():
        info = find_rate(ledger.state, ccy, target, on, pivot=ledger.base_currency)
        if info is None:
            missing.append(ccy)
        else:
            total += bucket["realized_gain"] * info.rate
    return {
        "since": since.isoformat() if since else None, "until": until.isoformat() if until else None, "in": target,
        "sales": sales, "dividends": dividends,
        "by_currency": {c: {k: _fmt(v) for k, v in b.items()} for c, b in sorted(by_ccy.items())},
        "by_symbol": [{"symbol": s, "ccy": c, **{k: _fmt(v) for k, v in b.items()}} for (s, c), b in sorted(by_symbol.items())],
        "realized_gain_converted": _fmt(round_money(total, target)), "partial": bool(missing), "missing": missing,
        "notes": ["Fees and tax are shown next to the gain; the gain itself is proceeds minus the cost of the lots sold."],
    }
