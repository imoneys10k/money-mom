"""Statement import: a declarative mapping describes one CSV layout; the program applies it the same way every time.

Nothing here guesses. A row either matches a rule the user approved and is recorded, or it is held as
*pending* with its payee listed so the user is asked once and the answer becomes a rule. Importing the same
file twice is safe: every row has a stable hash and rows already in the ledger are skipped. The whole batch is
written all-or-nothing.

`inspect_statement` helps write a mapping: it reports the encoding, the header row and a *suggested* mapping,
and where it cannot tell (a date written 03/04/2026, which sign means money in) it says so instead of choosing.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import re
import tomllib
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .currency import choose_currency
from .errors import LedgerError
from .events import ROOTS, check_currency
from .intents import resolve_account
from .ledger import Ledger

IMPORTS_DIR = "imports"
ENCODINGS = ("utf-8-sig", "gb18030")
DELIMITERS = (",", "\t", ";", "|")
DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y.%m.%d", "%Y年%m月%d日", "%Y%m%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y", "%d.%m.%Y",
)
# the same cells read as day-first or as month-first: these must be told apart by a person, never guessed
DAY_OR_MONTH_FIRST = (("%d/%m/%Y", "%m/%d/%Y"), ("%d-%m-%Y", "%m-%d-%Y"))
SIGNS = ("inflow_positive", "outflow_positive", "unsigned")
FIELDS = ("any", "payee", "description")


def _bad(message: str, code: str = "invalid_mapping", **details: Any) -> LedgerError:
    return LedgerError(message, code=code, details=details or None)


# ---------------------------------------------------------------- mapping


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    text: str
    field: str
    account: str | None  # None means: skip these rows (for example a transfer imported from the other side)
    regex: bool


@dataclass(frozen=True)
class Mapping:
    name: str
    encoding: str | None
    delimiter: str | None
    header: tuple[str, ...]
    ccy: str | None
    columns: dict[str, str]
    date_formats: tuple[str, ...]
    sign: str
    direction_in: tuple[str, ...]
    direction_out: tuple[str, ...]
    direction_ignore: tuple[str, ...]
    skip_when: tuple[tuple[str, tuple[str, ...]], ...]
    rules: tuple[Rule, ...] = field(default_factory=tuple)


_COLUMN_KEYS = {"date", "amount", "amount_in", "amount_out", "payee", "description", "id", "direction", "balance"}


def _strs(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _bad(f"{label} must be a list of text values")
    return tuple(value)


def _unknown(table: dict, allowed: set[str], where: str) -> None:
    extra = set(table) - allowed
    if extra:
        raise _bad(f"unknown key(s) {sorted(extra)} in {where}; allowed: {sorted(allowed)}", code="invalid_mapping")


def parse_mapping(text: str, name: str = "mapping") -> Mapping:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise _bad(f"the mapping is not valid TOML: {err}") from None
    _unknown(data, {"name", "encoding", "delimiter", "header", "ccy", "columns", "format", "direction", "skip", "rules"}, "the mapping")
    columns = data.get("columns")
    if not isinstance(columns, dict) or not all(isinstance(v, str) and v for v in columns.values()):
        raise _bad("[columns] is required and every value must be a column name")
    _unknown(columns, _COLUMN_KEYS, "[columns]")
    columns = {key: _cell(value) for key, value in columns.items()}
    if "date" not in columns:
        raise _bad("[columns] needs a `date` column")
    has_single, has_pair = "amount" in columns, ("amount_in" in columns or "amount_out" in columns)
    if has_single == has_pair:
        raise _bad("[columns] needs either `amount`, or `amount_in` and/or `amount_out`, but not both kinds")

    fmt = data.get("format", {})
    _unknown(fmt, {"date", "dates", "sign"}, "[format]")
    formats = fmt.get("dates") if "dates" in fmt else ([fmt["date"]] if "date" in fmt else None)
    if not formats:
        raise _bad("[format] needs `date` (a strptime pattern such as \"%Y-%m-%d\") or `dates` (a list of them)")
    formats = _strs(formats, "[format] date")
    sign = fmt.get("sign", "unsigned" if "direction" in columns else "inflow_positive")
    if sign not in SIGNS:
        raise _bad(f"[format] sign must be one of {', '.join(SIGNS)}")
    if has_pair:
        sign = "inflow_positive"
    if sign == "unsigned" and "direction" not in columns:
        raise _bad("sign = \"unsigned\" needs a `direction` column that says which rows are money in or out")

    direction = data.get("direction", {})
    _unknown(direction, {"in", "out", "ignore"}, "[direction]")
    d_in = tuple(_cell(v) for v in _strs(direction.get("in", []), "[direction] in"))
    d_out = tuple(_cell(v) for v in _strs(direction.get("out", []), "[direction] out"))
    if "direction" in columns and sign == "unsigned" and not (d_in and d_out):
        raise _bad("[direction] needs `in` and `out`: the values of the direction column for money in and out")

    skips = []
    for index, item in enumerate(data.get("skip", [])):
        if not isinstance(item, dict):
            raise _bad(f"skip #{index + 1} must be a table with `column` and `values`")
        _unknown(item, {"column", "values"}, f"skip #{index + 1}")
        if not isinstance(item.get("column"), str):
            raise _bad(f"skip #{index + 1} needs a `column`")
        skips.append((_cell(item["column"]), tuple(_cell(v) for v in _strs(item.get("values"), f"skip #{index + 1} values"))))

    rules = []
    for index, item in enumerate(data.get("rules", [])):
        label = f"rule #{index + 1}"
        if not isinstance(item, dict):
            raise _bad(f"{label} must be a table")
        _unknown(item, {"match", "field", "regex", "account", "skip"}, label)
        match = item.get("match")
        if not isinstance(match, str) or not match.strip():
            raise _bad(f"{label} needs a non-empty `match`")
        field_name = item.get("field", "any")
        if field_name not in FIELDS:
            raise _bad(f"{label}: field must be one of {', '.join(FIELDS)}")
        is_regex = bool(item.get("regex", False))
        skip, account = bool(item.get("skip", False)), item.get("account")
        if skip == (account is not None):
            raise _bad(f"{label} needs exactly one of `account` or `skip = true`")
        try:
            pattern = re.compile(match if is_regex else re.escape(match), re.IGNORECASE)
        except re.error as err:
            raise _bad(f"{label}: `match` is not a valid regular expression: {err}") from None
        rules.append(Rule(pattern, match, field_name, None if skip else str(account), is_regex))

    ccy = data.get("ccy")
    if ccy is not None:
        ccy = check_currency(ccy, "ccy")
    encoding = data.get("encoding")
    if encoding is not None:
        try:
            "".encode(encoding)
        except LookupError:
            raise _bad(f"unknown encoding {encoding!r}") from None
    delimiter = data.get("delimiter")
    if delimiter is not None and (not isinstance(delimiter, str) or len(delimiter) != 1):
        raise _bad("delimiter must be a single character")
    header = tuple(_cell(h) for h in _strs(data.get("header", list(columns.values())), "header"))
    return Mapping(
        name=name, encoding=encoding, delimiter=delimiter, header=header, ccy=ccy,
        columns=dict(columns), date_formats=formats, sign=sign, direction_in=d_in, direction_out=d_out,
        direction_ignore=tuple(_cell(v) for v in _strs(direction.get("ignore", []), "[direction] ignore")),
        skip_when=tuple(skips),
        rules=tuple(rules),
    )


def mapping_path(ledger: Ledger, name_or_path: str) -> Path:
    candidate = Path(name_or_path).expanduser()
    if candidate.suffix == ".toml" and candidate.is_file():
        return candidate
    safe = re.fullmatch(r"[\w.-]+", name_or_path)
    if not safe:
        raise _bad(f"{name_or_path!r} is neither a file nor a mapping name (letters, digits, '-', '_', '.')", code="unknown_mapping")
    return ledger.root / IMPORTS_DIR / f"{name_or_path}.toml"


def load_mapping(ledger: Ledger, name_or_path: str) -> Mapping:
    path = mapping_path(ledger, name_or_path)
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in (ledger.root / IMPORTS_DIR).glob("*.toml"))) or "none yet"
        raise _bad(f"no mapping {name_or_path!r} (saved mappings: {known})", code="unknown_mapping")
    return parse_mapping(path.read_text(encoding="utf-8"), path.stem)


def save_mapping(ledger: Ledger, name: str, text: str) -> Path:
    """Validate a mapping and store it under the ledger's imports folder."""
    if not re.fullmatch(r"[\w.-]+", name):
        raise _bad(f"mapping names use letters, digits, '-', '_' and '.', got {name!r}", code="unknown_mapping")
    parse_mapping(text, name)  # refuses anything invalid before it is stored
    path = ledger.root / IMPORTS_DIR / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")
    return path


