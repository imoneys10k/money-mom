"""The ledger directory: append-only JSONL event files plus a small TOML config.

Layout::

    <root>/money-mom.toml
    <root>/ledger/YYYY-MM.jsonl   events, filed by the UTC month of their `ts`
    <root>/ledger/.lock           advisory lock held while writing
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import tomllib
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .errors import LedgerError, RuleError, ValidationError
from .events import SCHEMA_VERSION, Event, Txn, check_currency, parse_event
from .ids import IdGenerator
from .state import LedgerState, Problem

CONFIG_NAME = "money-mom.toml"
LEDGER_DIR = "ledger"
TONES = ("gentle", "normal", "strict", "zen")
ALIASES_NAME = "aliases.toml"
DEFAULT_AUTO_POST_CONFIDENCE = 0.9
_FILE_RE = re.compile(r"(\d{4})-(\d{2})\.jsonl")

AmountLike = str | int | Decimal
PostingLike = tuple[str | None, AmountLike, str]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValidationError(f"duplicate key {key!r} in event", code="duplicate_key")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValidationError(f"{name} is not allowed in an event", code="invalid_event")


def _amount_str(value: AmountLike) -> str:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("money amounts must be str, int or Decimal, never float or bool")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("money amounts must be finite")
        return format(value, "f")
    return str(value)


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


@contextlib.contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Cross-process advisory lock (flock on POSIX, msvcrt on Windows; both run in CI)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        if os.name == "nt":  # pragma: no cover
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _event_file(ledger_dir: Path, ts: _dt.datetime) -> Path:
    utc = ts.astimezone(_dt.timezone.utc)
    return ledger_dir / f"{utc.year:04d}-{utc.month:02d}.jsonl"


class Ledger:
    def __init__(
        self,
        root: Path,
        config: dict[str, Any],
        state: LedgerState,
        *,
        clock: Callable[[], _dt.datetime] | None = None,
        ids: IdGenerator | None = None,
    ) -> None:
        self.root = root
        self.config = config
        self.state = state
        self._clock = clock or (lambda: _dt.datetime.now().astimezone())
        self._ids = ids or IdGenerator()

    @property
    def ledger_dir(self) -> Path:
        return self.root / LEDGER_DIR

    @classmethod
    def init(
        cls,
        root: str | os.PathLike[str],
        *,
        base_currency: str = "CNY",
        tone: str = "normal",
        **kwargs: Any,
    ) -> "Ledger":
        root = Path(root).expanduser()
        if (root / CONFIG_NAME).exists():
            raise LedgerError(f"{root} is already a Money Mom ledger", code="already_initialized")
        check_currency(base_currency, "base_currency")
        if tone not in TONES:
            raise LedgerError(f"tone must be one of {', '.join(TONES)}", code="invalid_config")
        (root / LEDGER_DIR).mkdir(parents=True, exist_ok=True)
        (root / CONFIG_NAME).write_text(
            f"schema = {SCHEMA_VERSION}\n"
            f"base_currency = {_toml_str(base_currency)}\n"
            f"tone = {_toml_str(tone)}\n"
            "# Entries an agent records with a confidence below this wait for your confirmation.\n"
            f"# auto_post_confidence = {DEFAULT_AUTO_POST_CONFIDENCE}\n",
            encoding="utf-8",
        )
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("cache.sqlite\ncache.sqlite-*\nledger/.lock\n", encoding="utf-8")
        return cls.open(root, **kwargs)

    @classmethod
    def open(cls, root: str | os.PathLike[str], **kwargs: Any) -> "Ledger":
        root = Path(root).expanduser()
        config = cls._read_config(root)
        return cls(root, config, cls._replay(root / LEDGER_DIR), **kwargs)

    @staticmethod
    def _read_config(root: Path) -> dict[str, Any]:
        path = root / CONFIG_NAME
        if not path.is_file():
            raise LedgerError(
                f"{root} is not a Money Mom ledger (missing {CONFIG_NAME}); run init first",
                code="not_a_ledger",
            )
        try:
            config = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as err:
            raise LedgerError(f"cannot parse {path}: {err}", code="invalid_config") from None
        if config.get("schema") != SCHEMA_VERSION:
            raise LedgerError(
                f"{path} has schema {config.get('schema')!r}; this version supports {SCHEMA_VERSION}",
                code="unsupported_version",
            )
        check_currency(config.get("base_currency"), "base_currency")
        if config.get("tone", "normal") not in TONES:
            raise LedgerError(f"tone must be one of {', '.join(TONES)}", code="invalid_config")
        threshold = config.get("auto_post_confidence", DEFAULT_AUTO_POST_CONFIDENCE)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise LedgerError("auto_post_confidence must be a number between 0 and 1", code="invalid_config")
        return config

    @staticmethod
    def _replay(ledger_dir: Path) -> LedgerState:
        state = LedgerState()
        files = sorted(p for p in ledger_dir.glob("*.jsonl") if _FILE_RE.fullmatch(p.name))
        for path in files:
            expected = _FILE_RE.fullmatch(path.name).groups()  # type: ignore[union-attr]
            with open(path, encoding="utf-8", newline="") as handle:
                text = handle.read()
            if text and not text.endswith("\n"):
                raise LedgerError(
                    f"{path.name} does not end with a newline; the last write was probably "
                    "interrupted. Inspect and repair the file by hand.",
                    code="corrupt_tail",
                    where=path.name,
                )
            for number, line in enumerate(text.split("\n"), start=1):
                if not line.strip():
                    continue
                where = f"{path.name}:{number}"
                try:
                    obj = json.loads(
                        line,
                        object_pairs_hook=_reject_duplicate_keys,
                        parse_constant=_reject_constant,
                    )
                except json.JSONDecodeError as err:
                    raise ValidationError(
                        f"invalid JSON: {err.msg}", code="invalid_json", where=where
                    ) from None
                except LedgerError as err:
                    err.where = err.where or where
                    raise
                ev = parse_event(obj, where)
                utc = ev.ts.astimezone(_dt.timezone.utc)
                if (f"{utc.year:04d}", f"{utc.month:02d}") != expected:
                    raise ValidationError(
                        f"event ts {ev.ts.isoformat()} belongs in {utc.year:04d}-{utc.month:02d}.jsonl, "
                        f"not {path.name}",
                        code="misplaced_event",
                        where=where,
                    )
                try:
                    state.apply(ev)
                except LedgerError as err:
                    err.where = err.where or where
                    raise
        return state

    @property
    def auto_post_confidence(self) -> float:
        return float(self.config.get("auto_post_confidence", DEFAULT_AUTO_POST_CONFIDENCE))

    @property
    def base_currency(self) -> str:
        return self.config["base_currency"]

    def aliases(self) -> dict[str, str]:
        """Friendly names for accounts, read fresh from aliases.toml."""
        path = self.root / ALIASES_NAME
        if not path.is_file():
            return {}
        try:
            table = tomllib.loads(path.read_text(encoding="utf-8")).get("aliases", {})
        except tomllib.TOMLDecodeError as err:
            raise LedgerError(f"cannot parse {path}: {err}", code="invalid_config") from None
        if not isinstance(table, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in table.items()):
            raise LedgerError(f"{path}: [aliases] must map text to account names", code="invalid_config")
        return dict(table)

    def _write_aliases(self, mapping: dict[str, str]) -> None:
        lines = ["# Managed by `money-mom alias`.", "[aliases]"]
        lines += [
            f"{json.dumps(k, ensure_ascii=False)} = {json.dumps(v, ensure_ascii=False)}"
            for k, v in sorted(mapping.items())
        ]
        tmp = self.root / f"{ALIASES_NAME}.tmp-{os.getpid()}"
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, self.root / ALIASES_NAME)

    def set_alias(self, alias: str, account: str) -> None:
        alias = alias.strip()
        if not alias or len(alias) > 64 or any(ord(c) < 32 for c in alias):
            raise LedgerError("an alias must be 1-64 printable characters", code="invalid_alias")
        with _exclusive_lock(self.ledger_dir / ".lock"):
            self.reload()
            if account not in self.state.accounts:
                raise LedgerError(
                    f"cannot alias {alias!r} to {account!r}: no such account", code="unknown_account",
                    details={"account": account},
                )
            mapping = {k: v for k, v in self.aliases().items() if k.casefold() != alias.casefold()}
            mapping[alias] = account
            self._write_aliases(mapping)

    def remove_alias(self, alias: str) -> None:
        with _exclusive_lock(self.ledger_dir / ".lock"):
            mapping = self.aliases()
            kept = {k: v for k, v in mapping.items() if k.casefold() != alias.strip().casefold()}
            if len(kept) == len(mapping):
                raise LedgerError(f"no alias {alias!r}", code="unknown_alias")
            self._write_aliases(kept)

    def reload(self) -> None:
        self.state = self._replay(self.ledger_dir)

    def check(self) -> list[Problem]:
        """Replay everything from disk and re-verify live assertions against the final state."""
        self.reload()
        return self.state.unmet_assertions()

    def append(self, raw: dict[str, Any], *, clamp_ts: bool = False) -> Event:
        return self.append_many([raw], clamp_ts=clamp_ts)[0]

    def append_many(
        self, raws: Iterable[dict[str, Any]], *, clamp_ts: bool = False
    ) -> list[Event]:
        """Validate and write events all-or-nothing (within one month file; see note).

        The whole ledger is replayed from disk under an exclusive lock first, so writers
        in other processes can never cause a stale-state write. If any event is rejected
        nothing is written. A batch that spans two UTC months is written file by file,
        so a crash between the two files can leave only the earlier month written.

        With `clamp_ts=True` an event whose ts is earlier than the newest event on disk
        gets that newest ts instead. The convenience methods use this because another
        process may have written since the event was built; raw callers stay strict.
        """
        raws = list(raws)
        if not raws:
            return []
        with _exclusive_lock(self.ledger_dir / ".lock"):
            fresh = self._replay(self.ledger_dir)
            events: list[Event] = []
            for index, raw in enumerate(raws):
                where = f"event #{index + 1} of this write" if len(raws) > 1 else None
                if clamp_ts and fresh.last_ts is not None:
                    raw = self._clamped(raw, fresh.last_ts)
                try:
                    ev = parse_event(raw, where)
                    self._check_write_policy(ev)
                    fresh.apply(ev)
                except LedgerError as err:
                    err.where = err.where or where
                    raise
                events.append(ev)
            by_file: dict[Path, list[str]] = defaultdict(list)
            for ev in events:
                line = json.dumps(ev.raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                by_file[_event_file(self.ledger_dir, ev.ts)].append(line)
            for path in sorted(by_file):
                with open(path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write("\n".join(by_file[path]) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            self.state = fresh
        return events

    def _check_write_policy(self, ev: Event) -> None:
        """Rules about *who* may write what. They apply to new writes only, never to replay,
        so changing the threshold later cannot make an old ledger unreadable.

        `actor` is self-declared, so this protects against an agent that forgets or is
        careless, not against one that lies. The agent's own permission prompts remain the
        real boundary.
        """
        if not isinstance(ev, Txn) or ev.actor_type != "agent":
            return
        if ev.confidence is None:
            raise RuleError(
                "an agent must state a confidence (0 to 1) for every transaction it records; "
                "pass --confidence, honestly",
                code="confidence_required",
            )
        threshold = self.auto_post_confidence
        if ev.status == "posted" and ev.confidence < threshold:
            raise RuleError(
                f"confidence {ev.confidence:g} is below the auto-post threshold {threshold:g}; "
                "record it as pending (--status pending) and ask the user to confirm",
                code="confidence_too_low",
                details={"confidence": ev.confidence, "threshold": threshold},
            )

    @staticmethod
    def _clamped(raw: dict[str, Any], last: _dt.datetime) -> dict[str, Any]:
        try:
            ts = _dt.datetime.fromisoformat(raw["ts"])
        except (KeyError, TypeError, ValueError):
            return raw  # let parse_event report the problem
        if ts.tzinfo is not None and ts < last:
            return {**raw, "ts": last.isoformat()}
        return raw

    def new_event(
        self,
        kind: str,
        *,
        actor: tuple[str, str],
        source: dict[str, Any] | None = None,
        confidence: float | None = None,
        meta: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """Build a raw event dict with a fresh id and a ts that never runs backwards."""
        ts = self._clock().replace(microsecond=0)
        last = self.state.last_ts
        if last is not None and ts < last:
            ts = last
        raw: dict[str, Any] = {
            "v": SCHEMA_VERSION,
            "id": self._ids.new(),
            "kind": kind,
            "ts": ts.isoformat(),
            "actor": {"type": actor[0], "name": actor[1]},
        }
        if source is not None:
            raw["source"] = source
        if confidence is not None:
            raw["confidence"] = confidence
        if meta:
            raw["meta"] = meta
        raw.update({k: v for k, v in fields.items() if v is not None})
        return raw

    @staticmethod
    def _postings(postings: Iterable[PostingLike]) -> list[dict[str, Any]]:
        return [
            {"account": account, "amount": _amount_str(amount), "ccy": ccy}
            for account, amount, ccy in postings
        ]

    def open_account(
        self,
        account: str,
        date: _dt.date | str,
        *,
        currencies: list[str] | None = None,
        actor: tuple[str, str] = ("human", "user"),
        **extra: Any,
    ) -> Event:
        return self.append(
            self.new_event(
                "open", actor=actor, account=account, date=str(date), currencies=currencies, **extra
            ),
            clamp_ts=True,
        )

    def close_account(
        self, account: str, date: _dt.date | str, *, actor: tuple[str, str] = ("human", "user"), **extra: Any
    ) -> Event:
        return self.append(
            self.new_event("close", actor=actor, account=account, date=str(date), **extra),
            clamp_ts=True,
        )

    def add_txn(
        self,
        date: _dt.date | str,
        postings: Iterable[PostingLike],
        *,
        status: str = "posted",
        narration: str | None = None,
        payee: str | None = None,
        import_hash: str | None = None,
        actor: tuple[str, str] = ("human", "user"),
        **extra: Any,
    ) -> Event:
        return self.append(
            self.new_event(
                "txn",
                actor=actor,
                date=str(date),
                status=status,
                postings=self._postings(postings),
                narration=narration,
                payee=payee,
                import_hash=import_hash,
                **extra,
            ),
            clamp_ts=True,
        )

    def confirm(
        self,
        target: str,
        postings: Iterable[PostingLike] | None = None,
        *,
        actor: tuple[str, str] = ("human", "user"),
        **extra: Any,
    ) -> Event:
        return self.append(
            self.new_event(
                "confirm",
                actor=actor,
                target=target,
                postings=None if postings is None else self._postings(postings),
                **extra,
            ),
            clamp_ts=True,
        )

    def void(
        self, target: str, reason: str, *, actor: tuple[str, str] = ("human", "user"), **extra: Any
    ) -> Event:
        return self.append(
            self.new_event("void", actor=actor, target=target, reason=reason, **extra),
            clamp_ts=True,
        )

    def assert_balance(
        self,
        account: str,
        date: _dt.date | str,
        amount: AmountLike,
        ccy: str,
        *,
        actor: tuple[str, str] = ("human", "user"),
        **extra: Any,
    ) -> Event:
        return self.append(
            self.new_event(
                "assert",
                actor=actor,
                account=account,
                date=str(date),
                amount=_amount_str(amount),
                ccy=ccy,
                **extra,
            ),
            clamp_ts=True,
        )
