import random
import unittest
from decimal import Decimal

from money_mom import LedgerError, RuleError
from money_mom.intents import postings_with_slots, record_exchange
from money_mom.templates import apply_template

from helpers import AGENT, HUMAN, LedgerTestCase
from test_cli import CliTestCase

D = Decimal


def err(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except LedgerError as e:
        return e
    raise AssertionError("expected a LedgerError")


class ExchangeCase(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)
        self.ledger.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])
        self.ledger.open_account("Assets:港币户", "2026-01-01", currencies=["HKD"])
        self.ledger.add_txn("2026-01-02", [("Assets:美元户", "5000", "USD"), ("Equity:期初余额", "-5000", "USD")])
        self.ledger.add_txn("2026-01-02", [("Assets:银行卡", "50000", "CNY"), ("Equity:期初余额", "-50000", "CNY")])

    def exchange(self, give="100 USD", get="720 CNY", **kw):
        kw.setdefault("slots", {"from": "美元户", "to": "银行卡"})
        kw.setdefault("date", "2026-10-07")
        return record_exchange(self.ledger, give=give, get=get, **kw)

    def bal(self, account, ccy):
        return self.ledger.state.balance_of(account).get(ccy, D(0))


class Recording(ExchangeCase):
    def test_four_postings_and_each_currency_balances_on_its_own(self):
        result = self.exchange()
        self.assertEqual(result.status, "posted")
        self.assertEqual(
            [(p["account"], p["amount"], p["ccy"]) for p in result.postings],
            [("Assets:银行卡", "720", "CNY"), ("Equity:汇兑", "-720", "CNY"),
             ("Equity:汇兑", "100", "USD"), ("Assets:美元户", "-100", "USD")],
        )
        for ccy in ("USD", "CNY"):
            self.assertEqual(sum(D(p["amount"]) for p in result.postings if p["ccy"] == ccy), 0)

    def test_balances_move_the_right_way(self):
        self.exchange()
        self.assertEqual(self.bal("Assets:美元户", "USD"), D(4900))
        self.assertEqual(self.bal("Assets:银行卡", "CNY"), D(50720))
        self.assertEqual(self.bal("Equity:汇兑", "USD"), D(100))
        self.assertEqual(self.bal("Equity:汇兑", "CNY"), D(-720))
        self.assertEqual(self.ledger.check(), [])

    def test_the_rate_obtained_is_kept_and_nothing_is_assumed(self):
        result = self.exchange(give="100 USD", get="671.25 CNY")
        self.assertEqual(result.details["rate"], "1 USD = 6.7125 CNY")
        self.assertEqual(result.event.meta["exchange"]["rate"], "6.7125")
        self.assertEqual(result.event.meta["intent"], "exchange")
        third = self.exchange(give="3 USD", get="20 CNY")
        self.assertEqual(third.details["rate"], "1 USD = 6.66666667 CNY")  # rounded to 8 places

    def test_currencies_can_come_from_the_accounts(self):
        result = self.exchange(give="100", get="720")
        self.assertEqual((result.details["give"]["ccy"], result.details["get"]["ccy"]), ("USD", "CNY"))
        self.assertEqual(result.currency["give"], "account")
        hk = self.exchange(give="1000", get="918", slots={"from": "银行卡", "to": "港币户"})
        self.assertEqual((hk.details["give"]["ccy"], hk.details["get"]["ccy"]), ("CNY", "HKD"))

    def test_explicit_currency_flags(self):
        result = self.exchange(give="100", get="720", give_ccy="usd", get_ccy="人民币")
        self.assertEqual((result.details["give"]["ccy"], result.details["get"]["ccy"]), ("USD", "CNY"))

    def test_direction_for_random_amounts(self):
        rng = random.Random(3)
        for _ in range(20):
            give = f"{rng.randint(1, 900)}.{rng.randint(0, 99):02d}"
            get = f"{rng.randint(1, 9000)}.{rng.randint(0, 99):02d}"
            before = (self.bal("Assets:美元户", "USD"), self.bal("Assets:银行卡", "CNY"))
            self.exchange(give=f"{give} USD", get=f"{get} CNY")
            self.assertEqual(self.bal("Assets:美元户", "USD") - before[0], -D(give))
            self.assertEqual(self.bal("Assets:银行卡", "CNY") - before[1], D(get))

    def test_it_needs_two_different_currencies(self):
        e = err(self.exchange, give="100", get="720", slots={"from": "现金", "to": "银行卡"})
        self.assertEqual(e.code, "usage_error")
        self.assertIn("two different currencies", e.message)
        self.assertEqual(err(self.exchange, give="100 USD", get="720 USD").code, "usage_error")

    def test_same_account_is_refused(self):
        self.assertEqual(err(self.exchange, slots={"from": "银行卡", "to": "Assets:银行卡"}).code, "usage_error")

    def test_amounts_must_be_positive_text(self):
        for give in ("0 USD", "-5 USD", 1.5, "abc"):
            self.assertEqual(err(self.exchange, give=give).code, "invalid_amount", repr(give))

    def test_a_ledger_without_a_conversion_account_is_told_how_to_get_one(self):
        from money_mom import Ledger
        bare = Ledger.init(self.root.parent / "bare", clock=self.clock)
        for account, ccys in (("Assets:A", ["USD"]), ("Assets:B", None)):
            bare.open_account(account, "2026-01-01", currencies=ccys)
        e = err(record_exchange, bare, give="1 USD", get="7 CNY", slots={"from": "A", "to": "B"}, date="2026-10-07")
        self.assertEqual(e.code, "no_conversion_account")
        self.assertIn("money-mom open Equity:汇兑", e.message)
        bare.open_account("Equity:Conversions", "2026-01-01")
        record_exchange(bare, give="1 USD", get="7 CNY", slots={"from": "A", "to": "B"}, date="2026-10-07")  # works now


