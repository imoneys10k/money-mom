"""Event schema v1: strict parsing of raw JSON objects into typed, immutable events.

Strictness is deliberate: an unknown kind or field is rejected rather than ignored,
so information is never silently dropped from a financial record.
"""

from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .errors import ValidationError

SCHEMA_VERSION = 1
KINDS = ("open", "close", "txn", "confirm", "void", "assert", "price", "review")
VERDICTS = ("same", "different", "transfer")
ROOTS = ("Assets", "Liabilities", "Equity", "Income", "Expenses")
ACTOR_TYPES = ("human", "agent")
SOURCE_TYPES = ("chat", "file", "screenshot", "import", "manual")
TXN_STATUSES = ("posted", "pending")

_COMMON_REQUIRED = frozenset({"v", "id", "kind", "ts", "actor"})
_COMMON_OPTIONAL = frozenset({"source", "confidence", "meta", "chain"})
_KIND_REQUIRED = {
    "open": frozenset({"account", "date"}),
    "close": frozenset({"account", "date"}),
    "txn": frozenset({"date", "status", "postings"}),
    "confirm": frozenset({"target"}),
    "void": frozenset({"target", "reason"}),
    "assert": frozenset({"date", "account", "amount", "ccy"}),
    "price": frozenset({"date", "base", "quote", "rate"}),
    "review": frozenset({"targets", "verdict"}),
}
_KIND_OPTIONAL = {
    "open": frozenset({"currencies"}),
    "close": frozenset(),
    "txn": frozenset({"narration", "payee", "import_hash"}),
    "confirm": frozenset({"postings"}),
    "void": frozenset(),
    "assert": frozenset(),
    "price": frozenset(),
    "review": frozenset({"keep"}),
}

_ID_RE = re.compile(r"[0-9A-Za-z_-]{1,64}")
_CCY_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,23}")
_AMOUNT_RE = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ABS_PATH_RE = re.compile(r"(?:[/~\\]|[A-Za-z]:[\\/])")


def _bad(message: str, code: str = "invalid_event", **details: Any) -> ValidationError:
    return ValidationError(message, code=code, details=details or None)


@dataclass(frozen=True)
class Cost:
    """What one unit of a held commodity (a share, a fund unit) cost, in a currency, and the day the lot was
    acquired. A posting with a cost is a *lot*: buying creates one, selling names the lot it takes from."""

    per_unit: Decimal
    ccy: str
    date: _dt.date | None = None  # None means: the date of the transaction


@dataclass(frozen=True)
class Posting:
    account: str | None
    amount: Decimal
    ccy: str
    cost: Cost | None = None

    def weight(self) -> tuple[str, Decimal]:
        """The (currency, amount) this posting counts for when a transaction is checked for balance: a lot counts
        for its cost, everything else for itself."""
        if self.cost is not None:
            return self.cost.ccy, self.amount * self.cost.per_unit
        return self.ccy, self.amount


@dataclass(frozen=True, kw_only=True)
class Event:
    raw: dict
    id: str
    kind: str
    ts: _dt.datetime
    actor_type: str
    actor_name: str
    source: dict | None
    confidence: float | None
    meta: dict


@dataclass(frozen=True, kw_only=True)
class Open(Event):
    account: str
    date: _dt.date
    currencies: tuple[str, ...] | None = None


@dataclass(frozen=True, kw_only=True)
class Close(Event):
    account: str
    date: _dt.date


@dataclass(frozen=True, kw_only=True)
class Txn(Event):
    date: _dt.date
    status: str
    postings: tuple[Posting, ...]
    narration: str | None = None
    payee: str | None = None
    import_hash: str | None = None


@dataclass(frozen=True, kw_only=True)
class Confirm(Event):
    target: str
    postings: tuple[Posting, ...] | None = None


@dataclass(frozen=True, kw_only=True)
class Void(Event):
    target: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class Assert(Event):
    date: _dt.date
    account: str
    amount: Decimal
    ccy: str


@dataclass(frozen=True, kw_only=True)
class Price(Event):
    """One unit of `base` is worth `rate` units of `quote` on `date`, according to `source`."""

    date: _dt.date
    base: str
    quote: str
    rate: Decimal


