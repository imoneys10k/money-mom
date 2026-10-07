import datetime as dt
import io
import json
import unittest
import urllib.error
from decimal import Decimal
from unittest import mock

from money_mom import Ledger, LedgerError, RuleError, ValidationError
from money_mom.events import parse_event
from money_mom.rates import (
    FRANKFURTER_REF, OPEN_ER_REF, Fetched, RateUnsupported, convert, convert_balances, fetch_frankfurter, fetch_open_er,
    find_rate, round_money, update_rates,
)
from money_mom.cache import open_cache, run_query
from money_mom.templates import apply_template

from helpers import AGENT, HUMAN, LedgerTestCase
from test_cli import CliTestCase

D = Decimal
DAY = dt.date(2026, 10, 7)
SRC = {"type": "import", "ref": "test source"}


def err(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except LedgerError as e:
        return e
    raise AssertionError("expected a LedgerError")


class RateCase(LedgerTestCase):
    def price(self, base, quote, rate, date="2026-10-06", **kw):
        kw.setdefault("source", SRC)
        return self.ledger.append(self.ledger.new_event(
            "price", actor=kw.pop("actor", HUMAN), date=date, base=base, quote=quote, rate=rate, **kw), clamp_ts=True)

    def find(self, base, quote, on=DAY, pivot="CNY"):
        return find_rate(self.ledger.state, base, quote, on, pivot=pivot)


class PriceEvents(unittest.TestCase):
    def raw(self, **over):
        base = {"v": 1, "id": "P1", "kind": "price", "ts": "2026-10-07T12:00:00+08:00",
                "actor": {"type": "human", "name": "u"}, "date": "2026-10-06", "base": "USD", "quote": "CNY",
                "rate": "6.7046", "source": SRC}
        base.update(over)
        return base

    def test_valid(self):
        ev = parse_event(self.raw())
        self.assertEqual((ev.base, ev.quote, ev.rate), ("USD", "CNY", D("6.7046")))

    def test_invalid(self):
        for over, code in (
            ({"rate": "0"}, "invalid_price"), ({"rate": "-1"}, "invalid_price"), ({"rate": 6.7}, "invalid_amount"),
            ({"rate": "6,7"}, "invalid_amount"), ({"quote": "USD"}, "invalid_price"), ({"base": "usd"}, "invalid_currency"),
            ({"date": "2026-13-01"}, "invalid_date"), ({"source": None}, "missing_field"), ({"extra": 1}, "unknown_field"),
        ):
            raw = self.raw(**over)
            with self.assertRaises(ValidationError, msg=str(over)) as ctx:
                parse_event(raw)
            self.assertEqual(ctx.exception.code, code, str(over))
        raw = self.raw()
        del raw["source"]
        with self.assertRaises(ValidationError) as ctx:
            parse_event(raw)
        self.assertEqual(ctx.exception.code, "missing_field")


class Lookup(RateCase):
    def test_direct_inverse_and_identity(self):
        self.price("USD", "CNY", "6.7046")
        self.assertEqual((self.find("USD", "CNY").rate, self.find("USD", "CNY").via), (D("6.7046"), "direct"))
        inv = self.find("CNY", "USD")
        self.assertEqual(inv.via, "inverse")
        self.assertEqual(round(inv.rate, 6), round(D(1) / D("6.7046"), 6))
        ident = self.find("CNY", "CNY")
        self.assertEqual((ident.rate, ident.via), (D(1), "identity"))

    def test_converting_between_two_foreign_currencies_goes_through_the_base(self):
        self.price("USD", "CNY", "6.7046")
        self.price("HKD", "CNY", "0.85432")
        info = self.find("USD", "HKD")
        self.assertEqual(info.via, "via CNY")
        self.assertEqual(info.rate, D("6.7046") * (D(1) / D("0.85432")))
        self.assertIsNone(find_rate(self.ledger.state, "USD", "HKD", DAY, pivot=None))

    def test_never_looks_ahead_and_uses_the_latest_earlier_day(self):
        self.price("USD", "CNY", "6.70", date="2026-10-01")
        self.price("USD", "CNY", "6.80", date="2026-10-05")
        self.price("USD", "CNY", "9.99", date="2026-10-20")
        self.assertEqual(self.find("USD", "CNY", dt.date(2026, 10, 7)).rate, D("6.80"))
        self.assertEqual(self.find("USD", "CNY", dt.date(2026, 10, 3)).rate, D("6.70"))
        self.assertIsNone(self.find("USD", "CNY", dt.date(2026, 9, 30)))

    def test_staleness_is_reported_not_hidden(self):
        self.price("USD", "CNY", "6.70", date="2026-09-01")
        info = self.find("USD", "CNY")
        self.assertEqual((info.stale, info.age_days), (True, 36))
        self.price("USD", "CNY", "6.71", date="2026-10-06")
        self.assertFalse(self.find("USD", "CNY").stale)

    def test_the_older_of_two_combined_rates_decides_staleness(self):
        self.price("USD", "CNY", "6.70", date="2026-10-06")
        self.price("HKD", "CNY", "0.85", date="2026-09-01")
        info = self.find("USD", "HKD")
        self.assertEqual((info.rate_date, info.stale), (dt.date(2026, 9, 1), True))

    def test_a_later_price_for_the_same_day_replaces_an_earlier_one_and_void_removes_it(self):
        first = self.price("USD", "CNY", "6.70")
        second = self.price("USD", "CNY", "6.75")
        self.assertEqual(self.find("USD", "CNY").rate, D("6.75"))
        self.ledger.void(second.id, "typo")
        self.assertEqual(self.find("USD", "CNY").rate, D("6.70"))
        self.ledger.void(first.id, "also wrong")
        self.assertIsNone(self.find("USD", "CNY"))
        e = err(self.ledger.void, first.id, "again")
        self.assertEqual(e.code, "already_voided")

    def test_convert_is_exact_and_refuses_to_guess(self):
        self.price("USD", "CNY", "6.7046")
        amount, info = convert(self.ledger.state, D("100"), "USD", "CNY", DAY)
        self.assertEqual(amount, D("670.4600"))
        e = err(convert, self.ledger.state, D("1"), "EUR", "CNY", DAY)
        self.assertEqual(e.code, "no_rate")
        self.assertIn("rates update", e.message)

    def test_rounding_follows_the_currency(self):
        self.assertEqual(round_money(D("670.455"), "CNY"), D("670.46"))  # half up
        self.assertEqual(round_money(D("0.005"), "USD"), D("0.01"))
        self.assertEqual(round_money(D("1234.5"), "JPY"), D("1235"))
        self.assertEqual(str(round_money(D("5"), "CNY")), "5.00")


class Converting(RateCase):
    def test_totals_are_summed_before_rounding_and_missing_rates_are_listed(self):
        self.price("USD", "CNY", "6.7046")
        balances = {("Assets:A", "USD"): D("1000"), ("Assets:B", "CNY"): D("20000"), ("Assets:C", "EUR"): D("5")}
        result = convert_balances(self.ledger.state, balances, "CNY", DAY, pivot="CNY")
        self.assertEqual(result["total"], "26704.60")
        self.assertTrue(result["partial"])
        self.assertEqual(result["missing"], ["EUR"])
        eur = next(r for r in result["balances"] if r["ccy"] == "EUR")
        self.assertIsNone(eur["converted"])

    def test_no_double_rounding(self):
        self.price("USD", "CNY", "6.7046")
        balances = {("a", "USD"): D("0.01"), ("b", "USD"): D("0.01"), ("c", "USD"): D("0.01")}
        result = convert_balances(self.ledger.state, balances, "CNY", DAY, pivot="CNY")
        self.assertEqual([r["converted"] for r in result["balances"]], ["0.07", "0.07", "0.07"])
        self.assertEqual(result["total"], "0.20")  # 3 x 0.067046 = 0.201138, not 3 x 0.07

    def test_stale_currencies_are_flagged(self):
        self.price("USD", "CNY", "6.70", date="2026-08-01")
        result = convert_balances(self.ledger.state, {("a", "USD"): D(1)}, "CNY", DAY, pivot="CNY")
        self.assertEqual(result["stale"], ["USD"])


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_for(payload=None, status=None, exc=None, seen=None):
    def opener(request, timeout=None):
        if seen is not None:
            seen.append((request.full_url, dict(request.header_items()), timeout))
        if exc is not None:
            raise exc
        if status is not None:
            raise urllib.error.HTTPError(request.full_url, status, "x", {}, io.BytesIO(b"{}"))
        return FakeResponse(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    return opener


class Fetching(unittest.TestCase):
    OK = {"amount": 1.0, "base": "USD", "date": "2026-10-06", "rates": {"CNY": 6.7046}}

    def test_a_good_answer(self):
        seen = []
        got = fetch_frankfurter("USD", "CNY", None, opener=opener_for(self.OK, seen=seen))
        self.assertEqual(got, Fetched("USD", "CNY", dt.date(2026, 10, 6), "6.7046"))
        url, headers, timeout = seen[0]
        self.assertEqual(url, "https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY")
        self.assertTrue(headers["User-agent"].startswith("money-mom/"))
        self.assertEqual(timeout, 10)

    def test_a_historical_day_is_requested_by_date(self):
        seen = []
        fetch_frankfurter("USD", "CNY", dt.date(2026, 10, 1), opener=opener_for(self.OK, seen=seen))
        self.assertTrue(seen[0][0].startswith("https://api.frankfurter.dev/v1/2026-10-01?"))

    def test_only_codes_and_a_date_are_sent(self):
        seen = []
        fetch_frankfurter("USD", "CNY", None, opener=opener_for(self.OK, seen=seen))
        self.assertNotRegex(seen[0][0], r"amount|account|payee")
        self.assertTrue(seen[0][0].startswith("https://"))

    def test_the_exact_text_of_the_rate_is_kept(self):
        payload = b'{"amount":1.0,"base":"USD","date":"2026-10-06","rates":{"CNY":158.09}}'
        self.assertEqual(fetch_frankfurter("USD", "CNY", None, opener=opener_for(payload)).rate, "158.09")

    def test_an_unsupported_pair_is_not_an_error_of_the_network(self):
        for status in (404, 422):
            with self.assertRaises(RateUnsupported):
                fetch_frankfurter("USD", "TWD", None, opener=opener_for(status=status))

    def test_trouble_is_reported_as_rates_unavailable(self):
        for kwargs in (
            dict(status=500), dict(exc=urllib.error.URLError("no route")), dict(exc=TimeoutError()),
            dict(payload=b"not json"), dict(payload={"base": "USD"}),
            dict(payload={**self.OK, "base": "EUR"}), dict(payload={**self.OK, "rates": {"CNY": -1}}),
            dict(payload={**self.OK, "date": "yesterday"}), dict(payload={**self.OK, "rates": {"CNY": "x"}}),
        ):
            e = err(fetch_frankfurter, "USD", "CNY", None, opener=opener_for(**kwargs))
            self.assertEqual(e.code, "rates_unavailable", kwargs)

    def test_bad_currency_codes_never_reach_the_network(self):
        seen = []
        self.assertRaises(LedgerError, fetch_frankfurter, "usd", "CNY", None, opener=opener_for(self.OK, seen=seen))
        self.assertEqual(seen, [])


class FallbackFetching(unittest.TestCase):
    OK = {"result": "success", "base_code": "TWD", "time_last_update_utc": "Wed, 07 Oct 2026 00:02:32 +0000",
          "rates": {"CNY": 0.2192, "USD": 0.0314}}

    def test_a_good_answer_is_dated_by_the_source_and_names_it(self):
        seen = []
        got = fetch_open_er("TWD", "CNY", None, opener=opener_for(self.OK, seen=seen))
        self.assertEqual((got.base, got.quote, got.date, got.rate), ("TWD", "CNY", dt.date(2026, 10, 7), "0.2192"))
        self.assertIn("open.er-api.com", got.source)
        self.assertEqual(seen[0][0], "https://open.er-api.com/v6/latest/TWD")  # only a currency code goes out

    def test_it_has_no_history_so_an_earlier_day_is_unsupported_not_answered_with_today(self):
        self.assertRaises(RateUnsupported, fetch_open_er, "TWD", "CNY", dt.date(2026, 1, 1), opener=opener_for(self.OK))

    def test_unknown_pairs_and_bad_answers(self):
        self.assertRaises(RateUnsupported, fetch_open_er, "TWD", "XXX", None, opener=opener_for(self.OK))
        self.assertRaises(RateUnsupported, fetch_open_er, "TWD", "CNY", None, opener=opener_for({**self.OK, "result": "error"}))
        for payload in ({**self.OK, "rates": {"CNY": 0}}, {**self.OK, "time_last_update_utc": "yesterday-ish"}, b"not json"):
            self.assertEqual(err(fetch_open_er, "TWD", "CNY", None, opener=opener_for(payload)).code, "rates_unavailable")
        self.assertEqual(err(fetch_open_er, "TWD", "CNY", None, opener=opener_for(status=500)).code, "rates_unavailable")
        self.assertEqual(err(fetch_open_er, "TWD", "CNY", None, opener=opener_for(exc=urllib.error.URLError("offline"))).code, "rates_unavailable")


class Updating(RateCase):
    def setUp(self) -> None:
        super().setUp()
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)
        self.ledger.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])
        self.ledger.open_account("Assets:港币户", "2026-01-01", currencies=["HKD"])
        self.ledger.open_account("Assets:台币户", "2026-01-01", currencies=["TWD"])
        self.calls = []

    def fetch(self, base, quote, on):
        self.calls.append((base, quote, on))
        rates = {"USD": "6.7046", "HKD": "0.85432"}
        if base not in rates:
            raise RateUnsupported(base)
        return Fetched(base, quote, dt.date(2026, 10, 6), rates[base])

    def update(self, **kw):
        kw.setdefault("fetch", self.fetch)
        return update_rates(self.ledger, **kw)

    def test_records_what_is_new_with_its_source(self):
        result = self.update()
        self.assertEqual([r["base"] for r in result["recorded"]], ["HKD", "USD"])
        self.assertEqual(result["unsupported"], ["TWD"])
        self.assertTrue(result["written"])
        ev = [e for e in self.ledger.state.events if e.kind == "price"][0]
        self.assertEqual((ev.source["type"], ev.source["ref"]), ("import", "api.frankfurter.dev (ECB reference rate)"))
        self.assertEqual((ev.quote, ev.date), ("CNY", dt.date(2026, 10, 6)))

    def test_the_second_source_is_asked_only_for_what_the_first_does_not_publish(self):
        asked = []

        def second(base, quote, on):
            asked.append(base)
            return Fetched(base, quote, dt.date(2026, 10, 7), "0.2192", OPEN_ER_REF)

        result = self.update(fallback=second)
        self.assertEqual(asked, ["TWD"])
        self.assertEqual(result["unsupported"], [])
        self.assertEqual(sorted(result["sources"]), sorted([FRANKFURTER_REF, OPEN_ER_REF]))
        twd = [e for e in self.ledger.state.events if e.kind == "price" and e.base == "TWD"][0]
        self.assertEqual(twd.source["ref"], OPEN_ER_REF)

    def test_a_second_source_that_does_not_know_it_either_leaves_it_unsupported(self):
        def second(base, quote, on):
            raise RateUnsupported(base)
        self.assertEqual(self.update(fallback=second)["unsupported"], ["TWD"])

    def test_only_foreign_currencies_are_fetched(self):
        self.update()
        self.assertEqual({c[0] for c in self.calls}, {"USD", "HKD", "TWD"})
        self.assertTrue(all(c[1] == "CNY" for c in self.calls))

    def test_running_again_changes_nothing(self):
        self.update()
        before = self.total_lines()
        again = self.update()
        self.assertEqual((again["recorded"], len(again["unchanged"]), again["written"]), ([], 2, False))
        self.assertEqual(self.total_lines(), before)

    def test_a_changed_rate_for_the_same_day_is_recorded_as_a_correction(self):
        self.update()
        changed = lambda b, q, o: Fetched(b, q, dt.date(2026, 10, 6), "6.7100") if b == "USD" else self.fetch(b, q, o)
        result = self.update(fetch=changed)
        self.assertEqual([r["rate"] for r in result["recorded"]], ["6.7100"])
        self.assertEqual(find_rate(self.ledger.state, "USD", "CNY", DAY).rate, D("6.7100"))

    def test_a_network_failure_writes_nothing(self):
        def flaky(base, quote, on):
            if base == "USD":
                raise LedgerError("could not reach the rate source: boom", code="rates_unavailable")
            return self.fetch(base, quote, on)
        before = self.event_files()
        e = err(self.update, fetch=flaky)
        self.assertEqual(e.code, "rates_unavailable")
        self.assertEqual(self.event_files(), before)

    def test_explicit_currencies_and_dates(self):
        self.update(currencies=["usd"], on=dt.date(2026, 10, 1))
        self.assertEqual(self.calls, [("USD", "CNY", dt.date(2026, 10, 1))])
        self.assertEqual(self.update(currencies=["CNY"])["checked"], [])  # the base needs no rate
        self.assertEqual(self.update(currencies=["美元"])["checked"], ["USD"])  # words work too
        self.assertEqual(err(self.update, currencies=["$"]).code, "usage_error")
        self.assertEqual(err(self.update, currencies=["XYZ"]).code, "unknown_currency")

    def test_dry_run_writes_nothing(self):
        before = self.event_files()
        result = self.update(dry_run=True)
        self.assertEqual((len(result["recorded"]), result["written"]), (2, False))
        self.assertEqual(self.event_files(), before)

    def test_nothing_to_do_without_foreign_currencies(self):
        fresh = Ledger.init(self.root.parent / "plain", clock=self.clock)
        apply_template(fresh, "cn", "2026-01-01", HUMAN)
        self.assertEqual(update_rates(fresh, fetch=self.fetch)["checked"], [])

    def test_agents_may_update_rates_without_a_confidence(self):
        self.update(actor=AGENT)  # a rate is a sourced fact, not a judgement call