class Uncertainty(ExchangeCase):
    def test_unresolved_accounts_become_pending(self):
        result = self.exchange(slots={"from": "美元户", "to": "不存在的账户"})
        self.assertEqual(result.status, "pending")
        self.assertIsNone(result.postings[0]["account"])
        self.assertEqual(self.bal("Assets:美元户", "USD"), D(5000))  # pending does not count

    def test_strict_refuses(self):
        e = err(self.exchange, slots={"from": "美元户"}, strict=True)
        self.assertEqual(e.code, "unresolved_account")

    def test_low_confidence_waits(self):
        result = self.exchange(confidence=0.5, actor=AGENT)
        self.assertEqual(result.status, "pending")
        self.assertEqual(self.exchange(confidence=0.95, actor=AGENT).status, "posted")

    def test_a_dollar_sign_is_never_guessed_when_two_dollars_are_in_use(self):
        e = err(self.exchange, give="$100", get="720 CNY", slots={"from": "现金", "to": "银行卡"})
        self.assertEqual((e.code, e.details["candidates"]), ("ambiguous_currency", ["USD", "HKD"]))

    def test_an_inferred_currency_waits_for_confirmation(self):
        from money_mom import Ledger
        led = Ledger.init(self.root.parent / "usd-only", clock=self.clock)
        apply_template(led, "cn", "2026-01-01", HUMAN)
        led.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])  # the only dollar in this ledger
        result = record_exchange(led, give="$100", get="720 CNY", slots={"from": "现金", "to": "银行卡"}, date="2026-10-07")
        self.assertEqual((result.details["give"]["ccy"], result.status, result.currency["inferred"]), ("USD", "pending", True))
        self.assertIn("please confirm", result.reasons[0])
        led.confirm(result.event.id)  # every account is resolved, so it can be accepted as it stands
        self.assertEqual(led.state.txns[result.event.id].status, "posted")

    def test_dry_run_writes_nothing(self):
        before = self.event_files()
        result = self.exchange(dry_run=True)
        self.assertFalse(result.written)
        self.assertEqual(result.details["get"], {"amount": "720", "ccy": "CNY"})
        self.assertEqual(self.event_files(), before)

    def test_confirming_by_slot_fills_the_right_postings(self):
        result = self.exchange(slots={"from": "美元户", "to": "不存在的账户"}, confidence=1, actor=AGENT)
        postings = postings_with_slots(self.ledger, result.event.id, {"to": "银行卡"})
        self.assertEqual(postings[0]["account"], "Assets:银行卡")
        self.assertEqual(postings[3]["account"], "Assets:美元户")
        self.ledger.confirm(result.event.id, [(p["account"], p["amount"], p["ccy"]) for p in postings])
        self.assertEqual(self.bal("Assets:银行卡", "CNY"), D(50720))

    def test_confirming_the_source_slot_touches_only_the_source_posting(self):
        result = self.exchange(slots={"from": "不存在的账户", "to": "银行卡"})
        self.assertEqual(result.status, "pending")
        postings = postings_with_slots(self.ledger, result.event.id, {"from": "美元户"})
        self.assertEqual([p["account"] for p in postings], ["Assets:银行卡", "Equity:汇兑", "Equity:汇兑", "Assets:美元户"])
        self.ledger.confirm(result.event.id, [(p["account"], p["amount"], p["ccy"]) for p in postings])
        self.assertEqual(self.bal("Assets:美元户", "USD"), D(4900))
        self.assertEqual(self.ledger.check(), [])

    def test_the_intent_commands_do_not_accept_an_exchange(self):
        from money_mom.intents import record_intent
        self.assertEqual(err(record_intent, self.ledger, "exchange", amount="1", slots={}, date="2026-10-07").code, "usage_error")


