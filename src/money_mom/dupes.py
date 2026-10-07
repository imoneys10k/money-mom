"""Duplicate detection across sources, and recording a person's judgement about it.

The same payment often reaches the ledger twice: once from a WeChat statement and once from the bank's, or once
told to an agent in chat and once in a statement. Rows from *one* statement can never duplicate each other (a
stable row hash already stops a repeat import), so this module only compares entries of different origin.

Nothing here decides. It lists *candidates* with the evidence for each, an agent shows them to the person, and
the person's answer is written as a `review` event: `same` (one is voided, with `meta.duplicate_of`) or `different`
(remembered, so nobody asks again). A statement row that looks like a duplicate of an existing entry is written
as *pending* and held until the person answers, so it never reaches a balance before then.

Evidence, strongest first: the payment method on the statement row points at the other entry's account; the
payee or description matches; the dates are the same day. Equal amounts alone are only ever a *possible*.

A second kind of candidate is the two sides of one transfer between the person's own accounts: money leaves one
account and arrives in another, so each statement shows it once, with opposite signs. If the person says
`transfer`, both rows are voided and one real transfer is recorded in their place.
"""

from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from .errors import LedgerError
from .events import Posting
from .state import LedgerState, TxnRecord

DEFAULT_WINDOW_DAYS = 2
HELD = "duplicate_review"  # meta.held on a row waiting for the person's judgement
_GENERIC_SEGMENTS = {"储蓄卡", "信用卡", "银行卡", "借记卡", "cash", "checking", "savings", "credit", "card", "bank", "wallet", "银行", "钱包"}


@dataclass(frozen=True)
class Item:
    """One side of a comparison: a ledger transaction, or a statement row that is about to become one."""

    id: str | None
    date: _dt.date
    account: str
    amount: Decimal
    ccy: str
    payee: str
    narration: str
    origin: tuple[str, str]
    via: str
    status: str
    held: bool
    has_hash: bool
    source: str
    open_counter: bool = False  # the other side of the entry is not decided yet (an account is still `?`)

    def names(self) -> set[str]:
        return {n for n in (_norm(self.payee), _norm(self.narration)) if n}

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id, "date": self.date.isoformat(), "status": self.status, "account": self.account,
            "amount": format(self.amount, "f"), "ccy": self.ccy, "payee": self.payee or None,
            "narration": self.narration or None, "source": self.source, "payment_method": self.via or None,
            "held_for_review": self.held,
        }


@dataclass(frozen=True)
class Candidate:
    tier: str  # "likely" | "possible"
    evidence: tuple[str, ...]
    days_apart: int
    a: Item
    b: Item
    kind: str = "payment"  # "payment": the same payment twice; "transfer": the two sides of one own-account transfer

    def json(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "tier": self.tier, "evidence": list(self.evidence), "days_apart": self.days_apart,
            "ids": [self.a.id, self.b.id], "a": self.a.json(), "b": self.b.json(),
            "suggest_keep": suggest_keep(self.a, self.b) if self.kind == "payment" else None,
            "suggest": "same" if self.kind == "payment" else "transfer",
        }


def _norm(text: str | None) -> str:
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(c for c in folded if c.isalnum())


def _money_posting(postings: Iterable[Posting]) -> Posting | None:
    """The one asset/liability posting of a simple spend or income; None for transfers and anything unusual."""
    found = [p for p in postings if p.account and p.account.split(":", 1)[0] in ("Assets", "Liabilities")]
    return found[0] if len(found) == 1 else None


def origin_of(event) -> tuple[str, str]:
    """Rows of one statement file share an origin; every other entry is its own origin."""
    source = event.source or {}
    if source.get("type") == "import":
        return ("import", str(source.get("sha256") or source.get("ref") or event.id))
    return ("entry", event.id)