def list_mappings(ledger: Ledger) -> list[str]:
    return sorted(p.stem for p in (ledger.root / IMPORTS_DIR).glob("*.toml"))


def add_rule(
    ledger: Ledger, name: str, *, match: str, account: str | None, field_name: str = "any", regex: bool = False
) -> str:
    """Append a rule to a saved mapping (lower priority than the existing ones). `account=None` means skip."""
    path = mapping_path(ledger, name)
    mapping = load_mapping(ledger, name)  # also proves the file is valid before we touch it
    if field_name not in FIELDS:
        raise _bad(f"field must be one of {', '.join(FIELDS)}")
    if not match.strip():
        raise _bad("`match` must not be empty")
    if regex:
        try:
            re.compile(match)
        except re.error as err:
            raise _bad(f"not a valid regular expression: {err}") from None
    if account is not None:
        ledger.reload()
        res = resolve_account(ledger.state, ledger.aliases(), account, ROOTS, _dt.date.today())
        if not res.ok:
            raise LedgerError(
                f"cannot use {account!r} in a rule: {res.message}", code="unresolved_account",
                details={"unresolved": [res.to_dict()]},
            )
        account = res.account
    lines = ["", "[[rules]]", f"match = {_toml(match)}"]
    if field_name != "any":
        lines.append(f"field = {_toml(field_name)}")
    if regex:
        lines.append("regex = true")
    lines.append("skip = true" if account is None else f"account = {_toml(account)}")
    block = "\n".join(lines) + "\n"
    existing = path.read_text(encoding="utf-8")
    path.write_text(existing.rstrip("\n") + "\n" + block, encoding="utf-8", newline="\n")
    parse_mapping(path.read_text(encoding="utf-8"), name)  # never leave a broken mapping behind
    return f"rule #{len(mapping.rules) + 1}"


