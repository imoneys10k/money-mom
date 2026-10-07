"""Command line interface.

Every command prints readable text by default and a single JSON document with `--json`:
``{"ok": true, "data": ...}`` or ``{"ok": false, "error": {...}}``. Exit codes: 0 success,
1 the ledger refused or could not do it, 2 the command line itself was wrong.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .cache import open_cache, run_query
from .currency import choose_currency, parse_currency, parse_money
from .doctor import run_doctor
from .dupes import DEFAULT_WINDOW_DAYS, candidates_involving, find_candidates, resolve_pair
from .errors import LedgerError
from .events import ROOTS
from .intents import SPECS, postings_with_slots, record_exchange, record_intent, resolve_account
from .importing import (
    _money, add_rule, inspect_statement, list_mappings, load_mapping, read_statement, recheck, run_import, save_mapping,
)
from .ledger import TONES, Ledger
from .rates import convert_balances, fetch_frankfurter, update_rates
from .reconcile import reconcile, rows_from_json, seal
from .charts import FORMATS, KINDS, LANGS, chart_data, render
from .alerts import find_alerts
from .report import monthly_report, monthly_series
from .templates import TEMPLATES, apply_template

DEFAULT_HOME = "~/MoneyMom"
JSON_TXN_FIELDS = {"date", "status", "narration", "payee", "postings", "import_hash", "source", "confidence", "meta"}


class UsageError(LedgerError):
    code = "usage_error"


def _today() -> str:
    return dt.date.today().isoformat()


def _fmt(amount: Decimal) -> str:
    return format(amount, "f")


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _ledger_path(args: argparse.Namespace) -> Path:
    return Path(args.ledger or os.environ.get("MONEY_MOM_HOME") or DEFAULT_HOME).expanduser()


def _actor(args: argparse.Namespace) -> tuple[str, str]:
    raw = args.actor or os.environ.get("MONEY_MOM_ACTOR") or "human:user"
    kind, sep, name = raw.partition(":")
    if not sep or kind not in ("human", "agent") or not name.strip():
        raise UsageError(f"--actor must look like human:NAME or agent:NAME, got {raw!r}")
    return kind, name


def _parse_posting(spec: str) -> dict[str, str | None]:
    parts = spec.rsplit(None, 2)
    if len(parts) != 3:
        raise UsageError(f'posting {spec!r} must be "ACCOUNT AMOUNT CCY", e.g. "Expenses:Dining 38.00 CNY"')
    account, amount, ccy = parts
    return {"account": None if account == "?" else account, "amount": amount, "ccy": ccy}


def _source(args: argparse.Namespace) -> dict[str, str] | None:
    if not (args.source_type or args.source_ref or args.source_sha256):
        return None
    if not args.source_type:
        raise UsageError("--source-ref and --source-sha256 need --source-type")
    out = {"type": args.source_type}
    if args.source_ref:
        out["ref"] = args.source_ref
    if args.source_sha256:
        out["sha256"] = args.source_sha256
    return out


def _load_json(path: str) -> Any:
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except OSError as err:
        raise UsageError(f"cannot read {path}: {err.strerror or err}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise UsageError(f"{path} is not valid JSON: {err}") from None


def _override_meta(args: argparse.Namespace, meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    merged = dict(meta or {})
    if getattr(args, "override_lock", None):
        merged["override_lock"] = args.override_lock
    return merged or None


def _txn_summary(ledger: Ledger, event_id: str) -> dict[str, Any]:
    rec = ledger.state.txns[event_id]
    return {"id": rec.id, "date": rec.date.isoformat(), "status": rec.status}


def _postings_json(postings) -> list[dict[str, Any]]:
    return [{"account": p.account, "amount": _fmt(p.amount), "ccy": p.ccy} for p in postings]


def _width(text: str) -> int:
    """Terminal display width: CJK characters take two columns."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _ljust(text: str, width: int) -> str:
    return text + " " * (width - _width(text))


