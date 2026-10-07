"""Recognising currencies in what people write, and choosing one without guessing.

People write money many ways: ``5美元``, ``HK$200``, ``¥38``, ``USD 5``, ``1,200元``, plain ``38``.
This module reads such text and decides which currency it means, in this order of trust:

1. an explicit ``--ccy`` and/or a currency written in the amount (unambiguous ones win outright);
2. for an ambiguous symbol such as ``$`` or ``¥``: the currencies the accounts involved allow;
3. then, only if exactly one candidate appears in the ledger, that one, **flagged as inferred**
   so the caller can ask for confirmation instead of trusting it;
4. when nothing is said at all: what the accounts allow, else the ledger's base currency.

Anything still unclear is an error that lists the candidates. A wrong currency silently corrupts
every total, so asking is cheaper than guessing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .errors import LedgerError

ISO_CODES = frozenset(
    """AED AUD BDT BGN BRL CAD CHF CLP CNY COP CZK DKK EGP EUR GBP HKD HUF IDR ILS INR ISK JPY KRW KWD
    KZT LKR MOP MXN MYR NGN NOK NZD PHP PKR PLN QAR RON RUB SAR SEK SGD THB TRY TWD UAH USD VND ZAR""".split()
)

# Words and symbols that name exactly one currency. Keys are matched case-insensitively.
_ONE: dict[str, tuple[str, ...]] = {}
for _code, _names in {
    "USD": ["us$", "美元", "美金", "美刀", "刀", "美圆", "us dollar", "us dollars"],
    "HKD": ["hk$", "港币", "港元", "港幣", "hk dollar", "hk dollars"],
    "CNY": ["人民币", "人民幣", "rmb", "cn¥", "yuan", "renminbi"],
    "JPY": ["日元", "日圆", "日幣", "円", "jp¥", "yen"],
    "EUR": ["€", "欧元", "歐元", "euro", "euros"],
    "GBP": ["£", "英镑", "英鎊", "pound", "pounds", "sterling"],
    "AUD": ["a$", "au$", "澳元", "澳币", "澳幣"],
    "CAD": ["c$", "ca$", "加元", "加币", "加幣"],
    "SGD": ["s$", "sg$", "新加坡元", "新币", "新幣", "坡币"],
    "NZD": ["nz$", "纽元", "新西兰元"],
    "TWD": ["nt$", "新台币", "台币", "台幣", "新台幣"],
    "KRW": ["₩", "韩元", "韓元", "韩币", "won"],
    "THB": ["฿", "泰铢", "泰銖", "baht"],
    "INR": ["₹", "印度卢比"],
    "PHP": ["₱", "菲律宾比索"],
    "TRY": ["₺", "土耳其里拉"],
    "VND": ["₫", "越南盾"],
    "CHF": ["瑞士法郎", "瑞郎"],
    "BRL": ["r$", "巴西雷亚尔"],
}.items():
    for _name in _names:
        _ONE[_name.casefold()] = (_code,)
for _code in ISO_CODES:
    _ONE[_code.casefold()] = (_code,)

# Symbols that several currencies share: never resolved by guessing.
_DOLLARS = ("USD", "HKD", "CAD", "AUD", "SGD", "NZD", "MXN")
_MANY: dict[str, tuple[str, ...]] = {
    "$": _DOLLARS,
    "dollar": _DOLLARS,
    "dollars": _DOLLARS,
    "¥": ("CNY", "JPY"),
}

# A bare unit says "this is money" without naming a currency, so it asserts nothing.
_GENERIC = frozenset({"元", "块", "块钱", "圆", "蚊"})

_NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![\d])")


@dataclass(frozen=True)
class Money:
    amount: str  # plain decimal text, thousands separators removed
    candidates: tuple[str, ...]  # () nothing said, 1 unambiguous, 2+ ambiguous symbol
    symbol: str | None  # the currency text as written, if any

    @property
    def ccy(self) -> str | None:
        return self.candidates[0] if len(self.candidates) == 1 else None


def _lookup(expression: str) -> tuple[str, ...]:
    key = " ".join(expression.casefold().strip(" .").split())
    if key in _GENERIC:
        return ()
    if key in _ONE:
        return _ONE[key]
    if key in _MANY:
        return _MANY[key]
    raise LedgerError(
        f"cannot tell which currency {expression!r} is; use a 3-letter code such as USD with --ccy "
        "(tickers and other assets must be given with --ccy too)",
        code="unknown_currency", details={"text": expression},
    )


def parse_money(text: str) -> Money:
    """Split text such as ``HK$1,200.50`` or ``5美元`` into an amount and the currency it names."""
    normalised = unicodedata.normalize("NFKC", str(text)).strip()
    found = list(_NUMBER.finditer(normalised))
    if len(found) != 1:
        raise LedgerError(
            f"expected exactly one amount in {text!r}, for example 38, 38.50 or HK$200",
            code="invalid_amount",
        )
    match = found[0]
    prefix, suffix = normalised[: match.start()].strip(), normalised[match.end():].strip()
    if prefix.startswith(("-", "−")):
        raise LedgerError("an amount must be positive; the direction comes from the command", code="invalid_amount")
    if any(ch.isdigit() for ch in prefix + suffix):
        raise LedgerError(
            f"cannot read the number in {text!r}: write it like 1200 or 1,200.50 (a dot for decimals)",
            code="invalid_amount",
        )
    amount = match.group(1).replace(",", "") + (match.group(2) or "")
    named = [side for side in (prefix, suffix) if side and _lookup(side) != ()]
    if len(named) > 1:
        raise LedgerError(f"{text!r} names a currency twice", code="invalid_amount")
    for side in (prefix, suffix):
        if side:
            _lookup(side)  # rejects text that is neither a currency nor a generic unit
    if not named:
        return Money(amount, (), None)
    return Money(amount, _lookup(named[0]), named[0])


def parse_currency(text: str) -> tuple[str, ...]:
    """The currency a bare expression names (``usd``, ``美元``, ``$``). Empty for a generic unit."""
    return _lookup(unicodedata.normalize("NFKC", text))


@dataclass(frozen=True)
class CurrencyChoice:
    ccy: str
    matched_by: str  # flag | text | account | ledger | default
    inferred: bool = False
    message: str = ""


def _narrow(candidates: Iterable[str], allowed_sets: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    left = list(candidates)
    for account, allowed in allowed_sets:
        kept = [c for c in left if c in allowed]
        if not kept:
            raise LedgerError(
                f"{account} only allows {', '.join(allowed)}, which is none of {', '.join(left)}",
                code="currency_conflict", details={"account": account, "allowed": list(allowed)},
            )
        left = kept
    return left


def choose_currency(
    *,
    text: Money | None,
    flag: tuple[str, ...] | None,
    accounts: list[tuple[str, tuple[str, ...] | None]],
    used: Iterable[str],
    base: str,
) -> CurrencyChoice:
    """Pick one currency, or raise an error that says what the choices were. See the module doc."""
    allowed = [(name, ccys) for name, ccys in accounts if ccys]
    said = list(text.candidates) if text else []
    flagged = list(flag or ())

    if flagged and said:
        both = [c for c in flagged if c in said]
        if not both:
            raise LedgerError(
                f"the amount says {'/'.join(said)} but --ccy says {'/'.join(flagged)}",
                code="currency_conflict",
            )
        candidates, source = both, "flag"
    elif flagged:
        candidates, source = flagged, "flag"
    elif said:
        candidates, source = said, "text"
    else:
        candidates, source = [], ""

    if candidates:
        if len(candidates) == 1:
            ccy = _narrow(candidates, allowed)[0] if allowed else candidates[0]
            return CurrencyChoice(ccy, source)
        narrowed = _narrow(candidates, allowed)
        if len(narrowed) == 1:
            return CurrencyChoice(narrowed[0], "account", message=f"{narrowed[0]}: the only one the accounts allow")
        known = [c for c in narrowed if c in set(used) | {base}]
        symbol = text.symbol if text else "the currency"
        if len(known) == 1:
            # The base currency is what a symbol almost always means in that ledger, so it is used as is
            # (and reported). Any other pick is a guess about which currency was meant: ask for a check.
            return CurrencyChoice(
                known[0], "ledger", inferred=known[0] != base,
                message=f"{symbol} could be {', '.join(narrowed)}; {known[0]} is the only one in your ledger",
            )
        shown = known if len(known) > 1 else narrowed
        raise LedgerError(
            f"{symbol} could be {', '.join(shown)}; say which with --ccy",
            code="ambiguous_currency", details={"candidates": shown},
        )

    if allowed:
        common = set(allowed[0][1])
        for _, ccys in allowed[1:]:
            common &= set(ccys)
        if not common:
            raise LedgerError(
                "the accounts allow no currency in common; moving money between currencies needs `exchange`",
                code="currency_conflict", details={"accounts": [a for a, _ in allowed]},
            )
        if len(common) == 1:
            return CurrencyChoice(next(iter(common)), "account", message="the only currency the account allows")
        if base in common:
            return CurrencyChoice(base, "default")
        raise LedgerError(
            f"the accounts allow {', '.join(sorted(common))}; say which with --ccy",
            code="ambiguous_currency", details={"candidates": sorted(common)},
        )
    return CurrencyChoice(base, "default")