@dataclass(frozen=True, kw_only=True)
class Review(Event):
    """A person's judgement about two transactions: they are the same real-world payment (`same`, and which one
    to `keep`), two different ones (`different`, so nobody asks again), or the two sides of one transfer between
    their own accounts (`transfer`). Writing it changes nothing by itself; the events that act on it (a `void`, and
    for a transfer the new transaction) are written in the same batch."""

    targets: tuple[str, str]
    verdict: str
    keep: str | None = None


def check_account_name(name: Any, field: str = "account") -> str:
    if not isinstance(name, str):
        raise _bad(f"{field} must be a string", "invalid_account")
    if not unicodedata.is_normalized("NFC", name):
        raise _bad(f"{field} {name!r} must be in Unicode NFC form", "invalid_account")
    parts = name.split(":")
    if len(parts) < 2 or parts[0] not in ROOTS:
        raise _bad(
            f"{field} {name!r} must have at least two segments and start with one of "
            f"{', '.join(ROOTS)}",
            "invalid_account",
        )
    for seg in parts:
        if not seg or not seg[0].isalnum() or not all(c.isalnum() or c == "-" for c in seg):
            raise _bad(
                f"{field} {name!r} has an invalid segment {seg!r}: use letters, digits "
                "and '-', starting with a letter or digit",
                "invalid_account",
            )
    return name


def check_currency(ccy: Any, field: str = "ccy") -> str:
    if not isinstance(ccy, str) or not _CCY_RE.fullmatch(ccy):
        raise _bad(
            f"{field} {ccy!r} must be 1-24 chars of A-Z, 0-9, '.', '_' or '-'",
            "invalid_currency",
        )
    return ccy


def parse_amount(value: Any, field: str = "amount") -> Decimal:
    if not isinstance(value, str) or not _AMOUNT_RE.fullmatch(value):
        raise _bad(
            f"{field} must be a decimal string like \"38.00\" or \"-4500\", got {value!r}",
            "invalid_amount",
        )
    return Decimal(value)


def parse_date(value: Any, field: str = "date") -> _dt.date:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise _bad(f"{field} must be YYYY-MM-DD, got {value!r}", "invalid_date")
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        raise _bad(f"{field} {value!r} is not a real calendar date", "invalid_date") from None


def _parse_ts(value: Any) -> _dt.datetime:
    if not isinstance(value, str):
        raise _bad("ts must be an RFC 3339 string", "invalid_ts")
    try:
        parsed = _dt.datetime.fromisoformat(value)
    except ValueError:
        raise _bad(f"ts {value!r} is not a valid timestamp", "invalid_ts") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _bad(f"ts {value!r} must include a UTC offset, e.g. +08:00", "invalid_ts")
    return parsed


def _parse_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise _bad(f"{field} must be 1-64 chars of [0-9A-Za-z_-], got {value!r}", "invalid_id")
    return value


