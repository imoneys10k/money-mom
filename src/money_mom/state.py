"""Ledger state derived from the event stream, and the rules every event must obey.

`LedgerState.apply` validates an event against the current state and then mutates it.
Validation happens before any mutation, so a rejected event leaves the state untouched.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .errors import RuleError
from .events import Assert, Close, Confirm, Event, Open, Posting, Price, Txn, Void


@dataclass
class AccountInfo:
    name: str
    opened: _dt.date
    closed: _dt.date | None = None
    currencies: tuple[str, ...] | None = None


@dataclass
class TxnRecord:
    event: Txn
    postings: tuple[Posting, ...]
    status: str  # "pending" | "posted" | "voided"
    confirm_event_id: str | None = None
    void_event_id: str | None = None

    @property
    def id(self) -> str:
        return self.event.id

    @property
    def date(self) -> _dt.date:
        return self.event.date


@dataclass
class AssertRecord:
    event: Assert
    void_event_id: str | None = None

    @property
    def voided(self) -> bool:
        return self.void_event_id is not None


@dataclass
class PriceRecord:
    event: Price
    void_event_id: str | None = None

    @property
    def voided(self) -> bool:
        return self.void_event_id is not None


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    event_id: str | None = None


@dataclass
class LedgerState:
    accounts: dict[str, AccountInfo] = field(default_factory=dict)
    txns: dict[str, TxnRecord] = field(default_factory=dict)
    assertions: dict[str, AssertRecord] = field(default_factory=dict)
    prices: dict[str, PriceRecord] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    _ids: set[str] = field(default_factory=set)
    _last_ts: _dt.datetime | None = None
    _import_index: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    @property
    def last_ts(self) -> _dt.datetime | None:
        return self._last_ts

    def apply(self, ev: Event) -> None:
        if ev.id in self._ids:
            raise RuleError(f"event id {ev.id!r} already exists", code="duplicate_id")
        if self._last_ts is not None and ev.ts < self._last_ts:
            raise RuleError(
                f"event ts {ev.ts.isoformat()} is earlier than the previous event "
                f"({self._last_ts.isoformat()}); the ledger is append-only and ordered",
                code="ts_regression",
            )
        getattr(self, f"_apply_{ev.kind}")(ev)
        self._ids.add(ev.id)
        self._last_ts = ev.ts
        self.events.append(ev)

    def balances(self, as_of: _dt.date | None = None) -> dict[tuple[str, str], Decimal]:
        """Balance per (account, currency) from posted, non-voided transactions."""
        totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for rec in self.txns.values():
            if rec.status != "posted" or (as_of is not None and rec.date > as_of):
                continue
            for p in rec.postings:
                totals[(p.account, p.ccy)] += p.amount
        return dict(totals)

    def balance_of(self, account: str, as_of: _dt.date | None = None) -> dict[str, Decimal]:
        return {
            ccy: amount
            for (acct, ccy), amount in self.balances(as_of).items()
            if acct == account
        }

    def currencies_in_use(self) -> set[str]:
        """Every currency the ledger has seen: in entries (posted, pending or voided) and in accounts."""
        used = {p.ccy for rec in self.txns.values() for p in rec.postings}
        used |= {c for info in self.accounts.values() for c in (info.currencies or ())}
        return used

    def effective_prices(self) -> dict[tuple[str, str], dict[_dt.date, PriceRecord]]:
        """Live rates per (base, quote) and date. A later price for the same day replaces an earlier one."""
        table: dict[tuple[str, str], dict[_dt.date, PriceRecord]] = {}
        for rec in self.prices.values():  # insertion order is event order
            if not rec.voided:
                table.setdefault((rec.event.base, rec.event.quote), {})[rec.event.date] = rec
        return table

    def pending(self) -> list[TxnRecord]:
        return [r for r in self.txns.values() if r.status == "pending"]

    def unmet_assertions(self) -> list[Problem]:
        """Re-check live assertions against the final state (surfaces lock overrides)."""
        problems = []
        for rec in self.assertions.values():
            if rec.voided:
                continue
            ev = rec.event
            actual = self.balance_of(ev.account, ev.date).get(ev.ccy, Decimal(0))
            if actual != ev.amount:
                problems.append(
                    Problem(
                        "assertion_failed",
                        f"{ev.account} on {ev.date}: expected {ev.amount} {ev.ccy}, "
                        f"ledger says {actual} {ev.ccy}",
                        ev.id,
                    )
                )
        return problems

    def _locked_through(self, account: str) -> _dt.date | None:
        dates = [
            r.event.date
            for r in self.assertions.values()
            if not r.voided and r.event.account == account
        ]
        return max(dates) if dates else None

    def _known_account(self, name: str, when: _dt.date | None = None) -> AccountInfo:
        info = self.accounts.get(name)
        if info is None:
            raise RuleError(
                f"account {name!r} is not open; add an `open` event for it first",
                code="unknown_account",
                details={"account": name},
            )
        if when is not None:
            if when < info.opened:
                raise RuleError(
                    f"{name} is dated {when}, before it opened on {info.opened}",
                    code="date_before_open",
                    details={"account": name},
                )
            if info.closed is not None and when > info.closed:
                raise RuleError(
                    f"{name} is dated {when}, after it closed on {info.closed}",
                    code="account_closed",
                    details={"account": name},
                )
        return info

    def _check_postings(
        self, when: _dt.date, postings: tuple[Posting, ...], *, posted: bool
    ) -> None:
        for p in postings:
            if p.account is None:
                if posted:
                    raise RuleError(
                        "a posted transaction cannot have an unresolved (null) account; "
                        "keep it pending until the account is known",
                        code="unresolved_account",
                    )
                continue
            info = self._known_account(p.account, when)
            if info.currencies is not None and p.ccy not in info.currencies:
                raise RuleError(
                    f"{p.account} only allows {', '.join(info.currencies)}, got {p.ccy}",
                    code="currency_not_allowed",
                    details={"account": p.account, "ccy": p.ccy},
                )
        if posted:
            sums: dict[str, Decimal] = defaultdict(Decimal)
            for p in postings:
                sums[p.ccy] += p.amount
            off = {ccy: str(total) for ccy, total in sums.items() if total != 0}
            if off:
                raise RuleError(
                    "postings do not balance: "
                    + ", ".join(f"{ccy} off by {total}" for ccy, total in off.items()),
                    code="unbalanced",
                    details={"off_by": off},
                )

    def _check_lock(self, when: _dt.date, postings: tuple[Posting, ...], ev: Event) -> None:
        locked = sorted(
            {
                p.account
                for p in postings
                if p.account is not None
                and (through := self._locked_through(p.account)) is not None
                and when <= through
            }
        )
        if not locked:
            return
        reason = ev.meta.get("override_lock")
        if isinstance(reason, str) and reason.strip():
            if ev.actor_type == "human":
                return
            raise RuleError(
                f"{', '.join(locked)} is locked by a balance assertion on or after {when}; "
                "an agent cannot override a lock, ask the user to confirm and record it themselves",
                code="lock_override_denied",
                details={"accounts": locked},
            )
        raise RuleError(
            f"{', '.join(locked)} is locked by a balance assertion on or after {when}; "
            "a human must add meta.override_lock with a reason to change this period",
            code="period_locked",
            details={"accounts": locked},
        )

    def _apply_open(self, ev: Open) -> None:
        if ev.account in self.accounts:
            raise RuleError(
                f"account {ev.account!r} already exists (accounts cannot be reopened)",
                code="account_exists",
                details={"account": ev.account},
            )
        self.accounts[ev.account] = AccountInfo(ev.account, ev.date, None, ev.currencies)

    def _apply_close(self, ev: Close) -> None:
        info = self._known_account(ev.account)
        if info.closed is not None:
            raise RuleError(f"{ev.account} is already closed", code="account_closed")
        if ev.date < info.opened:
            raise RuleError(
                f"{ev.account} cannot close on {ev.date}, before it opened on {info.opened}",
                code="date_before_open",
            )
        leftover = {c: str(a) for c, a in self.balance_of(ev.account).items() if a != 0}
        if leftover:
            raise RuleError(
                f"{ev.account} still has a balance ({leftover}); move it out before closing",
                code="account_not_empty",
                details={"balance": leftover},
            )
        for rec in self.txns.values():
            if rec.status == "posted" and rec.date > ev.date:
                if any(p.account == ev.account for p in rec.postings):
                    raise RuleError(
                        f"{ev.account} has postings after {ev.date}",
                        code="postings_after_close",
                    )
        info.closed = ev.date

    def _apply_txn(self, ev: Txn) -> None:
        posted = ev.status == "posted"
        self._check_postings(ev.date, ev.postings, posted=posted)
        if posted:
            self._check_lock(ev.date, ev.postings, ev)
        if ev.import_hash and not ev.meta.get("allow_duplicate_import"):
            live = [t for t in self._import_index.get(ev.import_hash, []) if self.txns[t].status != "voided"]
            if live:
                raise RuleError(
                    f"import_hash {ev.import_hash!r} was already imported by transaction "
                    f"{live[0]}; void it first or set meta.allow_duplicate_import",
                    code="duplicate_import",
                    details={"existing": live[0]},
                )
        self.txns[ev.id] = TxnRecord(ev, ev.postings, ev.status)
        if ev.import_hash:
            self._import_index[ev.import_hash].append(ev.id)

    def _target_txn(self, target: str) -> TxnRecord:
        rec = self.txns.get(target)
        if rec is None:
            raise RuleError(
                f"no transaction with id {target!r}", code="unknown_target", details={"target": target}
            )
        return rec

    def _apply_confirm(self, ev: Confirm) -> None:
        rec = self._target_txn(ev.target)
        if rec.status != "pending":
            raise RuleError(
                f"transaction {ev.target} is {rec.status}, only pending ones can be confirmed",
                code="not_pending",
            )
        postings = ev.postings if ev.postings is not None else rec.postings
        self._check_postings(rec.date, postings, posted=True)
        self._check_lock(rec.date, postings, ev)
        rec.postings = postings
        rec.status = "posted"
        rec.confirm_event_id = ev.id

    def _apply_void(self, ev: Void) -> None:
        rec = self.txns.get(ev.target)
        if rec is not None:
            if rec.status == "voided":
                raise RuleError(f"{ev.target} is already voided", code="already_voided")
            if rec.status == "posted":
                self._check_lock(rec.date, rec.postings, ev)
            rec.status = "voided"
            rec.void_event_id = ev.id
            return
        arec = self.assertions.get(ev.target)
        if arec is not None:
            if arec.voided:
                raise RuleError(f"{ev.target} is already voided", code="already_voided")
            arec.void_event_id = ev.id
            return
        prec = self.prices.get(ev.target)
        if prec is not None:
            if prec.voided:
                raise RuleError(f"{ev.target} is already voided", code="already_voided")
            prec.void_event_id = ev.id
            return
        raise RuleError(
            f"no transaction, assertion or price with id {ev.target!r}",
            code="unknown_target",
            details={"target": ev.target},
        )

    def _apply_price(self, ev: Price) -> None:
        self.prices[ev.id] = PriceRecord(ev)

    def _apply_assert(self, ev: Assert) -> None:
        self._known_account(ev.account)
        actual = self.balance_of(ev.account, ev.date).get(ev.ccy, Decimal(0))
        if actual != ev.amount:
            raise RuleError(
                f"{ev.account} on {ev.date}: asserted {ev.amount} {ev.ccy} but the ledger "
                f"has {actual} {ev.ccy} (difference {ev.amount - actual})",
                code="assertion_failed",
                details={"expected": str(ev.amount), "actual": str(actual)},
            )
        self.assertions[ev.id] = AssertRecord(ev)
