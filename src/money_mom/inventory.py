"""Lots: what is held, in which account, bought when and at what cost.

A posting that carries a `cost` is a lot. Buying adds units to a lot; selling names the lot it takes units from
(the `sell` command picks them, first in first out by default), so the cost basis of every sale is written into
the ledger and the realised gain can be read back from it, never recomputed from a guess.

Lots are derived from the transactions, in date order, every time: nothing is stored that could drift from the
entries. A sale that takes more units than the lot holds, or voiding a purchase whose shares were already sold,
is refused, because it would leave the books claiming shares that never existed.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .events import Posting

Entry = tuple[_dt.date, str, tuple[Posting, ...]]  # (date, transaction id, postings)
LotKey = tuple[str, str, Decimal, str, _dt.date]  # account, symbol, cost per unit, cost currency, acquired on


@dataclass
class Lot:
    account: str
    symbol: str
    units: Decimal
    per_unit: Decimal
    cost_ccy: str
    date: _dt.date
    txn_id: str  # the purchase that created it

    @property
    def key(self) -> LotKey:
        return (self.account, self.symbol, self.per_unit, self.cost_ccy, self.date)

    @property
    def cost(self) -> Decimal:
        return self.units * self.per_unit


def has_lots(postings: Iterable[Posting]) -> bool:
    return any(p.cost is not None and p.account is not None for p in postings)


def replay(entries: Iterable[Entry], as_of: _dt.date | None = None) -> tuple[dict[LotKey, Lot], list[str]]:
    """The lots left after every entry (up to `as_of`), and what is wrong: a sale from a lot that does not exist
    or does not hold enough units."""
    lots: dict[LotKey, Lot] = {}
    problems: list[str] = []
    for when, txn_id, postings in sorted(entries, key=lambda e: e[0]):  # stable: same day keeps event order
        if as_of is not None and when > as_of:
            continue
        for p in postings:
            if p.cost is None or p.account is None:
                continue
            key: LotKey = (p.account, p.ccy, p.cost.per_unit, p.cost.ccy, p.cost.date or when)
            if p.amount > 0:
                lot = lots.get(key)
                if lot is None:
                    lots[key] = Lot(p.account, p.ccy, p.amount, p.cost.per_unit, p.cost.ccy, p.cost.date or when, txn_id)
                else:
                    lot.units += p.amount
                continue
            lot = lots.get(key)
            held = lot.units if lot else Decimal(0)
            if held < -p.amount:
                problems.append(
                    f"{txn_id} takes {-p.amount} {p.ccy} from {p.account} out of the lot bought {key[4]} at "
                    f"{p.cost.per_unit} {p.cost.ccy}, which holds {held}"
                )
                continue
            lot.units += p.amount  # type: ignore[union-attr]
            if lot.units == 0:  # type: ignore[union-attr]
                del lots[key]
    return lots, problems


def open_lots(lots: dict[LotKey, Lot]) -> list[Lot]:
    """Oldest first, then cheapest first, so a listing and a first-in-first-out sale agree."""
    return sorted(lots.values(), key=lambda l: (l.account, l.symbol, l.date, l.per_unit))


METHODS = ("fifo", "lifo", "hifo")


def choose_lots(lots: Iterable[Lot], account: str, symbol: str, units: Decimal, method: str = "fifo") -> list[tuple[Lot, Decimal]]:
    """Which lots a sale of `units` takes, as (lot, units taken) pairs. Raises ValueError when there are not enough."""
    if method not in METHODS:
        raise ValueError(f"unknown lot method {method!r}; use one of {', '.join(METHODS)}")
    pool = [l for l in lots if l.account == account and l.symbol == symbol and l.units > 0]
    pool.sort(key={
        "fifo": lambda l: (l.date, l.per_unit),
        "lifo": lambda l: (_dt.date.max.toordinal() - l.date.toordinal(), -l.per_unit),
        "hifo": lambda l: (-l.per_unit, l.date),
    }[method])
    held = sum((l.units for l in pool), Decimal(0))
    if held < units:
        raise ValueError(f"{account} holds {held} {symbol}, not {units}")
    taken, left = [], units
    for lot in pool:
        if left == 0:
            break
        use = min(lot.units, left)
        taken.append((lot, use))
        left -= use
    return taken