class Cli(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.js("open", "Assets:美元户", "--date", "2026-01-01", "--currency", "USD")
        self.js("transfer", "5000", "--from", "期初", "--to", "美元户", "--date", "2026-01-02")

    def test_exchange_command(self):
        data = self.js("exchange", "100 USD", "720 CNY", "--from", "美元户", "--to", "银行卡")["data"]
        self.assertEqual((data["status"], data["kind"], data["details"]["rate"]), ("posted", "exchange", "1 USD = 7.2 CNY"))
        out, _ = self.run_cli("--ledger", str(self.root), "exchange", "10 USD", "72 CNY", "--from", "美元户", "--to", "银行卡")
        self.assertIn("Recorded exchange of 10 USD for 72 CNY", out)
        balances = {(r["account"], r["ccy"]): r["amount"] for r in self.js("balance")["data"]["balances"]}
        self.assertEqual(balances[("Assets:美元户", "USD")], "4890")
        self.assertEqual(balances[("Assets:银行卡", "CNY")], "792")

    def test_flags_avoid_shell_quoting_of_dollar_signs(self):
        data = self.js("exchange", "100", "720", "--give-ccy", "USD", "--get-ccy", "CNY", "--from", "美元户", "--to", "银行卡")["data"]
        self.assertEqual((data["details"]["give"]["ccy"], data["details"]["get"]["ccy"]), ("USD", "CNY"))

    def test_dry_run_and_errors(self):
        dry = self.js("exchange", "100", "720", "--from", "美元户", "--to", "银行卡", "--dry-run")["data"]
        self.assertFalse(dry["written"])
        out, _ = self.run_cli("--ledger", str(self.root), "exchange", "100", "720", "--from", "美元户", "--to", "银行卡", "--dry-run")
        self.assertIn("would record a posted exchange of 100 USD for 720 CNY", out)
        same = self.js("exchange", "100 USD", "720 USD", "--from", "美元户", "--to", "银行卡", expect=2)
        self.assertEqual(same["error"]["code"], "usage_error")
        self.run_cli("--ledger", str(self.root), "exchange", "100", expect=2)  # both amounts are required

    def test_networth_after_an_exchange(self):
        self.js("exchange", "1000 USD", "6700 CNY", "--from", "美元户", "--to", "银行卡")
        self.js("rates", "set", "USD", "CNY", "6.7", "--source", "test", "--date", "2026-10-06")
        net = self.js("networth")["data"]
        self.assertEqual(net["by_currency"], {"CNY": "6700", "USD": "4000"})
        self.assertEqual(net["total"], "33500.00")


if __name__ == "__main__":
    unittest.main()
