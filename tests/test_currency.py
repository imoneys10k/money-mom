import unittest

from money_mom import LedgerError
from money_mom.currency import Money, choose_currency, parse_currency, parse_money
from money_mom.intents import record_intent
from money_mom.templates import apply_template

from helpers import HUMAN, LedgerTestCase
from test_cli import CliTestCase


def err(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except LedgerError as e:
        return e
    raise AssertionError("expected a LedgerError")


class ParseMoney(unittest.TestCase):
    def test_what_people_write(self):
        cases = {
            "38": ("38", ()), "38.50": ("38.50", ()), "1,200": ("1200", ()), "1,200.50元": ("1200.50", ()),
            "38块": ("38", ()), "５美元": ("5", ("USD",)), "5美元": ("5", ("USD",)), "5刀": ("5", ("USD",)),
            "5 USD": ("5", ("USD",)), "usd 5": ("5", ("USD",)), "US$5": ("5", ("USD",)),
            "HK$200": ("200", ("HKD",)), "HK＄200": ("200", ("HKD",)), "200港币": ("200", ("HKD",)),
            "NT$300": ("300", ("TWD",)), "A$10": ("10", ("AUD",)), "€12.5": ("12.5", ("EUR",)),
            "£3": ("3", ("GBP",)), "5 yen": ("5", ("JPY",)), "5 RMB": ("5", ("CNY",)), "1,000人民币": ("1000", ("CNY",)),
            "$5": ("5", ("USD", "HKD", "CAD", "AUD", "SGD", "NZD", "MXN")),
            "3 dollars": ("3", ("USD", "HKD", "CAD", "AUD", "SGD", "NZD", "MXN")),
            "¥38": ("38", ("CNY", "JPY")), "￥38": ("38", ("CNY", "JPY")), "¥38元": ("38", ("CNY", "JPY")),
        }
        for text, (amount, candidates) in cases.items():
            got = parse_money(text)
            self.assertEqual((got.amount, got.candidates), (amount, candidates), text)

    def test_ccy_property_is_only_set_when_unambiguous(self):
        self.assertEqual(parse_money("5美元").ccy, "USD")
        self.assertIsNone(parse_money("$5").ccy)
        self.assertIsNone(parse_money("38").ccy)

    def test_a_generic_unit_asserts_no_currency(self):
        for text in ("38元", "38块", "38块钱", "38圆"):
            self.assertEqual(parse_money(text).candidates, (), text)

    def test_bad_text(self):
        for text, code in {
            "": "invalid_amount", "abc": "invalid_amount", "元": "invalid_amount", "5-6": "invalid_amount",
            "1.200,50": "invalid_amount", "1,5": "invalid_amount", "-5": "invalid_amount", "−5": "invalid_amount",
            "$5 USD": "invalid_amount", "5美元港币": "unknown_currency", "5 xyz": "unknown_currency",
            "5 AAPL": "unknown_currency",
        }.items():
            self.assertEqual(err(parse_money, text).code, code, text)

    def test_parse_currency_alone(self):
        self.assertEqual(parse_currency("usd"), ("USD",))
        self.assertEqual(parse_currency("美元"), ("USD",))
        self.assertEqual(parse_currency("$")[:2], ("USD", "HKD"))
        self.assertEqual(parse_currency("元"), ())
        self.assertEqual(err(parse_currency, "AAPL").code, "unknown_currency")


class Choose(unittest.TestCase):
    def choose(self, text=None, flag=None, accounts=(), used=(), base="CNY"):
        return choose_currency(
            text=parse_money(text) if text else None, flag=parse_currency(flag) if flag else None,
            accounts=list(accounts), used=used, base=base,
        )

    def test_explicit_things_win(self):
        self.assertEqual(self.choose("5美元").matched_by, "text")
        self.assertEqual(self.choose("5", flag="usd").matched_by, "flag")
        self.assertEqual(self.choose("HK$5", flag="$").ccy, "HKD")  # the explicit one narrows the ambiguous one

    def test_an_explicit_currency_must_agree_with_everything(self):
        self.assertEqual(err(self.choose, "5 USD", flag="HKD").code, "currency_conflict")
        self.assertEqual(err(self.choose, "5 USD", accounts=[("Assets:A", ("CNY",))]).code, "currency_conflict")
        self.assertEqual(err(self.choose, "5", flag="USD", accounts=[("Assets:A", ("CNY",))]).code, "currency_conflict")

    def test_an_ambiguous_symbol_is_narrowed_by_the_accounts(self):
        got = self.choose("$5", accounts=[("Assets:美元户", ("USD",))])
        self.assertEqual((got.ccy, got.matched_by, got.inferred), ("USD", "account", False))
        got = self.choose("$5", accounts=[("Assets:A", ("USD", "HKD")), ("Assets:B", ("HKD", "EUR"))])
        self.assertEqual(got.ccy, "HKD")
        self.assertEqual(err(self.choose, "¥5", accounts=[("Assets:A", ("USD",))]).code, "currency_conflict")

    def test_an_ambiguous_symbol_falls_back_to_the_ledger_but_only_flags_non_base_picks(self):
        usd = self.choose("$5", used={"CNY", "USD"})
        self.assertEqual((usd.ccy, usd.matched_by, usd.inferred), ("USD", "ledger", True))
        yuan = self.choose("¥38", used={"CNY"})  # in a CNY ledger a bare yen sign means the base currency
        self.assertEqual((yuan.ccy, yuan.matched_by, yuan.inferred), ("CNY", "ledger", False))
        hkd_base = self.choose("$5", used={"HKD"}, base="HKD")
        self.assertEqual((hkd_base.ccy, hkd_base.inferred), ("HKD", False))

    def test_an_ambiguous_symbol_with_no_way_to_decide_is_an_error_that_lists_candidates(self):
        e = err(self.choose, "$5", used={"CNY"})
        self.assertEqual(e.code, "ambiguous_currency")
        self.assertEqual(e.details["candidates"][:2], ["USD", "HKD"])
        e = err(self.choose, "$5", used={"USD", "HKD", "CNY"})
        self.assertEqual(e.details["candidates"], ["USD", "HKD"])  # only the ones actually in use
        e = err(self.choose, "¥5", used={"CNY", "JPY"})
        self.assertEqual(e.details["candidates"], ["CNY", "JPY"])

    def test_nothing_said(self):
        self.assertEqual(self.choose("38").ccy, "CNY")
        self.assertEqual(self.choose().matched_by, "default")
        got = self.choose("38", accounts=[("Assets:美元户", ("USD",)), ("Expenses:X", None)])
        self.assertEqual((got.ccy, got.matched_by), ("USD", "account"))
        self.assertEqual(self.choose("38", accounts=[("A", ("CNY", "USD"))]).ccy, "CNY")  # base is allowed
        self.assertEqual(err(self.choose, "38", accounts=[("A", ("USD",)), ("B", ("CNY",))]).code, "currency_conflict")
        e = err(self.choose, "38", accounts=[("A", ("USD", "HKD"))])
        self.assertEqual((e.code, e.details["candidates"]), ("ambiguous_currency", ["HKD", "USD"]))
        self.assertEqual(self.choose("38", base="HKD").ccy, "HKD")


class InIntents(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)

    def spend(self, amount, **kw):
        slots = kw.pop("slots", {"from": "现金", "category": "咖啡"})
        return record_intent(self.ledger, "spend", amount=amount, slots=slots, date="2026-10-07", **kw)

    def test_currency_written_with_the_amount(self):
        for text, ccy, amount in (("5美元", "USD", "5"), ("HK$200", "HKD", "200"), ("1,200 EUR", "EUR", "1200"), ("38元", "CNY", "38")):
            result = self.spend(text)
            self.assertEqual((result.ccy, result.amount, result.status), (ccy, amount, "posted"), text)
            self.assertTrue(all(p["ccy"] == ccy for p in result.postings))

    def test_how_it_was_decided_is_recorded(self):
        ev = self.spend("HK$200").event
        self.assertEqual(ev.meta["currency"], {"matched_by": "text", "text": "HK$"})
        self.assertEqual(self.spend("38").event.meta["currency"], {"matched_by": "default"})

    def test_the_account_decides_when_nothing_is_said(self):
        self.ledger.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])
        result = self.spend("5", slots={"from": "美元户", "category": "咖啡"})
        self.assertEqual((result.ccy, result.currency["matched_by"], result.status), ("USD", "account", "posted"))

    def test_an_ambiguous_symbol_is_never_guessed(self):
        e = err(self.spend, "$5")
        self.assertEqual(e.code, "ambiguous_currency")
        self.ledger.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])
        self.assertEqual(self.spend("$5", slots={"from": "美元户", "category": "咖啡"}).ccy, "USD")  # the account settles it

    def test_a_pick_from_the_ledger_waits_for_confirmation_unless_it_is_the_base_currency(self):
        self.ledger.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])
        usd = self.spend("$5")  # USD is now the only dollar in the ledger, but it is a guess
        self.assertEqual((usd.ccy, usd.status, usd.currency["inferred"]), ("USD", "pending", True))
        self.assertIn("please confirm", usd.reasons[0])
        yuan = self.spend("¥38")
        self.assertEqual((yuan.ccy, yuan.status, yuan.currency["matched_by"]), ("CNY", "posted", "ledger"))

    def test_conflicts_are_hard_errors(self):
        self.ledger.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])
        self.assertEqual(err(self.spend, "5 HKD", slots={"from": "美元户", "category": "咖啡"}).code, "currency_conflict")
        self.assertEqual(err(self.spend, "5 USD", ccy="HKD").code, "currency_conflict")

    def test_floats_and_bad_text_are_still_refused(self):
        self.assertEqual(err(self.spend, 1.5).code, "invalid_amount")
        self.assertEqual(err(self.spend, "5 xyz").code, "unknown_currency")

    def test_each_currency_keeps_its_own_balance(self):
        self.spend("5美元")
        self.spend("38")
        balances = self.ledger.state.balance_of("Expenses:餐饮:咖啡")
        self.assertEqual({c: str(a) for c, a in balances.items()}, {"USD": "5", "CNY": "38"})