def _rjust(text: str, width: int) -> str:
    return " " * (width - _width(text)) + text


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    cells = [[("NULL" if v is None else str(v)) for v in row] for row in rows]
    widths = [max([_width(h)] + [_width(r[i]) for r in cells]) for i, h in enumerate(headers)]
    lines = ["  ".join(_ljust(h, w) for h, w in zip(headers, widths)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    lines += ["  ".join(_ljust(c, w) for c, w in zip(row, widths)).rstrip() for row in cells]
    return "\n".join(lines)


# ---- commands: each returns (data, human_text) -----------------------------------------


def cmd_init(args: argparse.Namespace) -> tuple[Any, str]:
    root = _ledger_path(args)
    ledger = Ledger.init(root, base_currency=args.base_currency, tone=args.tone)
    opened = 0
    if args.template != "none":
        opened = apply_template(ledger, args.template, args.date or _today(), _actor(args))
    data = {
        "path": str(root), "base_currency": args.base_currency, "tone": args.tone,
        "template": args.template, "accounts_opened": opened,
    }
    text = f"Created a Money Mom ledger at {root}"
    if opened:
        text += f"\nOpened {opened} accounts from the {args.template!r} template"
    else:
        text += "\nNo accounts yet. Start from a template with --template (" + ", ".join(sorted(TEMPLATES)) + "), or use `money-mom open`."
    return data, text


def cmd_open(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    ev = ledger.open_account(
        args.account, args.date or _today(), currencies=args.currency or None,
        actor=_actor(args), meta=_override_meta(args),
    )
    return {"id": ev.id, "account": args.account}, f"Opened {args.account}"


def cmd_close(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    ev = ledger.close_account(args.account, args.date or _today(), actor=_actor(args))
    return {"id": ev.id, "account": args.account}, f"Closed {args.account}"


def _txn_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    flags = {
        "date": args.date, "status": args.status, "narration": args.narration, "payee": args.payee,
        "import_hash": args.import_hash,
    }
    if args.from_json is not None:
        if args.posting or any(v is not None for v in flags.values()):
            raise UsageError("--from-json cannot be combined with --posting/--date/--status/--narration/--payee/--import-hash")
        data = _load_json(args.from_json)
        items = data if isinstance(data, list) else [data]
        if not items or not all(isinstance(i, dict) for i in items):
            raise UsageError("--from-json must contain a transaction object or a non-empty list of them")
        for item in items:
            unknown = set(item) - JSON_TXN_FIELDS
            if unknown:
                raise UsageError(f"unknown transaction field(s) {sorted(unknown)}; allowed: {sorted(JSON_TXN_FIELDS)}")
        return [dict(i) for i in items]
    if not args.posting:
        raise UsageError("give at least two --posting values, or use --from-json")
    item = {k: v for k, v in flags.items() if v is not None}
    item["postings"] = [_parse_posting(p) for p in args.posting]
    return [item]


def cmd_add(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    actor = _actor(args)
    flag_source = _source(args)
    events = []
    for item in _txn_items(args):
        item.setdefault("date", _today())
        item.setdefault("status", "posted")
        meta = item.pop("meta", None)
        if meta is not None and not isinstance(meta, dict):
            raise UsageError("meta must be an object")
        source = item.pop("source", None) or flag_source
        confidence = item.pop("confidence", None)
        if confidence is None:
            confidence = args.confidence
        events.append(
            ledger.new_event(
                "txn", actor=actor, source=source, confidence=confidence,
                meta=_override_meta(args, meta), **item,
            )
        )
    written = ledger.append_many(events, clamp_ts=True)
    data = [_txn_summary(ledger, e.id) for e in written]
    lines = [f"Recorded {d['status']} transaction {d['id']} dated {d['date']}" for d in data]
    hints = _dupe_hint(ledger, [e.id for e in written])
    if hints:
        for d in data:
            d["duplicate_candidates"] = [h for h in hints if d["id"] in h["ids"]]
        lines += ["  This may duplicate something already recorded:"] + _dupe_lines(hints) + [_DUPE_HOW]
    return data, "\n".join(lines)


def cmd_confirm(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    slots = {s: getattr(args, f"slot_{s}", None) for s in ("from", "to", "category")}
    named = {s: t for s, t in slots.items() if t}
    if named and args.posting:
        raise UsageError("use either --posting or slot names (--from/--to/--category), not both")
    if named:
        postings = postings_with_slots(ledger, args.target, named)
    else:
        postings = [_parse_posting(p) for p in args.posting] if args.posting else None
    ev = ledger.append(
        ledger.new_event(
            "confirm", actor=_actor(args), meta=_override_meta(args), target=args.target, postings=postings
        ),
        clamp_ts=True,
    )
    return {"id": ev.id, "target": args.target}, f"Confirmed {args.target}"


def cmd_void(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    ev = ledger.void(args.target, args.reason, actor=_actor(args), meta=_override_meta(args))
    return {"id": ev.id, "target": args.target}, f"Voided {args.target}"


def cmd_assert(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    ev = ledger.assert_balance(
        args.account, args.date or _today(), args.amount, args.ccy, actor=_actor(args)
    )
    return (
        {"id": ev.id, "account": args.account, "amount": args.amount, "ccy": args.ccy},
        f"Balance of {args.account} is {args.amount} {args.ccy} as asserted",
    )


def _intent_text(result) -> str:
    verb = {"posted": "Recorded", "pending": "Pending"}[result.status]
    what = f"{result.amount} {result.ccy}"
    if result.details:  # an exchange: show both sides
        what = f"{result.details['give']['amount']} {result.details['give']['ccy']} for {result.details['get']['amount']} {result.details['get']['ccy']}"
    if result.written:
        head = f"{verb} {result.kind} of {what} ({result.event.id})"
    else:
        head = f"Dry run, nothing written: would record a {result.status} {result.kind} of {what}"
    lines = [head]
    lines += [
        f"    {_ljust(p['account'] or '?', 28)} {_rjust(p['amount'], 12)} {p['ccy']}" for p in result.postings
    ]
    if result.reasons:
        lines.append("  Needs your confirmation:" if result.written else "  Would need confirmation:")
        lines += [f"    - {r}" for r in result.reasons]
        for u in result.unresolved:
            if u["candidates"]:
                lines.append(f"    candidates for {u['slot']}: {', '.join(u['candidates'])}")
        if result.written:
            slots = [u["slot"] for u in result.unresolved]
            fix = " ".join(f"--{slot} NAME" for slot in slots) if slots else ""
            lines.append(
                f"  Fill it in: money-mom confirm {result.event.id} {fix}".rstrip()
                if slots else f"  Accept it as is: money-mom confirm {result.event.id}"
            )
            lines.append(f"  Or drop it: money-mom void {result.event.id} --reason ...")
    return "\n".join(lines)


def _intent_handler(kind: str) -> Callable[[argparse.Namespace], tuple[Any, str]]:
    def handler(args: argparse.Namespace) -> tuple[Any, str]:
        ledger = Ledger.open(_ledger_path(args))
        result = record_intent(
            ledger, kind, amount=args.amount,
            slots={slot: getattr(args, f"slot_{slot}", None) for slot in SPECS[kind]},
            date=args.date or _today(), ccy=args.ccy, payee=args.payee, narration=args.narration,
            confidence=args.confidence, source=_source(args), import_hash=args.import_hash,
            actor=_actor(args), strict=args.strict, dry_run=args.dry_run, meta=_override_meta(args),
        )
        data, text = result.to_dict(), _intent_text(result)
        if result.written:
            hints = _dupe_hint(ledger, [result.event.id])
            if hints:
                data["duplicate_candidates"] = hints
                text += "\n  This may duplicate something already recorded:\n" + "\n".join(_dupe_lines(hints)) + "\n" + _DUPE_HOW
        return data, text

    return handler


_PLAIN_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _split_exchange(parts: list[str]) -> tuple[str, str]:
    """`100 USD 720 CNY`, `'100 USD' '720 CNY'`, `100 USD 720` and `100 720 CNY` all mean the same kind of thing."""
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 4:
        return f"{parts[0]} {parts[1]}", f"{parts[2]} {parts[3]}"
    if len(parts) == 3:
        first, middle, last = parts
        if not _PLAIN_NUMBER.fullmatch(middle):
            return f"{first} {middle}", last
        if not _PLAIN_NUMBER.fullmatch(last):
            return first, f"{middle} {last}"
    raise UsageError(
        "give the two amounts, each optionally followed by its currency: 100 USD 720 CNY, '100 USD' '720 CNY', "
        "or 100 720 with --give-ccy and --get-ccy"
    )


def cmd_exchange(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    give, get = _split_exchange(args.parts)
    result = record_exchange(
        ledger, give=give, get=get, slots={"from": args.slot_from, "to": args.slot_to},
        date=args.date or _today(), give_ccy=args.give_ccy, get_ccy=args.get_ccy, payee=args.payee,
        narration=args.narration, confidence=args.confidence, source=_source(args), import_hash=args.import_hash,
        actor=_actor(args), strict=args.strict, dry_run=args.dry_run, meta=_override_meta(args),
    )
    return result.to_dict(), _intent_text(result)


def cmd_alias(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    if args.alias_command == "add":
        ledger.set_alias(args.alias, args.account)
        return {"alias": args.alias, "account": args.account}, f"{args.alias} now means {args.account}"
    if args.alias_command == "remove":
        ledger.remove_alias(args.alias)
        return {"alias": args.alias}, f"Removed alias {args.alias}"
    mapping = ledger.aliases()
    rows = [[a, t] for a, t in sorted(mapping.items())]
    return mapping, _table(["alias", "account"], rows) if rows else "No aliases yet."


_TYPE_ROOTS = {"asset": "Assets", "liability": "Liabilities", "equity": "Equity", "income": "Income", "expense": "Expenses"}


def cmd_resolve(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    roots = tuple(_TYPE_ROOTS[t] for t in args.type) if args.type else ROOTS
    when = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    res = resolve_account(ledger.state, ledger.aliases(), args.term, roots, when)
    if res.ok:
        text = f"{args.term} -> {res.account} (by {res.matched_by})"
    else:
        text = f"Cannot resolve {args.term!r}: {res.message}"
        if res.candidates:
            text += "\n  candidates: " + ", ".join(res.candidates)
    return res.to_dict(), text


def cmd_doctor(args: argparse.Namespace) -> tuple[Any, str]:
    data = run_doctor(_ledger_path(args))
    lines = [f"money-mom {data['version']}  ledger: {data['path']}"]
    lines += [f"  [{'ok' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}" for c in data["checks"]]
    info = data["ledger"]
    if info:
        lines.append(
            f"  {info['accounts']} accounts, {info['posted']} posted, {info['pending']} pending; "
            f"base currency {info['base_currency']}, tone {info['tone']}, "
            f"auto-post confidence {info['auto_post_confidence']:g}"
        )
    if data["next_step"]:
        lines.append("  " + data["next_step"])
    lines.append("Healthy." if data["ok"] else "Problems found.")
    return data, "\n".join(lines)


def cmd_currency(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    money = parse_money(args.text)
    flag = parse_currency(args.ccy) if args.ccy else ()
    when = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    aliases, accounts = ledger.aliases(), []
    for name in args.account or []:
        res = resolve_account(ledger.state, aliases, name, ROOTS, when)
        if not res.ok:
            raise LedgerError(f"cannot resolve account {name!r}: {res.message}", code="unresolved_account",
                              details={"unresolved": [res.to_dict()]})
        accounts.append((res.account, ledger.state.accounts[res.account].currencies))
    choice = choose_currency(
        text=money, flag=flag or None, accounts=accounts,
        used=ledger.state.currencies_in_use(), base=ledger.base_currency,
    )
    data = {
        "amount": money.amount, "ccy": choice.ccy, "matched_by": choice.matched_by, "inferred": choice.inferred,
        "text": money.symbol, "candidates": list(money.candidates), "message": choice.message,
    }
    how = {"flag": "from --ccy", "text": "from the text", "account": "from the account",
           "ledger": "from your ledger", "default": "the base currency"}[choice.matched_by]
    text = f"{money.amount} {choice.ccy} ({how})" + (" - needs confirmation" if choice.inferred else "")
    return data, text


def _import_text(data: dict[str, Any]) -> str:
    verb = "Would import" if data["dry_run"] else "Imported"
    lines = [
        f"{verb} {data['imported']} of {data['rows_read']} rows from {data['file']} into {data['account']} "
        f"({data['ccy']}), mapping {data['mapping']!r}:",
        f"  posted {data['posted']}, pending {data['pending']}"
        + (" (all held: the confidence is below the auto-post threshold)" if data["held_for_confidence"] else
           " (no rule matched yet)" if data["pending"] else ""),
    ]
    skipped = []
    if data["duplicates_skipped"]:
        skipped.append(f"{data['duplicates_skipped']} already imported")
    if data["out_of_range"]:
        skipped.append(f"{data['out_of_range']} outside --since/--until")
    skipped += [f"{n} {why}" for why, n in data["ignored"].items()]
    if skipped:
        lines.append("  not imported: " + ", ".join(skipped))
    lines += [f"    e.g. {example}" for example in data["ignored_examples"]]
    if data["held_for_duplicates"]:
        lines.append(
            f"  {data['held_for_duplicates']} row(s) look like payments the ledger already has, so they are held as pending "
            "(not counted in any balance) until the user says whether each is the same payment:"
        )
        for row in data["possible_duplicates"][:10]:
            other = row["matches"][0]
            label = row["payee"] or row["description"] or ""
            lines.append(f"    [{row['tier']}] line {row['line']}  {row['date']}  {row['amount']}  {label}  ~  {other['id']} ({other['account']}, {other['source']})")
        lines.append("  Run `money-mom dupes`, show them to the user, then record their answer with `money-mom dupes resolve`.")
    if data["unresolved_payees"]:
        lines.append("  Ask the user a category for these payees; each answer becomes a rule:")
        lines += [f"    {u['payee']}  x{u['count']}  ({u['total']})" for u in data["unresolved_payees"][:10]]
        lines.append(f"  then `money-mom import rule-add --map {data['mapping']} --match TEXT --account ACCOUNT` and `money-mom import recheck --map {data['mapping']}`.")
    return "\n".join(lines)


def cmd_import(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    action = args.import_command
    if action == "inspect":
        info = inspect_statement(Path(args.file).expanduser(), encoding=args.encoding)
        suggested = info["suggested"]
        lines = [
            f"{info['file']}: {info['rows']} data rows, encoding {info['encoding']}, delimiter {info['delimiter']!r}, "
            f"header on line {info['header_line']} ({info['preamble_lines']} lines before it)",
            "columns: " + " | ".join(info["columns_in_file"]),
        ]
        for role, name in suggested["columns"].items():
            lines.append(f"  looks like {role}: {name}")
        lines.append(f"  date format: {suggested['date_format'] or 'unclear'}   sign: {suggested['sign'] or 'unclear'}")
        lines += [f"  note: {n}" for n in info["notes"]]
        rows = info["sample_rows"]
        if rows:
            lines.append(_table(list(rows[0]), [list(r.values()) for r in rows]))
        lines.append("This is a suggestion to check with the user. Save the mapping with `money-mom import save-map NAME FILE`.")
        return info, "\n".join(lines)
    if action == "save-map":
        text = sys.stdin.read() if args.file == "-" else Path(args.file).expanduser().read_text(encoding="utf-8")
        path = save_mapping(ledger, args.name, text)
        return {"name": args.name, "path": str(path)}, f"Saved mapping {args.name!r}"
    if action == "maps":
        names = list_mappings(ledger)
        return names, "\n".join(names) if names else "No mappings saved yet."
    if action == "run":
        mapping = load_mapping(ledger, args.map)
        data = run_import(
            ledger, Path(args.file).expanduser(), mapping, account=args.account,
            since=dt.date.fromisoformat(args.since) if args.since else None,
            until=dt.date.fromisoformat(args.until) if args.until else None,
            actor=_actor(args), confidence=args.confidence, encoding=args.encoding, dry_run=args.dry_run,
        )
        return data, _import_text(data)
    if action == "rule-add":
        if bool(args.account) == bool(args.skip):
            raise UsageError("give exactly one of --account or --skip")
        label = add_rule(ledger, args.map, match=args.match, account=args.account, field_name=args.field, regex=args.regex)
        return {"mapping": args.map, "rule": label}, f"Added {label} to {args.map!r}: {args.match!r} -> {args.account or 'skip'}"
    if action == "rule-list":
        mapping = load_mapping(ledger, args.map)
        rules = [{"match": r.text, "field": r.field, "regex": r.regex, "account": r.account, "skip": r.account is None}
                 for r in mapping.rules]
        rows = [[str(i + 1), r["match"], r["field"], "regex" if r["regex"] else "text", r["account"] or "(skip)"] for i, r in enumerate(rules)]
        return rules, _table(["#", "match", "field", "kind", "account"], rows) if rows else "No rules yet."
    data = recheck(ledger, args.map, actor=_actor(args), dry_run=args.dry_run)
    text = f"{'Would resolve' if data['dry_run'] else 'Resolved'} {data['resolved']} pending row(s) with the rules of {data['mapping']!r}."
    if data["still_pending"]:
        text += "\n  Still waiting: " + ", ".join(f"{u['payee']} x{u['count']}" for u in data["still_pending"][:10])
    return data, text


def _reconcile_text(report: dict[str, Any]) -> str:
    lines = [
        f"Reconciling {report['account']} ({report['ccy']}) {report['from']} to {report['to']}: "
        f"{report['statement_rows']} statement rows, {report['ledger_entries']} ledger entries",
        f"  matched {report['matched']} ({report['matched_by_import_hash']} by import hash)",
    ]

    def listing(title: str, entries: list[dict[str, Any]], line) -> None:
        if entries:
            lines.append(f"  {title} ({len(entries)}):")
            lines.extend(f"    {line(e)}" for e in entries[:20])
            if len(entries) > 20:
                lines.append(f"    ... and {len(entries) - 20} more")

    listing("on the statement but NOT in the ledger", report["missing_in_ledger"],
            lambda e: f"line {e['line']}  {e['date']}  {e['amount']}  {e['payee'] or e['description']}"
                      + ("  <- the same charge is already matched once: a possible double charge" if e["possible_double_charge"] else ""))
    listing("in the ledger but NOT on the statement", report["missing_in_statement"],
            lambda e: f"{e['id']}  {e['date']}  {e['amount']}  {e['text']}")
    listing("same item, different amount", report["amount_mismatches"],
            lambda e: f"{e['statement']['date']}  statement {e['statement']['amount']} vs ledger {e['ledger']['amount']}  (difference {e['difference']})  {e['ledger']['text']}")
    listing("waiting as pending (not counted)", report["pending_in_range"],
            lambda e: f"{e['id']}  {e['date']}  {e['amount']}  {e['text']}")
    balance = report["balance"]
    if balance:
        note = " (the differences listed above explain it)" if balance["explained_by_the_differences_listed"] else ""
        lines.append(f"  closing balance on {balance['date']}: statement {balance['statement']}, ledger {balance['ledger']}, "
                     f"difference {balance['difference']}{note}")
    lines.append("  Clean: everything matches." if report["clean"] else "  Not clean yet.")
    if report["missing_in_ledger"]:
        lines.append("  Add the missing rows with `money-mom import run`, or record them by hand.")
    if report["can_assert"]:
        lines.append("  Seal it with --assert to lock this period.")
    if "sealed" in report:
        lines.append(f"  Sealed: {report['sealed']['account']} is {report['sealed']['balance']} on {report['sealed']['date']} and that period is now locked.")
    return "\n".join(lines)


def cmd_reconcile(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    if bool(args.from_json) == bool(args.file):
        raise UsageError("give either FILE with --map, or --from-json (rows an agent read from a PDF or screenshot)")
    if args.file:
        if not args.map:
            raise UsageError("a statement file needs --map, the mapping that describes it")
        mapping = load_mapping(ledger, args.map)
        parsed = read_statement(Path(args.file).expanduser(), mapping, encoding=args.encoding)
        rows, ccy = parsed.rows, mapping.ccy
    else:
        rows, ccy = rows_from_json(_load_json(args.from_json)), None
    closing, closing_date = None, None
    if args.closing_balance is not None:
        closing = _money(args.closing_balance)
        closing_date = dt.date.fromisoformat(args.closing_date) if args.closing_date else None
    elif args.closing_from_statement:
        with_balance = [r for r in rows if r.balance is not None]
        if not with_balance:
            raise UsageError("the statement has no balance column (add `balance` under [columns] in the mapping), so give --closing-balance and --closing-date")
        last = max(with_balance, key=lambda r: (r.date, r.line))
        closing, closing_date = last.balance, last.date
    if closing is not None and closing_date is None:
        raise UsageError("give --closing-date with --closing-balance")
    report = reconcile(
        ledger, args.account, rows, ccy=ccy,
        since=dt.date.fromisoformat(args.since) if args.since else None,
        until=dt.date.fromisoformat(args.until) if args.until else None,
        tolerance_days=args.tolerance_days, closing_balance=closing, closing_date=closing_date,
    )
    if args.seal:
        report["sealed"] = seal(ledger, report, actor=_actor(args))
    return report, _reconcile_text(report)


def cmd_check(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    problems = ledger.check() + list(ledger.state.chain_problems)
    state = ledger.state
    data = {
        "ok": not problems,
        "events": len(state.events),
        "transactions": sum(1 for r in state.txns.values() if r.status == "posted"),
        "pending": len(state.pending()),
        "problems": [{"code": p.code, "message": p.message, "event_id": p.event_id} for p in problems],
    }
    head = (
        f"{'OK' if not problems else 'PROBLEMS'}: {data['events']} events, "
        f"{data['transactions']} posted transactions, {data['pending']} pending"
    )
    body = "\n".join(f"  [{p.code}] {p.message}" for p in problems)
    return data, head + ("\n" + body if body else "")


def _converted_text(result: dict[str, Any]) -> str:
    rows = [
        [r["account"], r["amount"], r["ccy"], r["converted"] if r["converted"] is not None else "no rate",
         f"{r['rate']} ({r['rate_date']}{', STALE' if r['stale'] else ''})" if r["rate"] and r["via"] != "identity" else ""]
        for r in result["balances"]
    ]
    text = _table(["account", "amount", "ccy", f"in {result['in']}", "rate"], rows) if rows else "No balances."
    if result["total"] is not None:
        text += f"\nTotal in {result['in']}: {result['total']}" + (" (PARTIAL)" if result["partial"] else "")
    else:
        text += "\n  No total: all accounts together always net to zero. Use --account Assets, or `money-mom networth`."
    if result["missing"]:
        text += f"\n  No rate for {', '.join(result['missing'])}: left out. Run `money-mom rates update`."
    if result["stale"]:
        text += f"\n  Rates for {', '.join(result['stale'])} are more than 7 days old."
    return text


def cmd_balance(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    if args.target_ccy:
        target = parse_currency(args.target_ccy)
        if len(target) != 1:
            raise UsageError(f"--in needs one currency, such as CNY or 人民币; got {args.target_ccy!r}")
        prefix = args.account
        kept = {
            key: amount for key, amount in ledger.state.balances(as_of).items()
            if (not prefix or key[0] == prefix or key[0].startswith(prefix + ":")) and (amount != 0 or args.all)
        }
        result = convert_balances(ledger.state, kept, target[0], as_of or dt.date.today(), pivot=ledger.base_currency)
        if not prefix:  # every account together always sums to zero, so a total would mean nothing
            result["total"] = None
            result["note"] = "total omitted: all accounts together always net to zero; filter with --account or use networth"
        return result, _converted_text(result)
    prefix = args.account
    rows = []
    for (account, ccy), amount in sorted(ledger.state.balances(as_of).items()):
        if prefix and not (account == prefix or account.startswith(prefix + ":")):
            continue
        if amount == 0 and not args.all:
            continue
        rows.append({"account": account, "ccy": ccy, "amount": _fmt(amount)})
    text = _table(["account", "amount", "ccy"], [[r["account"], r["amount"], r["ccy"]] for r in rows]) if rows else "No balances."
    return {"as_of": args.as_of, "balances": rows}, text


def cmd_networth(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    target = parse_currency(args.target_ccy) if args.target_ccy else (ledger.base_currency,)
    if len(target) != 1:
        raise UsageError(f"--in needs one currency, such as CNY or 人民币; got {args.target_ccy!r}")
    totals: dict[str, Decimal] = {}
    for (account, ccy), amount in ledger.state.balances(as_of).items():
        if account.split(":", 1)[0] in ("Assets", "Liabilities"):
            totals[ccy] = totals.get(ccy, Decimal(0)) + amount
    result = convert_balances(
        ledger.state, {("net worth", c): a for c, a in totals.items()}, target[0],
        as_of or dt.date.today(), pivot=ledger.base_currency,
    )
    result["by_currency"] = {c: format(a, "f") for c, a in sorted(totals.items())}
    rows = [
        [r["ccy"], r["amount"], r["converted"] if r["converted"] is not None else "no rate",
         f"{r['rate']} ({r['rate_date']}{', STALE' if r['stale'] else ''})" if r["rate"] and r["via"] != "identity" else ""]
        for r in result["balances"]
    ]
    text = f"Net worth (assets minus liabilities), in {result['in']}\n" + (
        _table(["ccy", "amount", f"in {result['in']}", "rate"], rows) if rows else "No balances."
    )
    text += f"\nTotal: {result['total']} {result['in']}" + (" (PARTIAL)" if result["partial"] else "")
    if result["missing"]:
        text += f"\n  No rate for {', '.join(result['missing'])}: left out. Run `money-mom rates update`."
    return result, text


def _signed(text: str | None) -> str:
    if text is None:
        return "-"
    return text if text.startswith("-") or Decimal(text) == 0 else "+" + text


def _report_text(r: dict[str, Any]) -> str:
    ccy = r["in"]
    head = f"{r['month']}" + (f"  (month to date, until {r['as_of']})" if r["in_progress"] else "")
    lines = [f"Monthly report {head}, in {ccy}", ""]
    partial = " (PARTIAL)" if r["partial"] else ""
    lines.append(f"Income    {r['income']['total']:>12} {ccy}{partial}")
    lines.append(f"Spending  {r['expenses']['total']:>12} {ccy}{partial}")
    lines.append(f"Net       {_signed(r['net']):>12} {ccy}{partial}")
    if r["savings_rate_percent"] is not None:
        lines.append(f"Saved     {r['savings_rate_percent']:>11}% of income")
    prev = r["previous"]
    if prev["has_data"] and prev.get("expenses_change") is not None:
        pct = f" ({_signed(prev['expenses_change_percent'])}%)" if prev["expenses_change_percent"] is not None else ""
        lines.append(f"Spending vs {prev['month']}: {_signed(prev['expenses_change'])} {ccy}{pct}")
    if r["categories"]:
        lines += ["", "Where it went"]
        rows = [
            [c["category"], c["converted"] if c["converted"] is not None else "no rate",
             "" if c["share_percent"] is None else f"{c['share_percent']}%",
             _signed(c["change"]) if c["change"] is not None else ""]
            for c in r["categories"]
        ]
        lines.append(_table(["category", ccy, "share", f"vs {prev['month']}"], rows))
    if prev["dropped_categories"]:
        lines.append(f"Spent on in {prev['month']} but not this month: {', '.join(prev['dropped_categories'])}")
    if r["top_spending"]:
        lines += ["", "Biggest items"]
        lines.append(_table(
            ["date", "what", "amount", "ccy"],
            [[t["date"], t["payee"] or t["narration"] or t["account"], t["amount"], t["ccy"]] for t in r["top_spending"]],
        ))
    worth = r["net_worth"]
    change = f" ({_signed(worth['change'])})" if worth["change"] is not None else ""
    lines += ["", f"Net worth {worth['end']['total']} {ccy} on {worth['end']['as_of']}{change}"
              + (" PARTIAL" if worth["end"]["partial"] or worth["start"]["partial"] else "")]
    if r["checks"]["accounts_with_assertion"]:
        lines.append(f"Balance checked this month: {', '.join(r['checks']['accounts_with_assertion'])}")
    lines += [""] + [f"Note: {n}" for n in r["notes"]]
    return "\n".join(lines).rstrip()


def cmd_report(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    target = None
    if args.target_ccy:
        named = parse_currency(args.target_ccy)
        if len(named) != 1:
            raise UsageError(f"--in needs one currency, such as CNY or 人民币; got {args.target_ccy!r}")
        target = named[0]
    if args.top < 0:
        raise UsageError("--top must not be negative")
    result = monthly_report(ledger, args.month, target=target, top=args.top)
    return result, _report_text(result)


def _alerts_text(r: dict[str, Any]) -> str:
    lines = [f"Subscriptions and anomalies, {r['month']} (as of {r['as_of']}), in {r['in']}", ""]
    subs = r["subscriptions"]
    if subs:
        lines.append("Recurring charges")
        rows = [[s["payee"], s["cadence"], s["latest"], s["ccy"], "fixed" if s["amount_is_steady"] else "varies",
                 s["last_date"], s["next_expected"], s["status"] + (f" ({s['days_late']}d)" if s["status"] == "overdue" else "")]
                for s in subs]
        lines.append(_table(["payee", "every", "latest", "ccy", "amount", "last", "next", "status"], rows))
        total = r["subscription_total"]
        lines.append(f"About {total['per_month']} {r['in']} a month across {total['count']} active"
                     + (" (PARTIAL: missing " + ", ".join(total["missing"]) + ")" if total["partial"] else "")
                     + (f"; {total['varying']} of them vary in amount, so that is an estimate" if total["varying"] else ""))
    else:
        lines.append("No recurring charges recognised.")
    lines += ["", "Worth a look" if r["alerts"] else "Nothing stands out this month."]
    lines += [f"  [{a['type']}] {a['summary']}" for a in r["alerts"]]
    lines += [""] + [f"Note: {n}" for n in r["notes"]]
    return "\n".join(lines).rstrip()


def cmd_alerts(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    target = None
    if args.target_ccy:
        named = parse_currency(args.target_ccy)
        if len(named) != 1:
            raise UsageError(f"--in needs one currency, such as CNY or 人民币; got {args.target_ccy!r}")
        target = named[0]
    result = find_alerts(ledger, args.month, target=target)
    return result, _alerts_text(result)


def cmd_chart(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    target = None
    if args.target_ccy:
        named = parse_currency(args.target_ccy)
        if len(named) != 1:
            raise UsageError(f"--in needs one currency, such as CNY or 人民币; got {args.target_ccy!r}")
        target = named[0]
    if not 1 <= args.months <= 60:
        raise UsageError("--months must be between 1 and 60")
    report = monthly_report(ledger, args.month, target=target)
    series = monthly_series(ledger, args.months, end=report["month"], target=target)
    if args.format == "json":
        figures = chart_data(args.kind, report, series)
        return figures, json.dumps(figures, ensure_ascii=False, indent=2, default=str)
    document = render(
        args.kind, report, series, lang=args.lang, hide=args.hide_amounts, fmt=args.format, generated=dt.date.today(),
    )
    if args.out == "-":
        return {"kind": args.kind, "format": args.format, "month": report["month"], "content": document}, document
    if args.out:
        path = Path(args.out).expanduser()
        if path.exists() and not args.force:
            raise UsageError(f"{path} already exists; choose another --out or add --force")
    else:
        private = "-private" if args.hide_amounts else ""
        path = ledger.root / "charts" / f"{args.kind}-{report['month']}{private}.{args.format}"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(document, encoding="utf-8")
    os.replace(tmp, path)
    data = {
        "kind": args.kind, "format": args.format, "month": report["month"], "path": str(path), "bytes": len(document.encode("utf-8")),
        "partial": report["partial"], "missing": report["missing"], "amounts_hidden": bool(args.hide_amounts),
    }
    text = f"Wrote {path}" + (" (amounts hidden)" if args.hide_amounts else "")
    if report["partial"]:
        text += f"\nNote: no rate for {', '.join(report['missing'])}; those amounts are left out and marked on the chart."
    return data, text


def cmd_rates(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    if args.rates_command == "update":
        on = dt.date.fromisoformat(args.date) if args.date else None
        data = update_rates(
            ledger, on=on, currencies=args.currency, fetch=fetch_frankfurter,
            actor=_actor(args), dry_run=args.dry_run,
        )
        if not data["checked"]:
            return data, f"No currency other than {data['base_currency']} in your ledger yet, so there is nothing to fetch."
        lines = [("Would record" if args.dry_run else "Recorded") + f" {len(data['recorded'])} rate(s) from {data['source']}:"]
        lines += [f"  1 {r['base']} = {r['rate']} {r['quote']}  ({r['date']})" for r in data["recorded"]]
        if data["unchanged"]:
            lines.append(f"{len(data['unchanged'])} already up to date.")
        if data["unsupported"]:
            lines.append(
                f"Not covered by the source: {', '.join(data['unsupported'])}. Record one yourself: "
                f"`money-mom rates set {data['unsupported'][0]} {data['base_currency']} RATE --source \"where it came from\"`."
            )
        lines.append("Rates are daily reference prices, not live market quotes.")
        return data, "\n".join(lines)
    if args.rates_command == "set":
        base, quote = parse_currency(args.base), parse_currency(args.quote)
        if len(base) != 1 or len(quote) != 1:
            raise UsageError("give each currency as one code or word, such as USD or 美元")
        actor = _actor(args)
        ev = ledger.append(
            ledger.new_event(
                "price", actor=actor, date=args.date or _today(), base=base[0], quote=quote[0], rate=args.rate,
                source={"type": "chat" if actor[0] == "agent" else "manual", "ref": args.source},
            ),
            clamp_ts=True,
        )
        return (
            {"id": ev.id, "base": base[0], "quote": quote[0], "rate": args.rate, "date": ev.date.isoformat()},
            f"1 {base[0]} = {args.rate} {quote[0]} on {ev.date.isoformat()} ({args.source})",
        )
    rows = []
    for (base, quote), by_date in sorted(ledger.state.effective_prices().items()):
        if args.base and parse_currency(args.base) != (base,) or args.quote and parse_currency(args.quote) != (quote,):
            continue
        day = max(by_date)
        rec = by_date[day]
        rows.append({
            "base": base, "quote": quote, "date": day.isoformat(), "rate": format(rec.event.rate, "f"),
            "source": (rec.event.source or {}).get("ref"), "age_days": (dt.date.today() - day).days,
        })
    text = _table(["from", "to", "date", "rate", "source"], [[r["base"], r["quote"], r["date"], r["rate"], r["source"]] for r in rows]) if rows else "No rates recorded yet. Run `money-mom rates update`."
    return rows, text


def cmd_pending(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    items = []
    for rec in ledger.state.pending():
        ev = rec.event
        items.append(
            {
                "id": rec.id, "date": rec.date.isoformat(), "payee": ev.payee, "narration": ev.narration,
                "postings": _postings_json(rec.postings), "confidence": ev.confidence,
                "source": ev.source, "actor": f"{ev.actor_type}:{ev.actor_name}",
                "intent": ev.meta.get("intent"), "held_for_duplicate_review": ev.meta.get("held") == "duplicate_review",
            }
        )
    if not items:
        return [], "Nothing is waiting for confirmation."
    lines = []
    for it in items:
        label = it["payee"] or it["narration"] or it["intent"] or ""
        conf = "" if it["confidence"] is None else f"  confidence {it['confidence']}"
        held = "  (looks like a duplicate: see `money-mom dupes`)" if it["held_for_duplicate_review"] else ""
        lines.append(f"{it['id']}  {it['date']}  {label}{conf}{held}")
        lines += [
            f"    {_ljust(p['account'] or '?', 28)} {_rjust(p['amount'], 12)} {p['ccy']}"
            for p in it["postings"]
        ]
    return items, "\n".join(lines)


def _dupe_hint(ledger: Ledger, ids: list[str]) -> list[dict[str, Any]]:
    """Unjudged duplicate candidates that involve any of these just-written transactions."""
    wanted = set(ids)
    return [c.json() for c in find_candidates(ledger.state, ledger.aliases())
            if c.a.id in wanted or c.b.id in wanted]


def _dupe_lines(items: list[dict[str, Any]]) -> list[str]:
    lines = []
    for c in items:
        a, b = c["a"], c["b"]
        lines.append(f"  [{c['tier']}] {a['amount']} {a['ccy']}, {c['days_apart']} day(s) apart")
        for tag, side in (("A", a), ("B", b)):
            label = side["payee"] or side["narration"] or ""
            held = "  (held: waiting for your answer)" if side["held_for_review"] else ""
            lines.append(f"      {tag} {side['id']}  {side['date']}  {side['account']}  {label}  [{side['status']}, {side['source']}]{held}")
        lines.append(f"      evidence: {', '.join(c['evidence'])}")
        lines.append(f"      if the same payment, keep {c['suggest_keep']}")
    return lines


_DUPE_HOW = (
    "Show these to the user and let them decide; never decide for them. Their answer:\n"
    "  same payment:  money-mom dupes resolve A_ID B_ID --same --keep ID --user-said \"...\"\n"
    "  two payments:  money-mom dupes resolve A_ID B_ID --different --user-said \"...\""
)


def cmd_dupes(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    window = getattr(args, "window", None) or DEFAULT_WINDOW_DAYS
    action = getattr(args, "dupes_command", None) or "list"
    if action == "resolve":
        data = resolve_pair(
            ledger, args.first, args.second, verdict="same" if args.same else "different", keep=args.keep,
            actor=_actor(args), user_said=args.user_said, window=window, meta=_override_meta(args),
        )
        if data["verdict"] == "same":
            text = f"Recorded: {data['kept']} and {data['voided']} are the same payment; {data['voided']} was voided (nothing is deleted)."
        else:
            text = "Recorded: these are two different payments. They will not be asked about again."
        if data["released"]:
            text += "\n  Released into the books: " + ", ".join(data["released"])
        if data["still_asking"]:
            text += "\n  Still undecided for these transactions:\n" + "\n".join(_dupe_lines(data["still_asking"]))
        return data, text
    items = [c.json() for c in find_candidates(ledger.state, ledger.aliases(), window=window)]
    data = {"count": len(items), "window_days": window, "candidates": items}
    if not items:
        return data, "No duplicate candidates: nothing in the ledger looks like the same payment twice."
    return data, f"{len(items)} possible duplicate(s):\n" + "\n".join(_dupe_lines(items)) + "\n" + _DUPE_HOW


def cmd_verify(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    problems = ledger.verify_chain()
    state = ledger.state
    data = {
        "ok": not problems, "events": len(state.events), "chained": state.chained_events,
        "before_chaining": state.unchained_events if state.chained_events else len(state.events) and state.unchained_events,
        "head": state.chain_head,
        "problems": [{"code": p.code, "message": p.message, "event_id": p.event_id} for p in problems],
    }
    lines = [
        f"{'OK' if not problems else 'TAMPERING OR EDITS FOUND'}: {data['events']} events, {data['chained']} chained"
        + (f", {data['before_chaining']} written before chaining began (not protected)" if data["before_chaining"] else ""),
        f"  head {state.chain_head}",
        "  (write the head down somewhere else if you want to be able to prove later that nothing at the end was cut off)",
    ]
    lines += [f"  [{p['code']}] {p['message']}" for p in data["problems"]]
    return data, "\n".join(lines)


def cmd_accounts(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    items = [
        {
            "name": a.name, "opened": a.opened.isoformat(),
            "closed": a.closed.isoformat() if a.closed else None,
            "currencies": list(a.currencies) if a.currencies else None,
        }
        for a in sorted(ledger.state.accounts.values(), key=lambda a: a.name)
    ]
    rows = [[i["name"], i["opened"], i["closed"] or "", ",".join(i["currencies"] or [])] for i in items]
    return items, _table(["account", "opened", "closed", "currencies"], rows) if rows else "No accounts yet."


def cmd_show(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    state = ledger.state
    if args.id in state.txns:
        rec = state.txns[args.id]
        kind, status = "txn", rec.status
    elif args.id in state.assertions:
        kind, status = "assert", "voided" if state.assertions[args.id].voided else "live"
    else:
        raise LedgerError(f"no transaction or assertion with id {args.id!r}", code="unknown_target")
    related = [e.raw for e in state.events if e.id == args.id or getattr(e, "target", None) == args.id]
    text = f"{kind} {args.id} ({status})\n" + "\n".join(_dump(r) for r in related)
    return {"id": args.id, "kind": kind, "status": status, "events": related}, text


def cmd_query(args: argparse.Namespace) -> tuple[Any, str]:
    root = _ledger_path(args)
    ledger = Ledger.open(root)
    conn = open_cache(root, ledger.state)
    try:
        result = run_query(conn, args.sql, limit=args.limit)
    finally:
        conn.close()
    text = _table(result["columns"], result["rows"]) if result["rows"] else "(no rows)"
    if result["truncated"]:
        text += f"\n(truncated at {args.limit} rows; use --limit to see more)"
    return result, text


# ---- plumbing --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--ledger", help=f"ledger directory (default: $MONEY_MOM_HOME or {DEFAULT_HOME})")
    common.add_argument("--json", action="store_true", help="print one JSON document instead of text")
    common.add_argument("--actor", help="who is writing: human:NAME or agent:NAME (default: $MONEY_MOM_ACTOR or human:user)")

    parser = argparse.ArgumentParser(
        prog="money-mom", parents=[common],
        description="Money Mom: an append-only, double-entry ledger for AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"money-mom {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def command(name: str, func: Callable, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[common], help=help, description=help)
        p.set_defaults(func=func)
        return p

    def txn_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--date", help="YYYY-MM-DD (default: today)")
        p.add_argument("--narration")
        p.add_argument("--payee")
        p.add_argument("--import-hash")
        p.add_argument("--confidence", type=float, help="0 to 1")
        p.add_argument("--source-type", choices=("chat", "file", "screenshot", "import", "manual"))
        p.add_argument("--source-ref")
        p.add_argument("--source-sha256")

    def lock_flag(p: argparse.ArgumentParser) -> None:
        p.add_argument("--override-lock", metavar="REASON", help="human only: write into a period locked by a balance assertion")

    p = command("init", cmd_init, "create a new ledger")
    p.add_argument("--base-currency", default="CNY")
    p.add_argument("--tone", default="normal", choices=TONES)
    p.add_argument("--template", default="none", choices=["none", *sorted(TEMPLATES)], help="start from a ready-made account tree")
    p.add_argument("--date", help="date the template accounts open (default: today); use an earlier date if you will back-date entries")

    p = command("open", cmd_open, "open an account")
    p.add_argument("account")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.add_argument("--currency", action="append", help="restrict the account to this currency (repeatable)")
    lock_flag(p)

    p = command("close", cmd_close, "close an account (its balance must be zero)")
    p.add_argument("account")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")

    p = command("add", cmd_add, "record a transaction (or several, atomically, with --from-json)")
    p.add_argument("--posting", action="append", metavar='"ACCOUNT AMOUNT CCY"', help='one posting; use "?" as the account when unknown (pending only); repeatable')
    txn_flags(p)
    p.add_argument("--status", choices=("posted", "pending"), help="default: posted")
    p.add_argument("--from-json", metavar="FILE|-", help="read a transaction object or a list of them from a file or stdin")
    lock_flag(p)

    p = command("confirm", cmd_confirm, "turn a pending transaction into a posted one")
    p.add_argument("target", help="id of the pending transaction")
    p.add_argument("--posting", action="append", metavar='"ACCOUNT AMOUNT CCY"', help="replacement postings (repeatable)")
    for slot, text in (("from", "fill the source account"), ("to", "fill the destination account"), ("category", "fill the category")):
        p.add_argument(f"--{slot}", dest=f"slot_{slot}", metavar="ACCOUNT", help=f"{text} of an entry made by spend/income/transfer")
    lock_flag(p)

    p = command("void", cmd_void, "void a transaction or an assertion (nothing is deleted)")
    p.add_argument("target")
    p.add_argument("--reason", required=True)
    lock_flag(p)

    p = command("assert", cmd_assert, "assert an account balance at the end of a day")
    p.add_argument("account")
    p.add_argument("amount")
    p.add_argument("ccy")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")

    slot_help = {
        "from": "account the money leaves (name, alias or leaf name)",
        "to": "account the money arrives in",
        "category": "expense or income category",
    }
    for kind, summary in (
        ("spend", "record money spent: an expense paid from an asset or liability account"),
        ("income", "record money received into an account from an income category"),
        ("transfer", "move money between your own accounts (also sets opening balances from Equity)"),
    ):
        p = command(kind, _intent_handler(kind), summary + ". Unknown or ambiguous names are never guessed: the entry is recorded as pending")
        p.add_argument("amount", help="a positive amount such as 38, 38.50 or 1,200; may carry its currency: 5美元, 'HK$200', 'USD 5' (single-quote anything with a $). The direction comes from the command")
        for slot in SPECS[kind]:
            p.add_argument(f"--{slot}", dest=f"slot_{slot}", metavar="ACCOUNT", help=slot_help[slot])
        p.add_argument("--ccy", help="currency, as a code or a word (USD, 美元). Default: what the amount says, else what the account allows, else the base currency")
        txn_flags(p)
        p.add_argument("--strict", action="store_true", help="fail instead of recording a pending entry when an account cannot be resolved")
        p.add_argument("--dry-run", action="store_true", help="show what would be recorded, write nothing")
        lock_flag(p)

    p = command("exchange", cmd_exchange, "record a currency exchange: money in one currency leaves an account and the same worth in another arrives. Recorded through an Equity conversion account; nothing is fetched")
    p.add_argument("parts", nargs="+", metavar="AMOUNT [CCY] AMOUNT [CCY]",
                   help="what you gave, then what you got: 100 USD 720 CNY, or '100 USD' '720 CNY', or 100 720 when the accounts or --give-ccy/--get-ccy say the currencies")
    p.add_argument("--from", dest="slot_from", metavar="ACCOUNT", help="account the money you gave leaves")
    p.add_argument("--to", dest="slot_to", metavar="ACCOUNT", help="account the money you got arrives in")
    p.add_argument("--give-ccy", help="currency of what you gave, as a code or word (avoids shell quoting of $)")
    p.add_argument("--get-ccy", help="currency of what you got")
    txn_flags(p)
    p.add_argument("--strict", action="store_true", help="fail instead of recording a pending entry when an account cannot be resolved")
    p.add_argument("--dry-run", action="store_true", help="show what would be recorded, write nothing")
    lock_flag(p)

    p = command("alias", cmd_alias, "manage friendly names for accounts")
    alias_sub = p.add_subparsers(dest="alias_command", required=True, metavar="ACTION")
    q = alias_sub.add_parser("add", parents=[common], help="make ALIAS mean ACCOUNT")
    q.add_argument("alias")
    q.add_argument("account")
    q = alias_sub.add_parser("remove", parents=[common], help="remove an alias")
    q.add_argument("alias")
    alias_sub.add_parser("list", parents=[common], help="list aliases")

    p = command("resolve", cmd_resolve, "show which account a name resolves to, or why it does not")
    p.add_argument("term")
    p.add_argument("--type", action="append", choices=sorted(_TYPE_ROOTS), help="only this kind of account (repeatable)")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")

    p = command("currency", cmd_currency, "show which currency a piece of text means, and how it was decided")
    p.add_argument("text", help="for example 38, 5美元, 'HK$200' or 'USD 5' (single-quote anything with a $)")
    p.add_argument("--ccy", help="a currency given explicitly: a code such as USD, or a word such as 美元")
    p.add_argument("--account", action="append", help="an account involved; the currencies it allows narrow the choice (repeatable)")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p = command("import", cmd_import, "bank and wallet statements: inspect a file, import it with a mapping, teach it rules")
    isub = p.add_subparsers(dest="import_command", required=True, metavar="ACTION")
    q = isub.add_parser("inspect", parents=[common], help="look at a statement file and suggest a mapping (nothing is written)")
    q.add_argument("file")
    q.add_argument("--encoding", help="force an encoding (default: utf-8, then gb18030)")
    q = isub.add_parser("save-map", parents=[common], help="validate a mapping (TOML) and save it under the ledger's imports folder")
    q.add_argument("name")
    q.add_argument("file", help="the mapping file, or - for stdin")
    isub.add_parser("maps", parents=[common], help="list saved mappings")
    q = isub.add_parser("run", parents=[common], help="import a statement: rule-matched rows are posted, the rest wait as pending")
    q.add_argument("file")
    q.add_argument("--map", required=True, help="saved mapping name, or a path to a .toml file")
    q.add_argument("--account", required=True, help="the account the statement belongs to, e.g. 支付宝 or Assets:招行卡")
    q.add_argument("--since", metavar="DATE", help="skip rows before this date")
    q.add_argument("--until", metavar="DATE", help="skip rows after this date")
    q.add_argument("--confidence", type=float, help="0 to 1; an agent must say how sure it is the mapping reads this file correctly")
    q.add_argument("--encoding")
    q.add_argument("--dry-run", action="store_true", help="show what would happen, write nothing")
    q = isub.add_parser("rule-add", parents=[common], help="teach a mapping: rows matching TEXT go to ACCOUNT (or are skipped)")
    q.add_argument("--map", required=True)
    q.add_argument("--match", required=True, help="text to look for (case-insensitive), or a regular expression with --regex")
    q.add_argument("--account", help="the counter account, e.g. 外卖 or Expenses:餐饮:外卖")
    q.add_argument("--skip", action="store_true", help="skip these rows instead (for example a transfer you import from the other side)")
    q.add_argument("--field", choices=("any", "payee", "description"), default="any")
    q.add_argument("--regex", action="store_true")
    q = isub.add_parser("rule-list", parents=[common], help="list the rules of a mapping")
    q.add_argument("--map", required=True)
    q = isub.add_parser("recheck", parents=[common], help="after adding rules, confirm the pending rows that now match")
    q.add_argument("--map", required=True)
    q.add_argument("--dry-run", action="store_true")

    p = command("reconcile", cmd_reconcile, "compare a statement with the ledger: what is missing, extra, different or charged twice; --assert seals a clean result")
    p.add_argument("account", help="the account the statement belongs to")
    p.add_argument("file", nargs="?", help="the statement file (CSV); needs --map")
    p.add_argument("--map", help="saved mapping that describes the file")
    p.add_argument("--from-json", metavar="FILE|-", help="rows an agent read from a PDF or screenshot: [{date, amount, payee, description, id}] (positive = money into the account)")
    p.add_argument("--since", metavar="DATE")
    p.add_argument("--until", metavar="DATE")
    p.add_argument("--tolerance-days", type=int, default=3, help="how many days apart the bank and the ledger may date the same item (default 3)")
    p.add_argument("--closing-balance", metavar="AMOUNT", help="the balance the statement ends with")
    p.add_argument("--closing-date", metavar="DATE", help="the day that balance is for")
    p.add_argument("--closing-from-statement", action="store_true", help="use the balance column of the statement's last row")
    p.add_argument("--assert", dest="seal", action="store_true", help="when everything matches, record a balance assertion that locks the period")
    p.add_argument("--encoding")

    command("doctor", cmd_doctor, "check the install and the ledger; a missing ledger is not an error")
    command("check", cmd_check, "replay the whole ledger and re-verify every assertion")

    p = command("balance", cmd_balance, "show balances (exact decimals)")
    p.add_argument("--account", help="only this account and its children")
    p.add_argument("--as-of", metavar="DATE")
    p.add_argument("--all", action="store_true", help="include zero balances")
    p.add_argument("--in", dest="target_ccy", metavar="CCY", help="also express every balance in this currency, using stored rates")

    p = command("networth", cmd_networth, "assets minus liabilities, per currency and as one total in --in (default: the base currency)")
    p.add_argument("--in", dest="target_ccy", metavar="CCY", help="currency of the total (default: the ledger's base currency)")
    p.add_argument("--as-of", metavar="DATE")

    p = command("report", cmd_report, "monthly report: income, spending by category, comparison with last month, net worth change")
    p.add_argument("--month", metavar="YYYY-MM", help="default: the current month (month to date)")
    p.add_argument("--in", dest="target_ccy", metavar="CCY", help="currency of the totals (default: the ledger's base currency)")
    p.add_argument("--top", type=int, default=5, help="how many of the biggest items to list (default 5)")

    p = command("alerts", cmd_alerts, "recurring charges (subscriptions) and things worth a look: price changes, overdue or doubled charges, unusually large or sudden spending")
    p.add_argument("--month", metavar="YYYY-MM", help="default: the current month (up to today)")
    p.add_argument("--in", dest="target_ccy", metavar="CCY", help="currency of the monthly total (default: the ledger's base currency)")

    p = command("chart", cmd_chart, "draw charts for a month: an HTML sheet (default) or one chart as SVG; amounts can be hidden for sharing")
    p.add_argument("kind", nargs="?", default="sheet", choices=KINDS, help="sheet (all charts on one page, default), spending, change, trend, networth, waterfall")
    p.add_argument("--month", metavar="YYYY-MM", help="default: the current month (month to date)")
    p.add_argument("--months", type=int, default=12, help="how many months the trend and net worth charts cover (default 12)")
    p.add_argument("--in", dest="target_ccy", metavar="CCY", help="currency of the figures (default: the ledger's base currency)")
    p.add_argument("--lang", choices=LANGS, default="zh", help="language of the labels (default zh)")
    p.add_argument("--format", choices=FORMATS, default="html", help="html (default), svg (one chart), or json (the figures behind it, nothing written)")
    p.add_argument("--hide-amounts", action="store_true", help="draw shares and shapes only, with no amounts anywhere, so the picture can be shared")
    p.add_argument("--out", metavar="PATH", help="where to write it (default: <ledger>/charts/); '-' prints to the terminal")
    p.add_argument("--force", action="store_true", help="overwrite an existing --out file")

    p = command("rates", cmd_rates, "exchange rates: fetch (the only command that uses the network), set, list")
    rates_sub = p.add_subparsers(dest="rates_command", required=True, metavar="ACTION")
    q = rates_sub.add_parser("update", parents=[common], help="fetch rates into the base currency from the ECB reference rates (needs the network)")
    q.add_argument("--currency", action="append", help="only this currency (repeatable); default: every foreign currency in the ledger")
    q.add_argument("--date", help="YYYY-MM-DD: the rate for that day (default: the latest)")
    q.add_argument("--dry-run", action="store_true", help="fetch and show, write nothing")
    q = rates_sub.add_parser("set", parents=[common], help="record a rate yourself: 1 BASE = RATE QUOTE")
    q.add_argument("base")
    q.add_argument("quote")
    q.add_argument("rate", help="a positive decimal, such as 7.2345")
    q.add_argument("--source", required=True, help="where the rate came from, for example \"bank app 2026-10-07\"")
    q.add_argument("--date", help="YYYY-MM-DD (default: today)")
    q = rates_sub.add_parser("list", parents=[common], help="the latest stored rate for each pair")
    q.add_argument("--base")
    q.add_argument("--quote")

    command("pending", cmd_pending, "list transactions waiting for confirmation")
    command("accounts", cmd_accounts, "list accounts")

    p = command("dupes", cmd_dupes, "possible duplicate payments across sources (list), and record the user's answer (resolve)")
    p.add_argument("--window", type=int, help=f"days apart that still count as the same time (default {DEFAULT_WINDOW_DAYS})")
    dsub = p.add_subparsers(dest="dupes_command", metavar="ACTION")
    q = dsub.add_parser("list", parents=[common], help="list unjudged candidates (the default)")
    q.add_argument("--window", type=int)
    q = dsub.add_parser("resolve", parents=[common], help="record the user's answer for two transactions")
    q.add_argument("first")
    q.add_argument("second")
    group = q.add_mutually_exclusive_group(required=True)
    group.add_argument("--same", action="store_true", help="they are the same payment: one is voided (nothing is deleted)")
    group.add_argument("--different", action="store_true", help="they are two payments: remembered, never asked again")
    q.add_argument("--keep", help="with --same: the id to keep (the other is voided)")
    q.add_argument("--user-said", metavar="TEXT", help="an agent must pass what the user answered")
    q.add_argument("--window", type=int)
    lock_flag(q)

    p = command("verify", cmd_verify, "check the ledger's hash chain: finds events that were edited, removed, reordered or added by hand")

    p = command("show", cmd_show, "show a transaction or assertion with all events that touch it")
    p.add_argument("id")

    p = command("query", cmd_query, "run one read-only SQL SELECT against the cache (tables: txns, postings, accounts, assertions, events; views: v_postings, v_pending, v_balances, v_monthly)")
    p.add_argument("sql")
    p.add_argument("--limit", type=int, default=1000)

    return parser


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    parser = _build_parser()
    args = parser.parse_args(argv)
    for name in ("ledger", "json", "actor"):
        if not hasattr(args, name):
            setattr(args, name, False if name == "json" else None)
    for name in ("date", "status", "narration", "payee", "import_hash", "confidence", "source_type",
                 "source_ref", "source_sha256", "from_json", "override_lock", "currency", "posting", "window", "user_said", "keep"):
        if not hasattr(args, name):
            setattr(args, name, None)
    try:
        _actor(args)  # validate --actor / $MONEY_MOM_ACTOR even for read-only commands
        data, text = args.func(args)
    except LedgerError as err:
        if args.json:
            print(_dump({"ok": False, "error": err.to_dict()}))
        else:
            print(f"error: {err}", file=sys.stderr)
        return 2 if err.code == "usage_error" else 1
    print(_dump({"ok": True, "data": data}) if args.json else text)
    if args.command in ("check", "doctor", "verify") and not data["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
