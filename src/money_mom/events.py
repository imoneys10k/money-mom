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
KINDS = ("open", "close", "txn", "confirm", "void", "assert")
ROOTS = ("Assets", "Liabilities", "Equity", "Income", "Expenses")
ACTOR_TYPES = ("human", "agent")
SOURCE_TYPES = ("chat", "file", "screenshot", "import", "manual")
TXN_STATUSES = ("posted", "pending")

_COMMON_REQUIRED = frozenset({"v", "id", "kind", "ts", "actor"})
_COMMON_OPTIONAL = frozenset({"source", "confidence", "meta"})
_KIND_REQUIRED = {
    "open": frozenset({"account", "date"}),
    "close": frozenset({"account", "date"}),
    "txn": frozenset({"date", "status", "postings"}),
    "confirm": frozenset({"target"}),
    "void": frozenset({"target", "reason"}),
    "assert": frozenset({"date", "account", "amount", "ccy"}),
}
_KIND_OPTIONAL = {
    "open": frozenset({"currencies"}),
    "close": frozenset(),
    "txn": frozenset({"narration", "payee", "import_hash"}),
    "confirm": frozenset({"postings"}),
    "void": frozenset(),
    "assert": frozenset(),
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
class Posting:
    account: str | None
    amount: Decimal
    ccy: str


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


def _parse_date(value: Any, field: str = "date") -> _dt.date:
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


def _parse_postings(value: Any) -> tuple[Posting, ...]:
    if not isinstance(value, list) or len(value) < 2:
        raise _bad("postings must be a list of at least 2 entries", "invalid_postings")
    out = []
    for index, item in enumerate(value):
        label = f"postings[{index}]"
        if not isinstance(item, dict):
            raise _bad(f"{label} must be an object", "invalid_postings")
        extra = set(item) - {"account", "amount", "ccy"}
        if extra & {"cost", "price"}:
            raise _bad(
                f"{label}: cost/price are reserved for a future version and not supported yet",
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
        out.append(
            Posting(
                account=account,
                amount=parse_amount(item["amount"], f"{label}.amount"),
                ccy=check_currency(item["ccy"], f"{label}.ccy"),
            )
        )
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
            date=_parse_date(obj["date"]),
            currencies=currencies,
        )
    if kind == "close":
        return Close(
            **common, account=check_account_name(obj["account"]), date=_parse_date(obj["date"])
        )
    if kind == "txn":
        if obj["status"] not in TXN_STATUSES:
            raise _bad(f"status must be one of {', '.join(TXN_STATUSES)}", "invalid_status")
        return Txn(
            **common,
            date=_parse_date(obj["date"]),
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
    return Assert(
        **common,
        date=_parse_date(obj["date"]),
        account=check_account_name(obj["account"]),
        amount=parse_amount(obj["amount"]),
        ccy=check_currency(obj["ccy"]),
    )
