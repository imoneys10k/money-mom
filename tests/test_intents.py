import datetime as dt
import random
import unittest
from decimal import Decimal

from money_mom import Ledger, LedgerError, RuleError
from money_mom.intents import SPECS, postings_with_slots, record_intent, resolve_account
from money_mom.templates import TEMPLATES, apply_template

from helpers import AGENT, HUMAN, LedgerTestCase

D = Decimal
DAY = dt.date(2026, 10, 7)


class IntentCase(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)

    def bal(self, account, ccy="CNY"):
        return self.ledger.state.balance_of(account).get(ccy, D(0))

    def spend(self, amount="38", **kw):
        kw.setdefault("slots", {"from": "支付宝", "category": "咖啡"})
        kw.setdefault("date", "2026-10-07")
        return record_intent(self.ledger, "spend", amount=amount, **kw)

    def resolve(self, term, roots, when=DAY):
        self.ledger.reload()
        return resolve_account(self.ledger.state, self.ledger.aliases(), term, roots, when)


class Rendering(IntentCase):
    def test_direction_and_balance_for_random_amounts(self):
        rng = random.Random(7)
        cases = {
            "spend": ({"from": "Assets:支付宝", "category": "Expenses:餐饮:咖啡"}, "Expenses:餐饮:咖啡", "Assets:支付宝", +1),
            "income": ({"to": "Assets:银行卡", "category": "Income:工资"}, "Assets:银行卡", "Income:工资", +1),
            "transfer": ({"from": "Assets:银行卡", "to": "Assets:现金"}, "Assets:现金", "Assets:银行卡", +1),
        }
        for kind, (slots, plus, minus, _) in cases.items():
            for _ in range(25):
                text = f"{rng.randint(1, 10**7)}.{rng.randint(0, 99):02d}"
                if rng.random() < 0.3:
                    text += str(rng.randint(0, 99)).zfill(2)  # more precision than cents
                before_plus, before_minus = self.bal(plus), self.bal(minus)
                result = record_intent(self.ledger, kind, amount=text, slots=slots, date=DAY)
                self.assertEqual(result.status, "posted")
                self.assertEqual(sum(D(p["amount"]) for p in result.postings), 0)
                self.assertEqual(self.bal(plus) - before_plus, D(text), (kind, text))
                self.assertEqual(self.bal(minus) - before_minus, -D(text), (kind, text))

    def test_amount_must_be_a_positive_exact_number(self):
        for bad in ("0", "0.00", "-5", "abc", "1e3", "1,000", 1.5, True, ""):
            with self.assertRaises(LedgerError, msg=repr(bad)) as ctx:
                self.spend(bad)
            self.assertEqual(ctx.exception.code, "invalid_amount", repr(bad))
        self.spend(D("38.50"))
        self.spend(12)

    def test_currency_defaults_to_the_base_currency(self):
        self.assertEqual(self.spend().ccy, "CNY")
        usd = record_intent(self.ledger, "spend", amount="5", slots={"from": "cash", "category": "咖啡"}, date=DAY, ccy="USD")
        self.assertEqual(usd.ccy, "USD")
        with self.assertRaises(LedgerError):
            self.spend(ccy="usd")

    def test_unknown_kind_or_slot(self):
        with self.assertRaises(LedgerError) as ctx:
            record_intent(self.ledger, "refund", amount="1", slots={}, date=DAY)
        self.assertEqual(ctx.exception.code, "usage_error")
        with self.assertRaises(LedgerError) as ctx:
            self.spend(slots={"from": "支付宝", "to": "咖啡"})
        self.assertEqual(ctx.exception.code, "usage_error")

    def test_bad_dates_and_confidence(self):
        for date in ("2026-13-01", "yesterday"):
            with self.assertRaises(LedgerError):
                self.spend(date=date)
        for conf in (1.5, -0.1, True, "high"):
            with self.assertRaises(LedgerError) as ctx:
                self.spend(confidence=conf)
            self.assertEqual(ctx.exception.code, "invalid_confidence")

    def test_a_transfer_needs_two_different_accounts(self):
        with self.assertRaises(LedgerError) as ctx:
            record_intent(self.ledger, "transfer", amount="1", slots={"from": "现金", "to": "Assets:现金"}, date=DAY)
        self.assertEqual(ctx.exception.code, "usage_error")

    def test_opening_balance_is_a_transfer_from_equity(self):
        result = record_intent(self.ledger, "transfer", amount="20000", slots={"from": "期初", "to": "银行卡"}, date="2026-01-02")
        self.assertEqual(result.status, "posted")
        self.assertEqual(self.bal("Assets:银行卡"), D(20000))
        self.assertEqual(self.bal("Equity:期初余额"), D(-20000))