def _toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------- reading


@dataclass(frozen=True)
class StatementRow:
    line: int
    date: _dt.date
    amount: Decimal  # signed from the statement account's point of view: positive = money in
    payee: str
    description: str
    id: str
    balance: Decimal | None
    raw: dict[str, str]


@dataclass
class ParsedStatement:
    rows: list[StatementRow]
    ignored: Counter
    ignored_examples: list[str]
    preamble_lines: int
    encoding: str
    delimiter: str
    header_line: int
    sha256: str
    filename: str


def _decode(data: bytes, forced: str | None) -> tuple[str, str]:
    for encoding in ([forced] if forced else list(ENCODINGS)):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise LedgerError(
        f"cannot decode the file as {forced or ' or '.join(ENCODINGS)}; give the right one with --encoding",
        code="invalid_statement",
    )


def _cell(value: str | None) -> str:
    return unicodedata.normalize("NFKC", (value or "")).replace("﻿", "").strip(" \t\r\n　")


_CURRENCY_MARK = r"(?:[¥$€£]|CNY|RMB|USD|HKD|EUR|GBP)"


def _money(text: str) -> Decimal | None:
    """An amount as banks write it. Only currency marks and a sign may surround the digits: any other
    text (a footer such as "共7笔记录") is not an amount, even if it contains a digit."""
    t = _cell(text)
    if not t:
        return None
    negative = False
    if t.startswith("(") and t.endswith(")"):
        negative, t = True, t[1:-1]
    if t.endswith("-") and not t.startswith("-"):
        negative, t = True, t[:-1]
    t = t.replace(" ", "")
    match = re.fullmatch(
        rf"([+-]?){_CURRENCY_MARK}?([+-]?)(\d[\d,]*(?:\.\d+)?|\.\d+){_CURRENCY_MARK}?(?:元)?", t, re.IGNORECASE
    )
    if not match:
        raise ValueError(text)
    if match.group(1) and match.group(2):
        raise ValueError(text)
    digits = match.group(3)
    if "," in digits:
        if not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", digits):
            raise ValueError(text)
        digits = digits.replace(",", "")
    value = Decimal(digits)
    return -value if (negative or "-" in (match.group(1), match.group(2))) else value


