"""Exchange rates: stored in the ledger as `price` events, looked up offline, fetched only on request.

The program never goes to the network by itself. `money-mom rates update` is the one place that does,
and what it sends is only a pair of currency codes and a date, never an amount or an account.
Each rate is stored with where it came from, so every converted figure can be traced back.

The default source is Frankfurter, which republishes the European Central Bank's daily reference rates:
one rate per business day (about 30 currencies, no TWD). It is not a live market feed.
"""

from __future__ import annotations

import datetime as _dt
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from . import __version__
from .currency import parse_currency
from .errors import LedgerError
from .events import check_currency, parse_amount
from .ledger import Ledger
from .state import LedgerState

FRANKFURTER_URL = "https://api.frankfurter.dev/v1"
FRANKFURTER_REF = "api.frankfurter.dev (ECB reference rate)"
OPEN_ER_URL = "https://open.er-api.com/v6/latest"
OPEN_ER_REF = "open.er-api.com (rates by exchangerate-api.com, open access)"
STALE_AFTER_DAYS = 7
_ZERO_DECIMALS = frozenset({"JPY", "KRW", "VND", "ISK", "CLP"})


def decimals_for(ccy: str) -> int:
    return 0 if ccy in _ZERO_DECIMALS else 2


def round_money(amount: Decimal, ccy: str) -> Decimal:
    return amount.quantize(Decimal(1).scaleb(-decimals_for(ccy)), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class RateInfo:
    rate: Decimal  # one unit of the base currency in units of the quote currency
    rate_date: _dt.date  # the day the rate is for (the older one when two rates were combined)
    via: str  # identity | direct | inverse | via <pivot>
    stale: bool
    age_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate": format(self.rate, "f"), "rate_date": self.rate_date.isoformat(),
            "via": self.via, "stale": self.stale, "age_days": self.age_days,
        }


def _one_way(
    table: dict[tuple[str, str], dict[_dt.date, Any]], base: str, quote: str, on: _dt.date
) -> tuple[Decimal, _dt.date, str] | None:
    """The latest rate on or before `on` for base->quote, or the inverse of quote->base. Never looks ahead."""
    for pair, invert in (((base, quote), False), ((quote, base), True)):
        dated = [d for d in table.get(pair, {}) if d <= on]
        if dated:
            day = max(dated)
            rate = table[pair][day].event.rate
            return (Decimal(1) / rate if invert else rate), day, "inverse" if invert else "direct"
    return None


def find_rate(
    state: LedgerState, base: str, quote: str, on: _dt.date, *, pivot: str | None = None, _depth: int = 0
) -> RateInfo | None:
    """How many `quote` one `base` was worth on `on`, from stored rates only. None if there is no rate.

    A held commodity (a share) is priced in some currency with a stored price (`rates set AAPL USD 190 ...`);
    asking for it in another currency goes through that one."""
    if base == quote:
        return RateInfo(Decimal(1), on, "identity", False, 0)
    table = state.effective_prices()
    found = _one_way(table, base, quote, on)
    if found is not None:
        rate, day, how = found
        age = (on - day).days
        return RateInfo(rate, day, how, age > STALE_AFTER_DAYS, age)
    if pivot and pivot not in (base, quote):
        first, second = _one_way(table, base, pivot, on), _one_way(table, pivot, quote, on)
        if first and second:
            day = min(first[1], second[1])
            age = (on - day).days
            return RateInfo(first[0] * second[0], day, f"via {pivot}", age > STALE_AFTER_DAYS, age)
    if _depth == 0 and base in state.commodities:
        for priced_in in sorted({q for (b, q) in table if b == base and q not in (base, quote)}):
            first = _one_way(table, base, priced_in, on)
            second = find_rate(state, priced_in, quote, on, pivot=pivot, _depth=1)
            if first and second:
                day = min(first[1], second.rate_date)
                age = (on - day).days
                return RateInfo(first[0] * second.rate, day, f"via {priced_in}", age > STALE_AFTER_DAYS, age)
    return None