class Resolution(IntentCase):
    SPEND_FROM = SPECS["spend"]["from"]
    SPEND_CAT = SPECS["spend"]["category"]

    def test_exact_alias_and_segment_matches(self):
        r = self.resolve("Assets:支付宝", self.SPEND_FROM)
        self.assertEqual((r.account, r.matched_by), ("Assets:支付宝", "exact"))
        r = self.resolve("alipay", self.SPEND_FROM)
        self.assertEqual((r.account, r.matched_by), ("Assets:支付宝", "alias"))
        self.assertEqual(self.resolve("ALIPAY", self.SPEND_FROM).account, "Assets:支付宝")
        r = self.resolve("咖啡", self.SPEND_CAT)
        self.assertEqual((r.account, r.matched_by), ("Expenses:餐饮:咖啡", "segment"))
        self.assertEqual(self.resolve("餐饮:咖啡", self.SPEND_CAT).account, "Expenses:餐饮:咖啡")
        self.assertEqual(self.resolve("餐饮", self.SPEND_CAT).account, "Expenses:餐饮")

    def test_aliases_with_capitals_match_any_casing(self):
        self.ledger.set_alias("WeChatPay", "Assets:微信")
        for term in ("WeChatPay", "wechatpay", "WECHATPAY"):
            self.assertEqual(self.resolve(term, self.SPEND_FROM).account, "Assets:微信", term)

    def test_ambiguous_leaf_names_list_their_candidates(self):
        self.ledger.open_account("Expenses:出行:地铁公交", "2026-01-01")
        r = self.resolve("地铁公交", self.SPEND_CAT)
        self.assertEqual(r.reason, "ambiguous")
        self.assertEqual(r.candidates, ["Expenses:交通:地铁公交", "Expenses:出行:地铁公交"])  # code point order
        self.assertEqual(self.resolve("交通:地铁公交", self.SPEND_CAT).account, "Expenses:交通:地铁公交")

    def test_a_substring_is_only_ever_a_suggestion(self):
        r = self.resolve("咖", self.SPEND_CAT)
        self.assertFalse(r.ok)
        self.assertEqual((r.reason, r.candidates), ("approximate", ["Expenses:餐饮:咖啡"]))

    def test_unknown_missing_and_blank(self):
        self.assertEqual(self.resolve("火星基地", self.SPEND_CAT).reason, "unknown")
        for term in (None, "", "   "):
            self.assertEqual(self.resolve(term, self.SPEND_CAT).reason, "missing")

    def test_roles_keep_the_wrong_kind_of_account_out(self):
        r = self.resolve("Assets:现金", self.SPEND_CAT)
        self.assertEqual(r.reason, "wrong_type")
        self.assertEqual(self.resolve("餐饮", self.SPEND_FROM).reason, "unknown")  # the expense is not a source
        self.assertEqual(self.resolve("信用卡", self.SPEND_FROM).account, "Liabilities:信用卡")
        self.assertEqual(self.resolve("cash", SPECS["spend"]["category"]).reason, "wrong_type")

    def test_dates_decide_whether_an_account_is_available(self):
        self.ledger.open_account("Assets:新卡", "2026-06-01")
        self.assertEqual(self.resolve("新卡", self.SPEND_FROM, dt.date(2026, 5, 31)).reason, "unknown")
        self.assertEqual(self.resolve("Assets:新卡", self.SPEND_FROM, dt.date(2026, 5, 31)).reason, "unavailable")
        self.assertTrue(self.resolve("新卡", self.SPEND_FROM, dt.date(2026, 6, 1)).ok)
        self.ledger.close_account("Assets:新卡", "2026-08-01")
        self.assertTrue(self.resolve("新卡", self.SPEND_FROM, dt.date(2026, 8, 1)).ok)
        self.assertEqual(self.resolve("Assets:新卡", self.SPEND_FROM, dt.date(2026, 8, 2)).reason, "unavailable")

    def test_an_alias_to_a_missing_account_is_reported(self):
        (self.root / "aliases.toml").write_text('[aliases]\n"幽灵" = "Assets:不存在"\n', encoding="utf-8")
        r = self.resolve("幽灵", self.SPEND_FROM)
        self.assertEqual(r.reason, "unknown")
        self.assertIn("does not exist", r.message)


