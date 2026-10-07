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
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .cache import open_cache, run_query
from .currency import choose_currency, parse_currency, parse_money
from .doctor import run_doctor
from .errors import LedgerError
from .events import ROOTS
from .intents import SPECS, postings_with_slots, record_exchange, record_intent, resolve_account
from .ledger import TONES, Ledger
from .rates import convert_balances, fetch_frankfurter, update_rates
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
        return result.to_dict(), _intent_text(result)

    return handler


def cmd_exchange(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    result = record_exchange(
        ledger, give=args.give, get=args.get, slots={"from": args.slot_from, "to": args.slot_to},
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


def cmd_check(args: argparse.Namespace) -> tuple[Any, str]:
    ledger = Ledger.open(_ledger_path(args))
    problems = ledger.check()
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
    text += f"\nTotal in {result['in']}: {result['total']}" + (" (PARTIAL)" if result["partial"] else "")
    if result["missing"]:
        text += f"\n  No rate for {', '.join(result['missing'])}: left out of the total. Run `money-mom rates update`."
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
                "intent": ev.meta.get("intent"),
            }
        )
    if not items:
        return [], "Nothing is waiting for confirmation."
    lines = []
    for it in items:
        label = it["payee"] or it["narration"] or it["intent"] or ""
        conf = "" if it["confidence"] is None else f"  confidence {it['confidence']}"
        lines.append(f"{it['id']}  {it['date']}  {label}{conf}")
        lines += [
            f"    {_ljust(p['account'] or '?', 28)} {_rjust(p['amount'], 12)} {p['ccy']}"
            for p in it["postings"]
        ]
    return items, "\n".join(lines)


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
    p.add_argument("give", help="what you gave, e.g. '100 USD' or 100 (the currency can come from the account)")
    p.add_argument("get", help="what you got, e.g. '720 CNY'")
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
                 "source_ref", "source_sha256", "from_json", "override_lock", "currency", "posting"):
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
    if args.command in ("check", "doctor") and not data["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