def _date(text: str, formats: tuple[str, ...]) -> _dt.date:
    t = _cell(text)
    for candidate in (t, t.split(" ")[0] if " " in t else None):
        if not candidate:
            continue
        for fmt in formats:
            try:
                return _dt.datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    raise ValueError(text)


def _reader(text: str, delimiter: str) -> list[tuple[int, list[str]]]:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [(reader.line_num, [_cell(c) for c in row]) for row in reader]


def read_statement(path: Path, mapping: Mapping, *, encoding: str | None = None) -> ParsedStatement:
    data = path.read_bytes()
    text, used = _decode(data, encoding or mapping.encoding)
    wanted = [h for h in mapping.header if h]
    chosen = None
    for delimiter in ([mapping.delimiter] if mapping.delimiter else list(DELIMITERS)):
        rows = _reader(text, delimiter)
        for index, (_, cells) in enumerate(rows):
            if all(w in cells for w in wanted):
                chosen = (delimiter, rows, index)
                break
        if chosen:
            break
    if not chosen:
        raise LedgerError(
            f"could not find the header row (a row containing {wanted}); check the mapping with `import inspect`",
            code="invalid_statement", details={"header": wanted},
        )
    delimiter, rows, head = chosen
    header = rows[head][1]
    cols = mapping.columns

    def at(record: dict[str, str], key: str) -> str:
        return record.get(cols[key], "") if key in cols else ""

    for key, name in cols.items():
        if name not in header:
            raise LedgerError(f"column {name!r} ({key}) is not in the file's header {header}", code="invalid_statement")

    parsed: list[StatementRow] = []
    ignored: Counter = Counter()
    examples: list[str] = []
    problems: list[str] = []
    for line, cells in rows[head + 1:]:
        if not any(cells):
            continue
        filled = [c for c in cells if c]
        if len(cells) < len(header) and len(filled) <= 1:  # "共7笔记录": a summary line, not a transaction
            ignored["short line (summary or footer)"] += 1
            if len(examples) < 3:
                examples.append(f"line {line}: {filled[0]}")
            continue
        record = dict(zip(header, cells + [""] * (len(header) - len(cells))))
        if any(_cell(record.get(c)) in values for c, values in mapping.skip_when):
            ignored["skipped by [[skip]]"] += 1
            continue
        if not at(record, "date"):
            ignored["no date (summary or footer line)"] += 1
            continue
        try:
            day = _date(at(record, "date"), mapping.date_formats)
        except ValueError:
            problems.append(f"line {line}: cannot read the date {at(record, 'date')!r}")
            continue
        try:
            if "amount" in cols:
                value = _money(at(record, "amount"))
                if value is None:
                    ignored["no amount"] += 1
                    continue
                if mapping.sign == "unsigned":
                    which = at(record, "direction")
                    if which in mapping.direction_ignore:
                        ignored[f"direction {which!r}"] += 1
                        continue
                    if which not in mapping.direction_in + mapping.direction_out:
                        problems.append(f"line {line}: the direction {which!r} is neither in, out nor ignored in [direction]")
                        continue
                    value = abs(value) if which in mapping.direction_in else -abs(value)
                elif mapping.sign == "outflow_positive":
                    value = -value
            else:
                money_in = _money(at(record, "amount_in")) or Decimal(0)
                money_out = _money(at(record, "amount_out")) or Decimal(0)
                value = abs(money_in) - abs(money_out)
            balance = _money(at(record, "balance")) if "balance" in cols else None
        except ValueError:
            problems.append(f"line {line}: cannot read an amount in {cells}")
            continue
        if value == 0:
            ignored["zero amount"] += 1
            continue
        parsed.append(StatementRow(line, day, value, at(record, "payee"), at(record, "description"),
                                   at(record, "id"), balance, record))
    if problems:
        shown = problems[:10]
        raise LedgerError(
            f"{len(problems)} row(s) could not be read; nothing was imported. First ones: " + "; ".join(shown),
            code="invalid_statement", details={"problems": shown, "count": len(problems)},
        )
    return ParsedStatement(parsed, ignored, examples, rows[head][0] - 1, used, delimiter, rows[head][0],
                           hashlib.sha256(data).hexdigest(), path.name)