def item_from_record(rec: TxnRecord) -> Item | None:
    money = _money_posting(rec.postings)
    if money is None or money.amount == 0:
        return None
    ev = rec.event
    info = ev.meta.get("import") if isinstance(ev.meta.get("import"), dict) else {}
    source = ev.source or {}
    label = f"{source.get('type', 'entry')}:{source['ref']}" if source.get("ref") else source.get("type", "entry")
    others = [p for p in rec.postings if p is not money]
    return Item(
        id=rec.id, date=rec.date, account=money.account or "", amount=money.amount, ccy=money.ccy,
        payee=ev.payee or "", narration=ev.narration or "", origin=origin_of(ev),
        via=str(info.get("via") or ""), status=rec.status, held=ev.meta.get("held") == HELD,
        has_hash=bool(ev.import_hash), source=label, open_counter=any(p.account is None for p in others),
    )


def suggest_keep(a: Item, b: Item) -> str | None:
    """Which one to keep if the person says they are the same: the one not held, then the one traceable to a
    statement, then the earlier one."""
    for pick in ((lambda i: not i.held), (lambda i: i.has_hash)):
        if pick(a) != pick(b):
            return (a if pick(a) else b).id
    return (a if a.date <= b.date else b).id


def _tokens(account: str, aliases: dict[str, str]) -> set[str]:
    """Words that identify an account in a statement's payment-method text: its aliases and path segments."""
    out = {k.casefold() for k, v in aliases.items() if v == account and len(k) >= 2}
    out |= {s.casefold() for s in account.split(":")[1:] if len(s) >= 2 and s.casefold() not in _GENERIC_SEGMENTS}
    return out


def _digit_groups(text: str) -> set[str]:
    return set(re.findall(r"\d{4}", text))


def _payment_link(via: str, other: Item, aliases: dict[str, str]) -> bool:
    if not via:
        return False
    low = via.casefold()
    tokens = _tokens(other.account, aliases)
    if any(t in low for t in tokens):
        return True
    digits = _digit_groups(via)
    return bool(digits) and any(digits & _digit_groups(t) for t in tokens | {other.account.casefold()})


def _same_names(a: Item, b: Item) -> bool:
    for x in a.names():
        for y in b.names():
            if x == y or (min(len(x), len(y)) >= 2 and (x in y or y in x)):
                return True
    return False


_TRANSFER_WORDS = ("转账", "充值", "提现", "还款", "转入", "转出", "transfer", "top-up", "topup", "withdraw")


def _mentions_account(text: str, other: Item, aliases: dict[str, str]) -> bool:
    low = text.casefold()
    return bool(low) and any(t in low for t in _tokens(other.account, aliases))


def compare_transfer(a: Item, b: Item, aliases: dict[str, str], window: int) -> Candidate | None:
    """The two sides of one transfer between own accounts: opposite amounts, different accounts, different origin."""
    days = abs((a.date - b.date).days)
    if days > window or a.origin == b.origin or a.account == b.account:
        return None
    if a.origin[0] == "entry" and b.origin[0] == "entry":
        return None  # a hand-made transfer is already one entry; two hand entries are not two statements
    evidence: list[str] = []
    text = f"{a.payee} {a.narration} {b.payee} {b.narration}"
    linked = (
        _mentions_account(f"{a.payee} {a.narration} {a.via}", b, aliases)
        or _mentions_account(f"{b.payee} {b.narration} {b.via}", a, aliases)
        or _payment_link(a.via, b, aliases) or _payment_link(b.via, a, aliases)
    )
    if linked:
        evidence.append("one_side_names_the_other_account")
    wording = any(w in text.casefold() for w in _TRANSFER_WORDS)
    if wording:
        evidence.append("transfer_wording")
    evidence.append("opposite_amounts")
    evidence.append("same_day" if days == 0 else f"{days}_day{'s' if days > 1 else ''}_apart")
    evidence.append("different_accounts")
    if days <= 1 and (linked or wording):
        tier = "likely"
    elif a.open_counter or b.open_counter:
        tier = "possible"  # without a reason, only worth asking while a side is still unclassified
    else:
        return None
    return Candidate(tier, tuple(evidence), days, a, b, kind="transfer")