class InTheCache(RateCase):
    def test_prices_table_and_latest_rate_view(self):
        self.price("USD", "CNY", "6.70", date="2026-10-01")
        self.price("USD", "CNY", "6.7046", date="2026-10-06")
        self.price("HKD", "CNY", "0.85432", date="2026-10-06")
        conn = open_cache(self.root, self.ledger.state)
        try:
            self.assertEqual(run_query(conn, "SELECT COUNT(*) FROM prices")["rows"], [[3]])
            latest = run_query(conn, "SELECT base, quote, date, rate_text FROM v_rates ORDER BY base")["rows"]
            self.assertEqual(latest, [["HKD", "CNY", "2026-10-06", "0.85432"], ["USD", "CNY", "2026-10-06", "6.7046"]])
            self.assertEqual(run_query(conn, "SELECT source_ref FROM prices LIMIT 1")["rows"], [["test source"]])
        finally:
            conn.close()

    def test_voided_and_replaced_prices_are_not_in_the_cache(self):
        first = self.price("USD", "CNY", "6.70")
        self.price("USD", "CNY", "6.75")
        conn = open_cache(self.root, self.ledger.state)
        try:
            self.assertEqual(run_query(conn, "SELECT rate_text FROM prices")["rows"], [["6.75"]])
        finally:
            conn.close()