def row_hashes(rows: list[StatementRow], account: str, ccy: str) -> list[str]:
    """A stable id per row. Identical rows in one file get distinct ids by their order of appearance."""
    seen: Counter = Counter()
    out = []
    for row in rows:
        core = f"{account}|{row.id}|{row.amount}" if row.id else f"{account}|{row.date}|{row.amount}|{row.payee}|{row.description}"
        key = f"{core}|{ccy}"
        seen[key] += 1
        out.append("imp1-" + hashlib.sha256(f"{key}#{seen[key]}".encode()).hexdigest()[:32])
    return out


# ---------------------------------------------------------------- applying rules and importing


def match_rule(mapping: Mapping, row: StatementRow) -> Rule | None:
    for rule in mapping.rules:
        haystack = {"payee": row.payee, "description": row.description}.get(rule.field, f"{row.payee} {row.description}")
        if rule.pattern.search(haystack):
            return rule
    return None


def _statement_account(ledger: Ledger, account: str, when: _dt.date) -> str:
    res = resolve_account(ledger.state, ledger.aliases(), account, ("Assets", "Liabilities"), when)
    if not res.ok:
        raise LedgerError(
            f"cannot tell which account the statement belongs to: {res.message}", code="unresolved_account",
            details={"unresolved": [res.to_dict()]},
        )
    return res.account


def _statement_currency(ledger: Ledger, mapping: Mapping, account: str) -> str:
    if mapping.ccy:
        return mapping.ccy
    choice = choose_currency(
        text=None, flag=None, accounts=[(account, ledger.state.accounts[account].currencies)],
        used=ledger.state.currencies_in_use(), base=ledger.base_currency,
    )
    return choice.ccy


def _fmt(value: Decimal) -> str:
    return format(value, "f")