def _opt_str(obj: dict, key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _bad(f"{key} must be a non-empty string when present", "invalid_field")
    return value


def _parse_cost(value: Any, label: str, ccy: str, amount: Decimal) -> Cost | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _bad(f"{label}.cost must be an object", "invalid_cost")
    extra = set(value) - {"per_unit", "ccy", "date"}
    if extra:
        raise _bad(f"{label}.cost: unknown field(s) {sorted(extra)}", "unknown_field")
    if "per_unit" not in value or "ccy" not in value:
        raise _bad(f"{label}.cost needs `per_unit` and `ccy`", "invalid_cost")
    per_unit = parse_amount(value["per_unit"], f"{label}.cost.per_unit")
    if per_unit <= 0:
        raise _bad(f"{label}.cost.per_unit must be greater than zero", "invalid_cost")
    cost_ccy = check_currency(value["ccy"], f"{label}.cost.ccy")
    if cost_ccy == ccy:
        raise _bad(f"{label}: a lot of {ccy} cannot be priced in {ccy}", "invalid_cost")
    if amount == 0:
        raise _bad(f"{label}: a lot needs a non-zero number of units", "invalid_cost")
    day = parse_date(value["date"], f"{label}.cost.date") if value.get("date") is not None else None
    return Cost(per_unit, cost_ccy, day)


def _parse_postings(value: Any) -> tuple[Posting, ...]:
    if not isinstance(value, list) or len(value) < 2:
        raise _bad("postings must be a list of at least 2 entries", "invalid_postings")
    out = []
    for index, item in enumerate(value):
        label = f"postings[{index}]"
        if not isinstance(item, dict):
            raise _bad(f"{label} must be an object", "invalid_postings")
        extra = set(item) - {"account", "amount", "ccy", "cost"}
        if "price" in extra:
            raise _bad(
                f"{label}: `price` is reserved for a future version and not supported yet",
                "unsupported_field",
            )
        if extra:
            raise _bad(f"{label}: unknown field(s) {sorted(extra)}", "unknown_field")
        missing = {"account", "amount", "ccy"} - set(item)
        if missing:
            raise _bad(f"{label}: missing {sorted(missing)}", "missing_field")
        account = item["account"]
        if account is not None:
            check_account_name(account, f"{label}.account")
        ccy = check_currency(item["ccy"], f"{label}.ccy")
        amount = parse_amount(item["amount"], f"{label}.amount")
        out.append(Posting(account=account, amount=amount, ccy=ccy, cost=_parse_cost(item.get("cost"), label, ccy, amount)))
    return tuple(out)


def _parse_source(value: Any) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _bad("source must be an object", "invalid_source")
    extra = set(value) - {"type", "ref", "sha256"}
    if extra:
        raise _bad(f"source: unknown field(s) {sorted(extra)}", "unknown_field")
    stype = value.get("type")
    if stype not in SOURCE_TYPES:
        raise _bad(f"source.type must be one of {', '.join(SOURCE_TYPES)}", "invalid_source")
    ref = value.get("ref")
    if ref is not None and not isinstance(ref, str):
        raise _bad("source.ref must be a string", "invalid_source")
    if stype in ("file", "screenshot", "import") and ref and _ABS_PATH_RE.match(ref):
        raise _bad(
            "source.ref must not be an absolute path (it would leak local paths); "
            "use a file name or a relative description",
            "invalid_source",
        )
    sha = value.get("sha256")
    if sha is not None and (not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha)):
        raise _bad("source.sha256 must be 64 lowercase hex chars", "invalid_source")
    return value


def _parse_actor(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"type", "name"}:
        raise _bad('actor must be {"type": ..., "name": ...}', "invalid_actor")
    if value["type"] not in ACTOR_TYPES:
        raise _bad(f"actor.type must be one of {', '.join(ACTOR_TYPES)}", "invalid_actor")
    name = value["name"]
    if not isinstance(name, str) or not name.strip():
        raise _bad("actor.name must be a non-empty string", "invalid_actor")
    return value["type"], name


def _parse_confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise _bad("confidence must be a number between 0 and 1, or null", "invalid_confidence")
    return float(value)


def parse_event(obj: Any, where: str | None = None) -> Event:
    """Validate one raw JSON object and return a typed event. Raises ValidationError."""
    try:
        return _parse(obj)
    except ValidationError as err:
        if err.where is None:
            err.where = where
        raise