class Cli(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.js("open", "Assets:美元户", "--date", "2026-01-01", "--currency", "USD")
        self.js("transfer", "20000", "--from", "期初", "--to", "银行卡", "--date", "2026-01-02")
        self.js("transfer", "1000", "--from", "期初", "--to", "美元户", "--date", "2026-01-02")

    def fake_fetch(self, base, quote, on=None, **kw):
        return Fetched(base, quote, dt.date(2026, 10, 6), "6.7046")

    def test_rates_update_set_and_list(self):
        with mock.patch("money_mom.cli.fetch_frankfurter", self.fake_fetch):
            preview, _ = self.run_cli("--ledger", str(self.root), "rates", "update", "--dry-run")
            data = self.js("rates", "update")["data"]
            again, _ = self.run_cli("--ledger", str(self.root), "rates", "update")
        self.assertEqual([r["base"] for r in data["recorded"]], ["USD"])
        self.assertIn("Would record 1 rate(s)", preview)
        self.assertIn("1 USD = 6.7046 CNY  (2026-10-06, api.frankfurter.dev (ECB reference rate))", preview)
        self.assertIn("not live market quotes", preview)
        self.assertIn("1 already up to date", again)
        self.js("rates", "set", "EUR", "CNY", "7.8", "--source", "bank app", "--date", "2026-10-06")
        listed = {(r["base"], r["quote"]): r for r in self.js("rates", "list")["data"]}
        self.assertEqual(listed[("USD", "CNY")]["rate"], "6.7046")
        self.assertEqual(listed[("EUR", "CNY")]["source"], "bank app")
        self.assertEqual(len(self.js("rates", "list", "--base", "EUR")["data"]), 1)
        self.run_cli("--ledger", str(self.root), "rates", "list")

    def test_the_second_source_covers_twd_and_can_be_switched_off(self):
        self.js("open", "Assets:台币户", "--date", "2026-01-01", "--currency", "TWD")
        self.js("transfer", "1000", "--ccy", "TWD", "--from", "期初", "--to", "台币户", "--date", "2026-01-02")
        asked = []

        def second(base, quote, on=None, **kw):
            asked.append(base)
            return Fetched(base, quote, dt.date(2026, 10, 7), "0.2192", OPEN_ER_REF)

        def unsupported(base, quote, on=None, **kw):
            if base == "TWD":
                raise RateUnsupported(base)
            return self.fake_fetch(base, quote, on)

        with mock.patch("money_mom.cli.fetch_frankfurter", unsupported), mock.patch("money_mom.cli.fetch_open_er", second):
            off = self.js("rates", "update", "--no-fallback", "--dry-run")["data"]
            self.assertEqual((asked, off["unsupported"]), ([], ["TWD"]))
            text, _ = self.run_cli("--ledger", str(self.root), "rates", "update")
        self.assertEqual(asked, ["TWD"])
        self.assertIn("exchangerate-api.com", text)
        self.assertIn("1 TWD = 0.2192 CNY", text)

    def test_set_needs_a_source_and_a_sane_rate(self):
        self.run_cli("--ledger", str(self.root), "rates", "set", "USD", "CNY", "7", expect=2)
        bad = self.js("rates", "set", "USD", "CNY", "0", "--source", "x", expect=1)
        self.assertEqual(bad["error"]["code"], "invalid_price")
        bad = self.js("rates", "set", "USD", "USD", "1", "--source", "x", expect=1)
        self.assertEqual(bad["error"]["code"], "invalid_price")
        self.js("rates", "set", "美元", "人民币", "6.7", "--source", "told by the user", "--actor", "agent:t")
        raw = self.js("rates", "list")["data"][0]
        self.assertEqual((raw["base"], raw["quote"]), ("USD", "CNY"))

    def test_update_failure_exits_1_and_says_why(self):
        def boom(*a, **k):
            raise LedgerError("could not reach the rate source: offline", code="rates_unavailable")
        with mock.patch("money_mom.cli.fetch_frankfurter", boom):
            data = self.js("rates", "update", expect=1)
        self.assertEqual(data["error"]["code"], "rates_unavailable")

    def test_balance_in_and_networth(self):
        missing = self.js("balance", "--account", "Assets", "--in", "CNY")["data"]
        self.assertTrue(missing["partial"])
        self.assertEqual(missing["missing"], ["USD"])
        out, _ = self.run_cli("--ledger", str(self.root), "balance", "--account", "Assets", "--in", "人民币")
        self.assertIn("PARTIAL", out)
        self.assertIn("rates update", out)
        self.js("rates", "set", "USD", "CNY", "6.7046", "--source", "test", "--date", "2026-10-06")
        data = self.js("balance", "--account", "Assets", "--in", "CNY")["data"]
        self.assertEqual((data["total"], data["partial"]), ("26704.60", False))
        net = self.js("networth")["data"]
        self.assertEqual((net["total"], net["in"], net["by_currency"]), ("26704.60", "CNY", {"CNY": "20000", "USD": "1000"}))
        out, _ = self.run_cli("--ledger", str(self.root), "networth")
        self.assertIn("Total: 26704.60 CNY", out)
        in_usd = self.js("networth", "--in", "USD")["data"]
        self.assertEqual(in_usd["in"], "USD")
        expected = round_money(D(20000) * (D(1) / D("6.7046")) + D(1000), "USD")  # CNY converted by the inverse rate
        self.assertEqual(in_usd["total"], format(expected, "f"))
        self.assertEqual(self.js("balance", "--in", "$", expect=2)["error"]["code"], "usage_error")

    def test_a_whole_ledger_total_is_omitted_because_it_is_always_zero(self):
        self.js("rates", "set", "USD", "CNY", "6.7046", "--source", "t", "--date", "2026-10-06")
        data = self.js("balance", "--in", "CNY")["data"]
        self.assertIsNone(data["total"])
        self.assertIn("net to zero", data["note"])
        out, _ = self.run_cli("--ledger", str(self.root), "balance", "--in", "CNY")
        self.assertIn("No total", out)
        self.assertNotIn("Total in", out)
        self.assertEqual(self.js("balance", "--account", "Assets", "--in", "CNY")["data"]["total"], "26704.60")

    def test_networth_as_of_uses_the_rate_of_that_day(self):
        self.js("rates", "set", "USD", "CNY", "6.50", "--source", "t", "--date", "2026-09-01")
        self.js("rates", "set", "USD", "CNY", "7.00", "--source", "t", "--date", "2026-10-01")
        old = self.js("networth", "--as-of", "2026-09-15")["data"]
        new = self.js("networth", "--as-of", "2026-10-02")["data"]
        self.assertEqual((old["total"], new["total"]), ("26500.00", "27000.00"))

    def test_doctor_reports_the_rates(self):
        self.assertEqual(self.js("doctor")["data"]["ledger"]["rates"], {"pairs": 0, "latest": None})
        self.js("rates", "set", "USD", "CNY", "6.7", "--source", "t", "--date", "2026-10-06")
        self.assertEqual(self.js("doctor")["data"]["ledger"]["rates"], {"pairs": 1, "latest": "2026-10-06"})


if __name__ == "__main__":
    unittest.main()