def run_import(
    ledger: Ledger,
    path: Path,
    mapping: Mapping,
    *,
    account: str,
    since: _dt.date | None = None,
    until: _dt.date | None = None,
    actor: tuple[str, str] = ("human", "user"),
    confidence: float | None = None,
    encoding: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    ledger.reload()
    if actor[0] == "agent" and confidence is None:
        raise LedgerError(
            "an agent must state a confidence (0 to 1) for an import: how sure it is that the mapping reads this file correctly",
            code="confidence_required",
        )
    parsed = read_statement(path, mapping, encoding=encoding)
    in_range = [r.date for r in parsed.rows if not ((since and r.date < since) or (until and r.date > until))]
    when = min(in_range, default=_dt.date.today())
    low_confidence = actor[0] == "agent" and confidence is not None and confidence < ledger.auto_post_confidence
    statement_account = _statement_account(ledger, account, when)
    ccy = _statement_currency(ledger, mapping, statement_account)
    live = ledger.state.live_import_hashes()
    hashes = row_hashes(parsed.rows, statement_account, ccy)
    aliases = ledger.aliases()
    source = {"type": "import", "ref": parsed.filename, "sha256": parsed.sha256}

    ignored = Counter(parsed.ignored)
    duplicates = out_of_range = 0
    events, planned = [], []
    unresolved: dict[str, dict[str, Any]] = {}
    for row, digest in zip(parsed.rows, hashes):
        if (since and row.date < since) or (until and row.date > until):
            out_of_range += 1
            continue
        if digest in live:
            duplicates += 1
            continue
        rule = match_rule(mapping, row)
        if rule is not None and rule.account is None:
            ignored["skipped by a rule"] += 1
            continue
        counter, note = None, None
        if rule is not None:
            res = resolve_account(ledger.state, aliases, rule.account, ROOTS, row.date)
            if res.ok:
                counter = res.account
            else:
                note = f"rule {rule.text!r}: {res.message}"
        label = row.payee or row.description or "(no payee)"
        status = "posted" if counter and not low_confidence else "pending"
        if not counter:
            slot = unresolved.setdefault(label, {"payee": label, "count": 0, "total": Decimal(0), "why": note})
            slot["count"] += 1
            slot["total"] += row.amount
        postings = [
            {"account": statement_account, "amount": _fmt(row.amount), "ccy": ccy},
            {"account": counter, "amount": _fmt(-row.amount), "ccy": ccy},
        ]
        meta = {"import": {"mapping": mapping.name, "line": row.line, "file": parsed.filename}}
        events.append(ledger.new_event(
            "txn", actor=actor, source=source, confidence=confidence, meta=meta, date=row.date.isoformat(),
            status=status, postings=postings, payee=row.payee or None, narration=row.description or None,
            import_hash=digest,
        ))
        planned.append(row)

    written = False
    if events and not dry_run:
        try:
            ledger.append_many(events, clamp_ts=True)
            written = True
        except LedgerError as err:
            match = re.search(r"event #(\d+)", err.where or "")
            if match:
                row = planned[int(match.group(1)) - 1]
                err.details = {**err.details, "row_line": row.line, "row_date": row.date.isoformat(),
                               "row_amount": _fmt(row.amount), "row_payee": row.payee}
                err.message = f"{err.message} (statement line {row.line}: {row.date} {row.amount} {row.payee or row.description}); nothing was imported. Use --since to import only newer rows."
            raise
    posted = sum(1 for e in events if e["status"] == "posted")
    return {
        "file": parsed.filename, "mapping": mapping.name, "account": statement_account, "ccy": ccy,
        "encoding": parsed.encoding, "delimiter": parsed.delimiter, "header_line": parsed.header_line,
        "rows_read": len(parsed.rows), "preamble_lines": parsed.preamble_lines,
        "imported": len(events), "posted": posted, "pending": len(events) - posted,
        "duplicates_skipped": duplicates, "out_of_range": out_of_range, "ignored": dict(ignored),
        "ignored_examples": parsed.ignored_examples,
        "unresolved_payees": sorted(
            ({**v, "total": _fmt(v["total"])} for v in unresolved.values()), key=lambda v: -v["count"]
        )[:25],
        "written": written, "dry_run": dry_run,
        "held_for_confidence": low_confidence,
    }


def recheck(ledger: Ledger, name: str, *, actor: tuple[str, str] = ("human", "user"), dry_run: bool = False) -> dict[str, Any]:
    """After a rule was added, confirm the pending rows of that mapping that now match a rule."""
    mapping = load_mapping(ledger, name)
    ledger.reload()
    aliases = ledger.aliases()
    events, resolved, left = [], 0, {}
    for rec in ledger.state.pending():
        info = rec.event.meta.get("import")
        if not isinstance(info, dict) or info.get("mapping") != mapping.name:
            continue
        missing = [i for i, p in enumerate(rec.postings) if p.account is None]
        if len(missing) != 1:
            continue
        pseudo = StatementRow(0, rec.date, Decimal(0), rec.event.payee or "", rec.event.narration or "", "", None, {})
        rule = match_rule(mapping, pseudo)
        counter = None
        if rule is not None and rule.account is not None:
            res = resolve_account(ledger.state, aliases, rule.account, ROOTS, rec.date)
            counter = res.account if res.ok else None
        if counter is None:
            label = rec.event.payee or rec.event.narration or "(no payee)"
            slot = left.setdefault(label, {"payee": label, "count": 0})
            slot["count"] += 1
            continue
        postings = [{"account": counter if i == missing[0] else p.account, "amount": _fmt(p.amount), "ccy": p.ccy}
                    for i, p in enumerate(rec.postings)]
        events.append(ledger.new_event("confirm", actor=actor, target=rec.id, postings=postings,
                                       meta={"recheck": {"mapping": mapping.name}}))
        resolved += 1
    if events and not dry_run:
        ledger.append_many(events, clamp_ts=True)
    return {"mapping": mapping.name, "resolved": resolved, "still_pending": sorted(left.values(), key=lambda v: -v["count"]),
            "written": bool(events) and not dry_run, "dry_run": dry_run}


# ---------------------------------------------------------------- inspecting a file to help write a mapping


_ROLE_WORDS = {  # specific roles first, so "收入金额" becomes amount_in and not amount
    "date": ("日期", "时间", "date", "time"),
    "amount_out": ("支出", "借方", "debit", "withdrawal", "付款金额"),
    "amount_in": ("收入", "贷方", "credit", "deposit", "收款金额"),
    "balance": ("余额", "balance"),
    "direction": ("收/支", "收支", "借贷"),
    "id": ("订单号", "流水号", "单号", "交易号", "reference"),
    "payee": ("对方", "商户", "收款方", "payee", "counterparty", "merchant"),
    "description": ("说明", "商品", "摘要", "备注", "用途", "description", "memo", "narrative", "详情"),
    "amount": ("金额", "amount", "发生额"),
}


def _looks_numeric(cell: str) -> bool:
    try:
        return _money(cell) is not None
    except ValueError:
        return False


def inspect_statement(path: Path, *, encoding: str | None = None, sample: int = 6) -> dict[str, Any]:
    data = path.read_bytes()
    text, used = _decode(data, encoding)
    best = None
    for delimiter in DELIMITERS:
        rows = _reader(text, delimiter)[:80]
        widths = Counter(len(c) for _, c in rows if any(c))
        if not widths:
            continue
        width, count = widths.most_common(1)[0]
        score = count * (width >= 3) * width
        if best is None or score > best[0]:
            best = (score, delimiter, width)
    if best is None or best[0] == 0:
        raise LedgerError("this does not look like a table with at least three columns", code="invalid_statement")
    _, delimiter, width = best
    rows = _reader(text, delimiter)

    def is_date(cell: str) -> bool:
        return bool(re.search(r"\d{4}[-/.年]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|^\d{8}$", cell))

    def has_data(cells: list[str]) -> bool:
        """A transaction row has a date and an amount somewhere in it."""
        return any(is_date(c) for c in cells) and any(_looks_numeric(c) and not is_date(c) for c in cells)

    head = None
    for index in range(len(rows) - 1):
        cells = rows[index][1]
        names_only = not any(is_date(c) or _looks_numeric(c) for c in cells)
        if len(cells) == width and len([c for c in cells if c]) >= 3 and names_only and has_data(rows[index + 1][1]):
            head = index
            break
    if head is None:
        raise LedgerError("could not find a header row followed by data rows", code="invalid_statement")
    header = rows[head][1]
    body = [cells for _, cells in rows[head + 1:] if len(cells) == width and any(cells)]
    samples = body[:sample]
    notes: list[str] = []
    columns: dict[str, str] = {}
    for role, words in _ROLE_WORDS.items():
        for name in header:
            if name and any(w in name.casefold() for w in words) and name not in columns.values():
                if role == "date" or role not in columns:
                    columns[role] = name
                    break
    date_format = None
    if "date" in columns:
        index = header.index(columns["date"])
        cells = [c[index] for c in body[:30] if c[index]]
        fits = [f for f in DATE_FORMATS if cells and all(_try_date(c, f) for c in cells)]
        if any(a in fits and b in fits for a, b in DAY_OR_MONTH_FIRST):
            notes.append("the dates could be day-first or month-first (for example 03/04/2026); say which one it is, it is not guessed")
        elif fits:
            date_format = fits[0]
        else:
            notes.append("could not work out the date format from the sample")
    sign = None
    if "amount" in columns:
        index = header.index(columns["amount"])
        values = [_try_money(c[index]) for c in body[:50] if c[index]]
        if "direction" in columns:
            sign = "unsigned"
            notes.append("there is a direction column: list its values for money in and money out under [direction]")
        elif any(v is not None and v < 0 for v in values):
            notes.append("amounts have both signs: confirm with a transaction you know which sign means money INTO this account; credit-card statements are often the opposite")
        else:
            notes.append("all amounts have the same sign: say whether they are money in or money out")
    elif "amount_in" in columns or "amount_out" in columns:
        sign = "inflow_positive"
    suggested = {"columns": columns, "date_format": date_format, "sign": sign}
    return {
        "file": path.name, "encoding": used, "delimiter": delimiter, "header_line": rows[head][0],
        "preamble_lines": rows[head][0] - 1, "columns_in_file": header,
        "sample_rows": [dict(zip(header, s)) for s in samples], "rows": len(body),
        "suggested": suggested, "notes": notes,
        "note": "a suggestion to check with the user, not a decision",
    }


def _try_date(cell: str, fmt: str) -> bool:
    try:
        _dt.datetime.strptime(cell.split(" ")[0] if " " in cell and " " not in fmt else cell, fmt)
        return True
    except ValueError:
        return False


def _try_money(cell: str) -> Decimal | None:
    try:
        return _money(cell)
    except ValueError:
        return None