def _parse(obj: Any) -> Event:
    if not isinstance(obj, dict):
        raise _bad("an event must be a JSON object", "event_not_object")
    version = obj.get("v")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise _bad(f"unsupported schema version {version!r}; expected {SCHEMA_VERSION}", "unsupported_version")
    kind = obj.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise _bad(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}", "unknown_kind")

    required = _COMMON_REQUIRED | _KIND_REQUIRED[kind]
    allowed = required | _COMMON_OPTIONAL | _KIND_OPTIONAL[kind]
    extra = set(obj) - allowed
    if extra:
        raise _bad(f"unknown field(s) {sorted(extra)} for kind {kind!r}", "unknown_field")
    missing = required - set(obj)
    if missing:
        raise _bad(f"missing field(s) {sorted(missing)} for kind {kind!r}", "missing_field")

    chain = obj.get("chain")
    if chain is not None and (not isinstance(chain, str) or not _SHA256_RE.fullmatch(chain)):
        raise _bad("chain must be 64 lowercase hex chars", "invalid_field")
    meta = obj.get("meta", {})
    if not isinstance(meta, dict):
        raise _bad("meta must be an object", "invalid_field")
    actor_type, actor_name = _parse_actor(obj["actor"])
    common: dict[str, Any] = dict(
        raw=obj,
        id=_parse_id(obj["id"], "id"),
        kind=kind,
        ts=_parse_ts(obj["ts"]),
        actor_type=actor_type,
        actor_name=actor_name,
        source=_parse_source(obj.get("source")),
        confidence=_parse_confidence(obj.get("confidence")),
        meta=meta,
    )

    if kind == "open":
        currencies = obj.get("currencies")
        if currencies is not None:
            if not isinstance(currencies, list) or not currencies:
                raise _bad("currencies must be a non-empty list when present", "invalid_currency")
            currencies = tuple(check_currency(c, "currencies[]") for c in currencies)
            if len(set(currencies)) != len(currencies):
                raise _bad("currencies must not repeat", "invalid_currency")
        return Open(
            **common,
            account=check_account_name(obj["account"]),
            date=parse_date(obj["date"]),
            currencies=currencies,
        )
    if kind == "close":
        return Close(
            **common, account=check_account_name(obj["account"]), date=parse_date(obj["date"])
        )
    if kind == "txn":
        if obj["status"] not in TXN_STATUSES:
            raise _bad(f"status must be one of {', '.join(TXN_STATUSES)}", "invalid_status")
        return Txn(
            **common,
            date=parse_date(obj["date"]),
            status=obj["status"],
            postings=_parse_postings(obj["postings"]),
            narration=_opt_str(obj, "narration"),
            payee=_opt_str(obj, "payee"),
            import_hash=_opt_str(obj, "import_hash"),
        )
    if kind == "confirm":
        postings = obj.get("postings")
        return Confirm(
            **common,
            target=_parse_id(obj["target"], "target"),
            postings=None if postings is None else _parse_postings(postings),
        )
    if kind == "void":
        reason = obj["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise _bad("void requires a non-empty reason", "invalid_field")
        return Void(**common, target=_parse_id(obj["target"], "target"), reason=reason)
    if kind == "price":
        if common["source"] is None:
            raise _bad(
                "a price must carry a source (where the rate came from), for example "
                '{"type": "import", "ref": "api.frankfurter.dev"}',
                "missing_field",
            )
        base, quote = check_currency(obj["base"], "base"), check_currency(obj["quote"], "quote")
        if base == quote:
            raise _bad("base and quote must be different currencies", "invalid_price")
        rate = parse_amount(obj["rate"], "rate")
        if rate <= 0:
            raise _bad("rate must be greater than zero", "invalid_price")
        return Price(**common, date=parse_date(obj["date"]), base=base, quote=quote, rate=rate)
    if kind == "review":
        targets = obj["targets"]
        if not isinstance(targets, list) or len(targets) != 2:
            raise _bad("targets must be a list of exactly two transaction ids", "invalid_review")
        pair = (_parse_id(targets[0], "targets[0]"), _parse_id(targets[1], "targets[1]"))
        if pair[0] == pair[1]:
            raise _bad("targets must be two different transactions", "invalid_review")
        verdict = obj["verdict"]
        if verdict not in VERDICTS:
            raise _bad(f"verdict must be one of {', '.join(VERDICTS)}", "invalid_review")
        keep = obj.get("keep")
        if verdict == "same":
            if keep not in pair:
                raise _bad("a `same` verdict must say which of the two to keep (`keep`)", "invalid_review")
        elif keep is not None:
            raise _bad("`keep` only makes sense with a `same` verdict", "invalid_review")
        return Review(**common, targets=pair, verdict=verdict, keep=keep)
    return Assert(
        **common,
        date=parse_date(obj["date"]),
        account=check_account_name(obj["account"]),
        amount=parse_amount(obj["amount"]),
        ccy=check_currency(obj["ccy"]),
    )