class Recording(IntentCase):
    def test_resolved_entries_are_posted_with_provenance(self):
        result = self.spend(payee="瑞幸", narration="午饭", actor=AGENT, confidence=0.96,
                            source={"type": "chat", "ref": "午饭瑞幸 38"}, import_hash="h1")
        self.assertEqual(result.status, "posted")
        self.assertTrue(result.written)
        ev = result.event
        self.assertEqual(ev.meta["intent"], "spend")
        self.assertEqual(ev.meta["given"], {"from": "支付宝", "category": "咖啡"})
        self.assertEqual((ev.payee, ev.narration, ev.import_hash), ("瑞幸", "午饭", "h1"))
        self.assertEqual((ev.actor_type, ev.confidence, ev.source["ref"]), ("agent", 0.96, "午饭瑞幸 38"))
        self.assertEqual(self.bal("Expenses:餐饮:咖啡"), D(38))
        with self.assertRaises(RuleError) as ctx:
            self.spend(import_hash="h1")
        self.assertEqual(ctx.exception.code, "duplicate_import")

    def test_the_confidence_threshold(self):
        self.assertEqual(self.spend(confidence=0.9).status, "posted")  # equal to the threshold
        self.assertEqual(self.spend(confidence=1).status, "posted")
        low = self.spend(confidence=0.89)
        self.assertEqual(low.status, "pending")
        self.assertIn("below the auto-post threshold", low.reasons[0])
        self.assertEqual(self.bal("Expenses:餐饮:咖啡"), D(76))  # the pending one did not count
        self.assertEqual(self.spend().status, "posted")  # no confidence means a person typed it

    def test_the_threshold_comes_from_the_config(self):
        path = self.root / "money-mom.toml"
        path.write_text(path.read_text(encoding="utf-8") + "auto_post_confidence = 0.5\n", encoding="utf-8")
        led = Ledger.open(self.root, clock=self.clock)
        self.assertEqual(led.auto_post_confidence, 0.5)
        kw = dict(amount="1", slots={"from": "现金", "category": "咖啡"}, date=DAY)
        self.assertEqual(record_intent(led, "spend", confidence=0.6, **kw).status, "posted")
        self.assertEqual(record_intent(led, "spend", confidence=0.4, **kw).status, "pending")

    def test_bad_threshold_values_are_rejected(self):
        for bad in ("1.5", "-0.1", '"high"', "true"):
            path = self.root / "money-mom.toml"
            base = path.read_text(encoding="utf-8").split("auto_post_confidence")[0].rstrip("# \n") + "\n"
            path.write_text(base + f"auto_post_confidence = {bad}\n", encoding="utf-8")
            with self.assertRaises(LedgerError, msg=bad) as ctx:
                Ledger.open(self.root)
            self.assertEqual(ctx.exception.code, "invalid_config")

    def test_unresolved_names_become_pending_with_candidates_attached(self):
        result = self.spend(slots={"from": "微信", "category": "咖"}, confidence=0.95)
        self.assertEqual(result.status, "pending")
        self.assertEqual([p["account"] for p in result.postings], [None, "Assets:微信"])
        self.assertEqual(result.unresolved[0]["candidates"], ["Expenses:餐饮:咖啡"])
        self.assertEqual(result.event.meta["unresolved"][0]["slot"], "category")
        self.assertEqual(self.ledger.state.balances(), {})

    def test_missing_slots_become_pending(self):
        both = record_intent(self.ledger, "spend", amount="9", slots={}, date=DAY)
        self.assertEqual(both.status, "pending")
        self.assertEqual({u["slot"] for u in both.unresolved}, {"from", "category"})
        self.assertTrue(all(u["reason"] == "missing" for u in both.unresolved))

    def test_strict_refuses_instead_of_recording(self):
        before = self.total_lines()
        with self.assertRaises(RuleError) as ctx:
            self.spend(slots={"from": "支付宝", "category": "咖"}, strict=True)
        self.assertEqual(ctx.exception.code, "unresolved_account")
        self.assertEqual(ctx.exception.details["unresolved"][0]["candidates"], ["Expenses:餐饮:咖啡"])
        self.assertEqual(self.total_lines(), before)

    def test_dry_run_writes_nothing_but_reports_everything(self):
        before = self.event_files()
        result = self.spend(dry_run=True, confidence=0.5)
        self.assertFalse(result.written)
        self.assertEqual(result.status, "pending")
        self.assertEqual(result.postings[0], {"account": "Expenses:餐饮:咖啡", "amount": "38", "ccy": "CNY"})
        self.assertEqual(self.event_files(), before)

    def test_an_account_that_forbids_the_currency_is_a_hard_error(self):
        self.ledger.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])
        with self.assertRaises(RuleError) as ctx:
            self.spend(slots={"from": "美元户", "category": "咖啡"})
        self.assertEqual(ctx.exception.code, "currency_not_allowed")

    def test_locked_periods_apply_to_intents_too(self):
        self.fund_card = record_intent(self.ledger, "transfer", amount="1000", slots={"from": "期初", "to": "银行卡"}, date="2026-10-01")
        self.ledger.assert_balance("Assets:银行卡", "2026-10-05", "1000", "CNY")
        kw = dict(amount="5", slots={"from": "银行卡", "category": "咖啡"}, date="2026-10-02")
        with self.assertRaises(RuleError) as ctx:
            record_intent(self.ledger, "spend", **kw)
        self.assertEqual(ctx.exception.code, "period_locked")
        with self.assertRaises(RuleError) as ctx:
            record_intent(self.ledger, "spend", actor=AGENT, confidence=1, meta={"override_lock": "pls"}, **kw)
        self.assertEqual(ctx.exception.code, "lock_override_denied")
        record_intent(self.ledger, "spend", meta={"override_lock": "forgot it"}, **kw)

    def test_closed_accounts_are_not_chosen(self):
        self.ledger.open_account("Assets:旧卡", "2026-01-01")
        self.ledger.close_account("Assets:旧卡", "2026-03-01")
        result = self.spend(slots={"from": "旧卡", "category": "咖啡"}, date="2026-10-07")
        self.assertEqual(result.status, "pending")
        self.assertEqual(result.unresolved[0]["reason"], "unknown")