def compare(a: Item, b: Item, aliases: dict[str, str], window: int = DEFAULT_WINDOW_DAYS) -> Candidate | None:
    """Is this pair worth asking about? Equal amount, currency and sign (or, for a transfer, the opposite sign
    in another account) are required; the rest is evidence."""
    if a.ccy != b.ccy:
        return None
    if a.amount == -b.amount and a.amount != 0:
        return compare_transfer(a, b, aliases, window)
    if a.amount != b.amount:
        return None
    days = abs((a.date - b.date).days)
    if days > window:
        return None
    if a.origin == b.origin:
        return None  # one statement file never duplicates itself: the row hash covers a repeat import
    both_entries = a.origin[0] == "entry" and b.origin[0] == "entry"
    same_account = a.account == b.account
    if both_entries and not (same_account and days == 0):
        return None  # two hand-made entries are only suspicious on the same day in the same account
    evidence: list[str] = []
    linked = _payment_link(a.via, b, aliases) or _payment_link(b.via, a, aliases)
    if linked:
        evidence.append("payment_method_points_at_the_other_account")
    names = _same_names(a, b)
    if names:
        evidence.append("same_payee_or_description")
    evidence.append("same_day" if days == 0 else f"{days}_day{'s' if days > 1 else ''}_apart")
    evidence.append("same_account" if same_account else "different_accounts")
    mixed = (a.origin[0] == "import") != (b.origin[0] == "import")
    if mixed:
        evidence.append("statement_row_and_hand_entry")
    strong = linked or names or (mixed and same_account and days == 0)
    tier = "likely" if days <= 1 and strong else "possible"
    return Candidate(tier, tuple(evidence), days, a, b)


def _items(state: LedgerState, exclude: set[str]) -> list[Item]:
    out = []
    for rec in state.txns.values():
        if rec.status == "voided" or rec.id in exclude:
            continue
        item = item_from_record(rec)
        if item is not None:
            out.append(item)
    return out


def find_candidates(
    state: LedgerState,
    aliases: dict[str, str],
    *,
    window: int = DEFAULT_WINDOW_DAYS,
    exclude: Iterable[str] = (),
    extra_judged: Iterable[frozenset[str]] = (),
) -> list[Candidate]:
    """Every unjudged candidate pair among the live transactions, most likely first."""
    judged = set(state.judged_pairs()) | set(extra_judged)
    buckets: dict[tuple[str, Decimal], list[Item]] = {}
    for item in _items(state, set(exclude)):
        buckets.setdefault((item.ccy, abs(item.amount)), []).append(item)
    found = []
    for items in buckets.values():
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if frozenset({a.id, b.id}) in judged:
                    continue
                cand = compare(a, b, aliases, window)
                if cand is not None:
                    found.append(cand)
    found.sort(key=lambda c: (c.tier != "likely", c.a.date, c.b.date, c.a.id or ""))
    return found


def candidates_for_probe(
    state: LedgerState, aliases: dict[str, str], probe: Item, *, window: int = DEFAULT_WINDOW_DAYS
) -> list[Candidate]:
    """Candidates between a not-yet-written statement row and the transactions already in the ledger."""
    found = []
    for item in _items(state, set()):
        cand = compare(item, probe, aliases, window)
        if cand is not None:
            found.append(cand)
    found.sort(key=lambda c: (c.tier != "likely", c.days_apart))
    return found


def candidates_involving(candidates: Iterable[Candidate], txn_id: str) -> list[Candidate]:
    return [c for c in candidates if txn_id in (c.a.id, c.b.id)]


# ---------------------------------------------------------------- recording the person's answer