class Cli(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.js("open", "Assets:美元户", "--date", "2026-01-01", "--currency", "USD")

    def test_currency_command(self):
        got = self.js("currency", "HK$200")["data"]
        self.assertEqual((got["amount"], got["ccy"], got["matched_by"]), ("200", "HKD", "text"))
        got = self.js("currency", "$5", "--account", "美元户")["data"]
        self.assertEqual((got["ccy"], got["matched_by"], got["candidates"][0]), ("USD", "account", "USD"))
        got = self.js("currency", "$5")["data"]
        self.assertEqual((got["ccy"], got["inferred"]), ("USD", True))
        got = self.js("currency", "38")["data"]
        self.assertEqual((got["ccy"], got["matched_by"]), ("CNY", "default"))
        out, _ = self.run_cli("--ledger", str(self.root), "currency", "5美元")
        self.assertIn("5 USD", out)

    def test_currency_command_errors(self):
        self.assertEqual(self.js("currency", "5 xyz", expect=1)["error"]["code"], "unknown_currency")
        self.assertEqual(self.js("currency", "5 USD", "--ccy", "HKD", expect=1)["error"]["code"], "currency_conflict")
        self.assertEqual(self.js("currency", "5", "--account", "不存在", expect=1)["error"]["code"], "unresolved_account")

    def test_spend_with_a_currency_in_the_amount(self):
        data = self.js("spend", "5美元", "--from", "现金", "--category", "咖啡")["data"]
        self.assertEqual((data["ccy"], data["amount"], data["currency"]["matched_by"]), ("USD", "5", "text"))
        data = self.js("spend", "5", "--from", "美元户", "--category", "咖啡")["data"]
        self.assertEqual((data["ccy"], data["currency"]["matched_by"]), ("USD", "account"))
        data = self.js("spend", "$5", "--from", "现金", "--category", "咖啡", "--actor", "agent:t", "--confidence", "1")["data"]
        self.assertEqual((data["status"], data["currency"]["inferred"]), ("pending", True))
        refused = self.js("spend", "5 EUR", "--from", "美元户", "--category", "咖啡", expect=1)
        self.assertEqual(refused["error"]["code"], "currency_conflict")

    def test_doctor_lists_the_currencies_in_use(self):
        self.js("spend", "5美元", "--from", "现金", "--category", "咖啡")
        self.assertEqual(self.js("doctor")["data"]["ledger"]["currencies"], ["CNY", "USD"])


if __name__ == "__main__":
    unittest.main()