class ConfirmingBySlot(IntentCase):
    def pending(self):
        return self.spend(slots={"from": "支付宝", "category": "吃饭"}, confidence=0.9)

    def test_filling_the_missing_slot_then_confirming(self):
        ev = self.pending().event
        postings = postings_with_slots(self.ledger, ev.id, {"category": "餐饮:聚餐"})
        self.assertEqual(postings[0]["account"], "Expenses:餐饮:聚餐")
        self.assertEqual(postings[1]["account"], "Assets:支付宝")
        self.ledger.confirm(ev.id, [(p["account"], p["amount"], p["ccy"]) for p in postings])
        self.assertEqual(self.bal("Expenses:餐饮:聚餐"), D(38))

    def test_income_slots_map_to_the_right_postings(self):
        ev = record_intent(self.ledger, "income", amount="500", slots={"to": "银行卡"}, date=DAY).event
        postings = postings_with_slots(self.ledger, ev.id, {"category": "奖金"})
        self.assertEqual([p["account"] for p in postings], ["Assets:银行卡", "Income:奖金"])
        self.assertEqual([p["amount"] for p in postings], ["500", "-500"])

    def test_errors(self):
        ev = self.pending().event
        with self.assertRaises(RuleError) as ctx:
            postings_with_slots(self.ledger, ev.id, {"category": "吃饭"})
        self.assertEqual(ctx.exception.code, "unresolved_account")
        with self.assertRaises(LedgerError) as ctx:
            postings_with_slots(self.ledger, ev.id, {"to": "银行卡"})
        self.assertEqual(ctx.exception.code, "usage_error")
        with self.assertRaises(RuleError) as ctx:
            postings_with_slots(self.ledger, "nope", {"category": "咖啡"})
        self.assertEqual(ctx.exception.code, "unknown_target")
        plain = self.ledger.add_txn("2026-10-07", [("Expenses:其他支出", "1", "CNY"), ("Assets:现金", "-1", "CNY")])
        with self.assertRaises(LedgerError) as ctx:
            postings_with_slots(self.ledger, plain.id, {"category": "咖啡"})
        self.assertEqual(ctx.exception.code, "usage_error")