def resolve_pair(
    ledger,
    first: str,
    second: str,
    *,
    verdict: str,
    keep: str | None = None,
    actor: tuple[str, str],
    user_said: str | None = None,
    window: int = DEFAULT_WINDOW_DAYS,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the person's judgement. `same` records a review and voids the other one (nothing is deleted);
    `different` records a review so the pair is never asked about again; `transfer` voids both and records one
    transfer between the two accounts in their place. A held statement row that has no unresolved candidates left
    afterwards is confirmed, which is the moment it first reaches a balance."""
    ledger.reload()
    state = ledger.state
    if verdict not in ("same", "different", "transfer"):
        raise LedgerError("the verdict must be `same`, `different` or `transfer`", code="invalid_review")
    if first == second:
        raise LedgerError("give two different transaction ids", code="invalid_review")
    for target in (first, second):
        rec = state.txns.get(target)
        if rec is None:
            raise LedgerError(f"no transaction with id {target!r}", code="unknown_target", details={"target": target})
        if rec.status == "voided":
            raise LedgerError(f"{target} is already voided", code="already_voided", details={"target": target})
    if actor[0] == "agent" and not (user_said and user_said.strip()):
        raise LedgerError(
            "a judgement about a duplicate is the user's to make: ask them, then pass what they said with --user-said",
            code="user_decision_required",
        )
    if verdict == "same":
        if keep not in (first, second):
            raise LedgerError("say which one to keep with --keep (one of the two ids)", code="invalid_review")
    elif keep is not None:
        raise LedgerError("--keep only goes with a `same` verdict", code="invalid_review")
    note = {"user_said": user_said.strip()} if user_said and user_said.strip() else {}
    extra = {**note, **(meta or {})}
    review = ledger.new_event(
        "review", actor=actor, meta=extra or None, targets=[first, second], verdict=verdict, keep=keep,
    )
    events = [review]
    dropped: list[str] = []
    new_transfer = None
    if verdict == "same":
        dropped = [second if keep == first else first]
        events.append(ledger.new_event(
            "void", actor=actor, target=dropped[0], reason=f"duplicate of {keep}", meta={**extra, "duplicate_of": keep},
        ))
    elif verdict == "transfer":
        ia, ib = item_from_record(state.txns[first]), item_from_record(state.txns[second])
        if ia is None or ib is None or ia.ccy != ib.ccy or ia.amount != -ib.amount or ia.account == ib.account:
            raise LedgerError(
                "these are not the two sides of one transfer: each must be a simple entry on its own account, with "
                "opposite amounts in the same currency",
                code="invalid_review",
            )
        narration = next((i.narration for i in (ia, ib) if i.narration), None)
        new_transfer = ledger.new_event(
            "txn", actor=actor, source={"type": "manual", "ref": "merged from two statements"},
            confidence=1.0 if actor[0] == "agent" else None, narration=narration, date=min(ia.date, ib.date).isoformat(),
            status="posted", meta={**extra, "merged_from": [first, second]},
            postings=[
                {"account": ia.account, "amount": format(ia.amount, "f"), "ccy": ia.ccy},
                {"account": ib.account, "amount": format(ib.amount, "f"), "ccy": ib.ccy},
            ],
        )
        events.append(new_transfer)
        dropped = [first, second]
        for target in dropped:
            events.append(ledger.new_event(
                "void", actor=actor, target=target, reason=f"two sides of one transfer, recorded as {new_transfer['id']}",
                meta={**extra, "merged_into": new_transfer["id"]},
            ))
    aliases = ledger.aliases()
    remaining = find_candidates(
        state, aliases, window=window, exclude=set(dropped), extra_judged=[frozenset({first, second})]
    )
    released = []
    for target in (first, second):
        rec = state.txns[target]
        if target in dropped or rec.status != "pending" or rec.event.meta.get("held") != HELD:
            continue
        if any(p.account is None for p in rec.postings) or candidates_involving(remaining, target):
            continue
        events.append(ledger.new_event("confirm", actor=actor, target=target, meta={"released_by_review": review["id"]}))
        released.append(target)
    ledger.append_many(events, clamp_ts=True)
    return {
        "verdict": verdict, "review": review["id"], "kept": keep, "voided": dropped[0] if verdict == "same" else None,
        "voided_both": dropped if verdict == "transfer" else None,
        "transfer": new_transfer["id"] if new_transfer else None, "released": released,
        "still_asking": [c.json() for c in remaining if first in (c.a.id, c.b.id) or second in (c.a.id, c.b.id)],
    }
