"""Business-level intents (spend / income / transfer) rendered into balanced postings.

An agent says what happened in everyday terms ("spent 38 from Alipay on coffee"); this
module decides the debit/credit direction and resolves names to accounts. It never guesses:
a name that is missing, unknown, ambiguous or only approximately matched leaves that side
of the transaction unresolved, and the entry is recorded as *pending* with the candidate
accounts attached so someone can confirm it.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .currency import Money, choose_currency, parse_currency, parse_money
from .errors import LedgerError, RuleError
from .events import Event, check_currency, parse_amount, parse_date
from .ledger import Ledger
from .state import AccountInfo, LedgerState

ASSET_LIKE = ("Assets", "Liabilities", "Equity")

# slot name -> account roots allowed there
SPECS: dict[str, dict[str, tuple[str, ...]]] = {
    "spend": {"from": ("Assets", "Liabilities"), "category": ("Expenses",)},
    "income": {"to": ("Assets", "Liabilities"), "category": ("Income",)},
    "transfer": {"from": ASSET_LIKE, "to": ASSET_LIKE},
}


# which posting each slot fills, in the order record_intent renders them
SLOT_POSTING: dict[str, dict[str, int]] = {
    "spend": {"category": 0, "from": 1},
    "income": {"to": 0, "category": 1},
    "transfer": {"to": 0, "from": 1},
}


@dataclass
class Resolution:
    term: str | None
    account: str | None = None
    reason: str | None = None  # None when resolved
    matched_by: str | None = None  # exact | alias | segment
    candidates: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.account is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term, "account": self.account, "reason": self.reason,
            "matched_by": self.matched_by, "candidates": self.candidates, "message": self.message,
        }


def _root(name: str) -> str:
    return name.split(":", 1)[0]


def _open_on(info: AccountInfo, when: _dt.date) -> bool:
    return info.opened <= when and (info.closed is None or when <= info.closed)


def _check_named(
    state: LedgerState, name: str, roots: tuple[str, ...], when: _dt.date, term: str, matched_by: str
) -> Resolution:
    info = state.accounts[name]
    if _root(name) not in roots:
        return Resolution(term, reason="wrong_type", message=f"{name} is a {_root(name)} account, but this slot needs {' or '.join(roots)}")
    if not _open_on(info, when):
        return Resolution(term, reason="unavailable", message=f"{name} is not open on {when}")
    return Resolution(term, account=name, matched_by=matched_by)


def resolve_account(
    state: LedgerState,
    aliases: dict[str, str],
    term: str | None,
    roots: tuple[str, ...],
    when: _dt.date,
) -> Resolution:
    """Map what someone said to exactly one account, or explain why that is not possible.

    Accepted: the full account name, an alias, or a trailing run of name segments that
    matches exactly one open account (``咖啡`` or ``餐饮:咖啡`` for ``Expenses:餐饮:咖啡``).
    A mere substring match is only ever offered as a candidate, never accepted.
    """
    if term is None or not term.strip():
        return Resolution(term, reason="missing", message="not specified")
    term = term.strip()
    folded = {alias.casefold(): target for alias, target in aliases.items()}

    if term in state.accounts:
        return _check_named(state, term, roots, when, term, "exact")
    target = folded.get(term.casefold())
    if target is not None:
        if target not in state.accounts:
            return Resolution(term, reason="unknown", message=f"alias {term!r} points to {target}, which does not exist")
        return _check_named(state, target, roots, when, term, "alias")

    pool = sorted(
        name for name, info in state.accounts.items() if _root(name) in roots and _open_on(info, when)
    )
    wanted = [seg.casefold() for seg in term.split(":")]
    exact = [n for n in pool if [s.casefold() for s in n.split(":")][-len(wanted):] == wanted]
    if len(exact) == 1:
        return Resolution(term, account=exact[0], matched_by="segment")
    if len(exact) > 1:
        return Resolution(term, reason="ambiguous", candidates=exact, message=f"{term!r} matches {len(exact)} accounts")
    near = [n for n in pool if any(wanted[-1] in seg.casefold() for seg in n.split(":")[1:])]
    if near:
        return Resolution(
            term, reason="approximate", candidates=near,
            message=f"no account is named {term!r}; these are similar (never auto-accepted)",
        )
    return Resolution(term, reason="unknown", message=f"no open {' or '.join(roots)} account matches {term!r}")


@dataclass
class IntentResult:
    kind: str
    status: str
    date: str
    amount: str
    ccy: str
    postings: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]
    reasons: list[str]
    event: Event | None  # None for a dry run
    currency: dict[str, Any] | None = None  # how the currency was decided: matched_by, inferred

    @property
    def written(self) -> bool:
        return self.event is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.event.id if self.event else None,
            "written": self.written, "kind": self.kind, "status": self.status,
            "date": self.date, "amount": self.amount, "ccy": self.ccy,
            "postings": self.postings, "unresolved": self.unresolved, "reasons": self.reasons,
            "currency": self.currency,
        }


def _amount_and_money(value: Any) -> tuple[Decimal, Money | None]:
    """A positive amount, plus the currency written with it if the amount was text such as ``HK$200``."""
    if isinstance(value, bool) or isinstance(value, float):
        raise LedgerError("amount must be text, an int or a Decimal, never a float", code="invalid_amount")
    money = None
    if isinstance(value, str):
        money = parse_money(value)
        text = money.amount
    else:
        text = format(value, "f") if isinstance(value, Decimal) else str(value)
    amount = parse_amount(text)
    if amount <= 0:
        raise LedgerError(f"amount must be greater than zero (got {text}); the direction comes from the intent", code="invalid_amount")
    return amount, money


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise LedgerError("confidence must be a number between 0 and 1", code="invalid_confidence")
    return float(value)


def record_intent(
    ledger: Ledger,
    kind: str,
    *,
    amount: Any,
    slots: dict[str, str | None],
    date: _dt.date | str,
    ccy: str | None = None,
    payee: str | None = None,
    narration: str | None = None,
    confidence: float | None = None,
    source: dict[str, str] | None = None,
    import_hash: str | None = None,
    actor: tuple[str, str] = ("human", "user"),
    strict: bool = False,
    dry_run: bool = False,
    meta: dict[str, Any] | None = None,
) -> IntentResult:
    """Render one intent and (unless `dry_run`) append it to the ledger."""
    spec = SPECS.get(kind)
    if spec is None:
        raise LedgerError(f"unknown intent {kind!r}; expected one of {', '.join(SPECS)}", code="usage_error")
    extra = set(slots) - set(spec)
    if extra:
        raise LedgerError(f"{kind} has no {sorted(extra)} slot; its slots are {sorted(spec)}", code="usage_error")

    value, money = _amount_and_money(amount)
    flag = parse_currency(ccy) if ccy else ()
    when = date if isinstance(date, _dt.date) else parse_date(date)
    confidence = _confidence(confidence)

    ledger.reload()  # resolve against the latest ledger on disk
    aliases = ledger.aliases()
    resolutions = {
        slot: resolve_account(ledger.state, aliases, slots.get(slot), roots, when)
        for slot, roots in spec.items()
    }
    if kind == "transfer" and resolutions["from"].ok and resolutions["from"].account == resolutions["to"].account:
        raise LedgerError(
            f"from and to are the same account ({resolutions['to'].account}); a transfer needs two",
            code="usage_error",
        )
    choice = choose_currency(
        text=money, flag=flag or None,
        accounts=[(r.account, ledger.state.accounts[r.account].currencies) for r in resolutions.values() if r.ok],
        used=ledger.state.currencies_in_use(), base=ledger.base_currency,
    )
    ccy = check_currency(choice.ccy)
    unresolved = [
        {"slot": slot, **res.to_dict()} for slot, res in resolutions.items() if not res.ok
    ]
    reasons = [f"{u['slot']}: {u['message']}" for u in unresolved]
    if choice.inferred:
        reasons.append(f"currency: {choice.message}; please confirm")
    if unresolved and strict:
        raise RuleError(
            "cannot resolve " + "; ".join(reasons), code="unresolved_account",
            details={"unresolved": unresolved},
        )
    threshold = ledger.auto_post_confidence
    if confidence is not None and confidence < threshold:
        reasons.append(f"confidence {confidence:g} is below the auto-post threshold {threshold:g}")
    status = "pending" if reasons else "posted"

    def account(slot: str) -> str | None:
        return resolutions[slot].account

    plus, minus = {
        "spend": ("category", "from"),
        "income": ("to", "category"),
        "transfer": ("to", "from"),
    }[kind]
    text = format(value, "f")
    postings = [
        {"account": account(plus), "amount": text, "ccy": ccy},
        {"account": account(minus), "amount": f"-{text}", "ccy": ccy},
    ]
    currency = {"ccy": ccy, "matched_by": choice.matched_by, "inferred": choice.inferred}
    record_meta: dict[str, Any] = {
        "intent": kind,
        "given": {slot: term for slot, term in slots.items() if term},
        "currency": {"matched_by": choice.matched_by, **({"text": money.symbol} if money and money.symbol else {})},
    }
    if unresolved:
        record_meta["unresolved"] = unresolved
    record_meta.update(meta or {})

    event = None
    if not dry_run:
        raw = ledger.new_event(
            "txn", actor=actor, source=source, confidence=confidence, meta=record_meta,
            date=when.isoformat(), status=status, postings=postings, payee=payee,
            narration=narration, import_hash=import_hash,
        )
        event = ledger.append(raw, clamp_ts=True)
    return IntentResult(kind, status, when.isoformat(), text, ccy, postings, unresolved, reasons, event, currency)


def postings_with_slots(ledger: Ledger, target: str, slots: dict[str, str | None]) -> list[dict[str, Any]]:
    """Fill (or replace) accounts of a pending intent-made transaction by slot name.

    Used by `confirm --category 餐饮`: names are resolved with the same never-guess rules,
    and a name that still cannot be resolved is an error that lists the candidates.
    """
    ledger.reload()
    rec = ledger.state.txns.get(target)
    if rec is None:
        raise RuleError(f"no transaction with id {target!r}", code="unknown_target", details={"target": target})
    kind = rec.event.meta.get("intent")
    if kind not in SPECS:
        raise LedgerError(
            f"{target} was not recorded from a spend/income/transfer intent, so slot names do not apply; "
            "give explicit --posting values instead",
            code="usage_error",
        )
    postings = [{"account": p.account, "amount": format(p.amount, "f"), "ccy": p.ccy} for p in rec.postings]
    aliases = ledger.aliases()
    for slot, term in slots.items():
        if term is None:
            continue
        if slot not in SPECS[kind]:
            raise LedgerError(f"a {kind} has no {slot!r} slot; its slots are {sorted(SPECS[kind])}", code="usage_error")
        res = resolve_account(ledger.state, aliases, term, SPECS[kind][slot], rec.date)
        if not res.ok:
            raise RuleError(
                f"cannot resolve {slot}: {res.message}", code="unresolved_account",
                details={"unresolved": [{"slot": slot, **res.to_dict()}]},
            )
        postings[SLOT_POSTING[kind][slot]]["account"] = res.account
    return postings