class Aliases(IntentCase):
    def test_add_list_replace_remove(self):
        self.ledger.set_alias("招行卡", "Assets:银行卡")
        self.assertEqual(self.ledger.aliases()["招行卡"], "Assets:银行卡")
        self.ledger.set_alias("Alipay", "Assets:现金")  # same word as the template's lower-case alias
        self.assertNotIn("alipay", self.ledger.aliases())
        self.assertEqual(self.ledger.aliases()["Alipay"], "Assets:现金")
        self.ledger.remove_alias("ALIPAY")
        self.assertNotIn("Alipay", self.ledger.aliases())
        with self.assertRaises(LedgerError) as ctx:
            self.ledger.remove_alias("never-existed")
        self.assertEqual(ctx.exception.code, "unknown_alias")

    def test_validation(self):
        with self.assertRaises(LedgerError) as ctx:
            self.ledger.set_alias("x", "Assets:不存在")
        self.assertEqual(ctx.exception.code, "unknown_account")
        for bad in ("", "  ", "a\nb", "x" * 65):
            with self.assertRaises(LedgerError, msg=repr(bad)) as ctx:
                self.ledger.set_alias(bad, "Assets:现金")
            self.assertEqual(ctx.exception.code, "invalid_alias")

    def test_the_file_is_valid_toml_and_leaves_no_temp_files(self):
        import tomllib
        self.ledger.set_alias('带"引号"的 名字', "Assets:现金")
        data = tomllib.loads((self.root / "aliases.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["aliases"]['带"引号"的 名字'], "Assets:现金")
        self.assertEqual([p.name for p in self.root.iterdir() if "tmp" in p.name], [])

    def test_a_broken_aliases_file_is_reported(self):
        (self.root / "aliases.toml").write_text("[aliases\n", encoding="utf-8")
        with self.assertRaises(LedgerError) as ctx:
            self.ledger.aliases()
        self.assertEqual(ctx.exception.code, "invalid_config")
        (self.root / "aliases.toml").write_text("[aliases]\nx = 1\n", encoding="utf-8")
        with self.assertRaises(LedgerError):
            self.ledger.aliases()


class Templates(LedgerTestCase):
    def test_every_template_is_valid_and_resolvable(self):
        for name, template in TEMPLATES.items():
            with self.subTest(template=name):
                led = Ledger.init(self.root.parent / f"t-{name}", clock=self.clock)
                count = apply_template(led, name, "2026-01-01", HUMAN)
                self.assertEqual(count, len(template["accounts"]))
                self.assertEqual(led.check(), [])
                for alias, target in led.aliases().items():
                    self.assertIn(target, led.state.accounts, alias)
                # names only need to be unique among accounts that compete for the same slot
                for roots in (("Assets", "Liabilities", "Equity"), ("Expenses",), ("Income",)):
                    leaves = [a.split(":")[-1].casefold() for a in template["accounts"] if a.split(":")[0] in roots]
                    self.assertEqual(len(leaves), len(set(leaves)), f"duplicate leaf names under {roots}")

    def test_applying_twice_changes_nothing(self):
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)
        before = self.event_files()
        with self.assertRaises(RuleError) as ctx:
            apply_template(self.ledger, "cn", "2026-01-01", HUMAN)
        self.assertEqual(ctx.exception.code, "account_exists")
        self.assertEqual(self.event_files(), before)

    def test_unknown_template(self):
        with self.assertRaises(LedgerError) as ctx:
            apply_template(self.ledger, "klingon", "2026-01-01", HUMAN)
        self.assertEqual(ctx.exception.code, "unknown_template")

    def test_english_template_end_to_end(self):
        apply_template(self.ledger, "en", "2026-01-01", HUMAN)
        result = record_intent(self.ledger, "spend", amount="4.50", slots={"from": "cash", "category": "coffee"}, date=DAY)
        self.assertEqual([p["account"] for p in result.postings], ["Expenses:Food:Coffee", "Assets:Cash"])


if __name__ == "__main__":
    unittest.main()