def convert(
    state: LedgerState, amount: Decimal, src: str, dst: str, on: _dt.date, *, pivot: str | None = None
) -> tuple[Decimal, RateInfo]:
    """`amount` of `src` expressed in `dst`, unrounded. Raises `no_rate` rather than guessing."""
    info = find_rate(state, src, dst, on, pivot=pivot)
    if info is None:
        raise LedgerError(
            f"no exchange rate from {src} to {dst} on or before {on}; "
            f"run `money-mom rates update`, or record one with `money-mom rates set {src} {dst} RATE --source TEXT`",
            code="no_rate", details={"base": src, "quote": dst, "date": on.isoformat()},
        )
    return amount * info.rate, info


@dataclass(frozen=True)
class Fetched:
    base: str
    quote: str
    date: _dt.date
    rate: str  # exact decimal text as the source published it
    source: str = FRANKFURTER_REF


class RateUnsupported(Exception):
    """The source has no rate for this pair (it covers about 30 currencies)."""


Opener = Callable[..., Any]


def fetch_frankfurter(
    base: str, quote: str, on: _dt.date | None = None, *, opener: Opener = urllib.request.urlopen, timeout: float = 10
) -> Fetched:
    """One rate from Frankfurter. For a weekend or holiday the source answers with the previous business day."""
    check_currency(base)
    check_currency(quote)
    day = "latest" if on is None else on.isoformat()
    request = urllib.request.Request(
        f"{FRANKFURTER_URL}/{day}?base={base}&symbols={quote}",
        headers={"User-Agent": f"money-mom/{__version__}", "Accept": "application/json"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            body = response.read(65536)
    except urllib.error.HTTPError as err:
        if err.code in (404, 422):
            raise RateUnsupported(f"{base}->{quote}") from None
        raise LedgerError(f"the rate source answered HTTP {err.code}", code="rates_unavailable") from None
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise LedgerError(f"could not reach the rate source: {reason}", code="rates_unavailable") from None
    try:
        data = json.loads(body, parse_float=str, parse_int=str)
        rate = data["rates"][quote]
        got_base, got_date = data["base"], data["date"]
        if got_base != base or not isinstance(rate, str):
            raise ValueError("unexpected answer")
        parsed_date = _dt.date.fromisoformat(got_date)
        if parse_amount(rate, "rate") <= 0:
            raise ValueError("rate must be positive")
    except (ValueError, KeyError, TypeError, LedgerError):
        raise LedgerError("the rate source sent an answer that could not be understood", code="rates_unavailable") from None
    return Fetched(base, quote, parsed_date, rate)


def fetch_open_er(
    base: str, quote: str, on: _dt.date | None = None, *, opener: Opener = urllib.request.urlopen, timeout: float = 10
) -> Fetched:
    """The latest rate from the open-access ExchangeRate-API, used only for currencies the ECB does not publish
    (TWD, for one). It has no history, so a request for an earlier day is reported as unsupported rather than
    answered with today's rate."""
    check_currency(base)
    check_currency(quote)
    if on is not None and on < _dt.date.today():
        raise RateUnsupported(f"{base}->{quote}: the fallback source has no history")
    request = urllib.request.Request(
        f"{OPEN_ER_URL}/{base}", headers={"User-Agent": f"money-mom/{__version__}", "Accept": "application/json"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            body = response.read(1_000_000)
    except urllib.error.HTTPError as err:
        if err.code in (404, 422):
            raise RateUnsupported(f"{base}->{quote}") from None
        raise LedgerError(f"the fallback rate source answered HTTP {err.code}", code="rates_unavailable") from None
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise LedgerError(f"could not reach the fallback rate source: {reason}", code="rates_unavailable") from None
    try:
        data = json.loads(body, parse_float=str, parse_int=str)
        if data.get("result") != "success" or data.get("base_code") != base:
            raise RateUnsupported(f"{base}->{quote}")
        rate = data["rates"].get(quote)
        if rate is None:
            raise RateUnsupported(f"{base}->{quote}")
        from email.utils import parsedate_to_datetime
        published = parsedate_to_datetime(data["time_last_update_utc"]).date()
        if not isinstance(rate, str) or parse_amount(rate, "rate") <= 0:
            raise ValueError("rate must be positive")
    except RateUnsupported:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, LedgerError):
        raise LedgerError("the fallback rate source sent an answer that could not be understood", code="rates_unavailable") from None
    return Fetched(base, quote, published, rate, OPEN_ER_REF)


Fetcher = Callable[[str, str, "_dt.date | None"], Fetched]


def update_rates(
    ledger: Ledger,
    *,
    on: _dt.date | None = None,
    currencies: list[str] | None = None,
    fetch: Fetcher = fetch_frankfurter,
    fallback: Fetcher | None = None,
    actor: tuple[str, str] = ("human", "user"),
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch each foreign currency's rate into the base currency and record what is new.

    Network trouble for any currency stops everything before anything is written. A currency the source
    does not cover is reported as `unsupported` and does not stop the others.
    """
    ledger.reload()
    base = ledger.base_currency
    wanted = sorted(ledger.state.currencies_in_use())
    if currencies:
        picked: set[str] = set()
        for text in currencies:
            named = parse_currency(text)
            if len(named) != 1:
                raise LedgerError(f"{text!r} is not one currency; use a code such as USD", code="usage_error")
            picked.add(named[0])
        wanted = sorted(picked)
    targets = [c for c in wanted if c != base]
    fetched: list[Fetched] = []
    unsupported: list[str] = []
    for ccy in targets:
        try:
            fetched.append(fetch(ccy, base, on))
        except RateUnsupported:
            if fallback is None:
                unsupported.append(ccy)
                continue
            try:  # only a currency the primary source does not publish is asked of the second one
                fetched.append(fallback(ccy, base, on))
            except RateUnsupported:
                unsupported.append(ccy)
    table = ledger.state.effective_prices()
    recorded, unchanged, events = [], [], []
    for item in fetched:
        existing = table.get((item.base, item.quote), {}).get(item.date)
        entry = {"base": item.base, "quote": item.quote, "date": item.date.isoformat(), "rate": item.rate, "source": item.source}
        if existing is not None and existing.event.rate == parse_amount(item.rate):
            unchanged.append(entry)
            continue
        recorded.append(entry)
        events.append(
            ledger.new_event(
                "price", actor=actor, date=item.date.isoformat(), base=item.base, quote=item.quote,
                rate=item.rate, source={"type": "import", "ref": item.source},
            )
        )
    if events and not dry_run:
        ledger.append_many(events, clamp_ts=True)
    return {
        "base_currency": base, "source": FRANKFURTER_REF, "sources": sorted({i.source for i in fetched}),
        "written": bool(events) and not dry_run,
        "recorded": recorded, "unchanged": unchanged, "unsupported": unsupported,
        "checked": targets,
    }


def convert_balances(
    state: LedgerState,
    balances: dict[tuple[str, str], Decimal],
    target: str,
    on: _dt.date,
    *,
    pivot: str | None,
) -> dict[str, Any]:
    """Convert (account, currency) balances into `target`. Currencies with no rate are listed, never dropped."""
    rows, missing, total = [], [], Decimal(0)
    for (account, ccy), amount in sorted(balances.items()):
        row: dict[str, Any] = {"account": account, "ccy": ccy, "amount": format(amount, "f")}
        info = find_rate(state, ccy, target, on, pivot=pivot)
        if info is None:
            row.update(converted=None, rate=None, rate_date=None, via=None, stale=None)
            if ccy not in missing:
                missing.append(ccy)
        else:
            value = amount * info.rate
            total += value
            row.update(
                converted=format(round_money(value, target), "f"), rate=format(info.rate, "f"),
                rate_date=info.rate_date.isoformat(), via=info.via, stale=info.stale,
            )
        rows.append(row)
    return {
        "in": target, "as_of": on.isoformat(), "balances": rows,
        "total": format(round_money(total, target), "f"), "partial": bool(missing), "missing": missing,
        "stale": sorted({r["ccy"] for r in rows if r["stale"]}),
    }
