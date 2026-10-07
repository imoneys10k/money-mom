import json
import unittest

from money_mom.templates import TEMPLATES

from test_cli import CliTestCase


class IntentCliCase(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")

    def balances(self, account=None):
        args = ("balance", "--account", account) if account else ("balance",)
        return {(r["account"]): r["amount"] for r in self.js(*args)["data"]["balances"]}


class Init(CliTestCase):
    def test_template_opens_accounts_and_aliases(self):
        data = self.js("init", "--template", "cn", "--date", "2026-01-01")["data"]
        self.assertEqual(data["accounts_opened"], len(TEMPLATES["cn"]["accounts"]))
        self.assertEqual(len(self.js("accounts")["data"]), data["accounts_opened"])
        self.assertEqual(self.js("alias", "list")["data"]["alipay"], "Assets:支付宝")
        self.assertEqual(self.js("check")["data"]["problems"], [])

    def test_without_a_template_you_are_told_what_to_do(self):
        out, _ = self.run_cli("--ledger", str(self.root), "init")
        self.assertIn("--template", out)
        self.assertEqual(self.js("accounts")["data"], [])

    def test_unknown_template_is_a_usage_error(self):
        self.run_cli("--ledger", str(self.root), "init", "--template", "klingon", expect=2)


class Recording(IntentCliCase):
    def test_spend_income_transfer_directions(self):
        self.js("transfer", "5000", "--from", "期初", "--to", "银行卡", "--date", "2026-01-02")
        self.js("income", "18500", "--to", "银行卡", "--category", "工资", "--date", "2026-10-01")
        spend = self.js("spend", "38", "--from", "支付宝", "--category", "咖啡", "--payee", "瑞幸", "--date", "2026-10-07")["data"]
        self.assertEqual((spend["status"], spend["written"], spend["ccy"]), ("posted", True, "CNY"))
        self.assertEqual([p["account"] for p in spend["postings"]], ["Expenses:餐饮:咖啡", "Assets:支付宝"])
        got = self.balances()
        self.assertEqual(got["Assets:银行卡"], "23500")
        self.assertEqual(got["Income:工资"], "-18500")
        self.assertEqual(got["Equity:期初余额"], "-5000")
        self.assertEqual(got["Expenses:餐饮:咖啡"], "38")
        self.assertEqual(got["Assets:支付宝"], "-38")

    def test_human_output_lists_postings(self):
        out, _ = self.run_cli("--ledger", str(self.root), "spend", "38", "--from", "支付宝", "--category", "咖啡")
        self.assertIn("Recorded spend of 38 CNY", out)
        self.assertIn("Expenses:餐饮:咖啡", out)
        self.assertIn("-38", out)

    def test_ambiguity_is_pending_not_an_error(self):
        data = self.js("spend", "45", "--from", "微信", "--category", "咖", "--actor", "agent:t", "--confidence", "0.95")["data"]
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["unresolved"][0]["candidates"], ["Expenses:餐饮:咖啡"])
        self.assertEqual(data["postings"][0]["account"], None)
        out, _ = self.run_cli("--ledger", str(self.root), "spend", "45", "--from", "微信", "--category", "咖")
        self.assertIn("Needs your confirmation", out)
        self.assertIn("candidates for category: Expenses:餐饮:咖啡", out)
        self.assertIn("money-mom confirm ", out)
        self.assertIn("--category NAME", out)
        self.assertIn("money-mom void ", out)
        self.assertEqual(len(self.js("pending")["data"]), 2)

    def test_strict_exits_1_with_candidates_and_writes_nothing(self):
        before = self.js("check")["data"]["events"]
        data = self.js("spend", "45", "--from", "微信", "--category", "咖", "--strict", expect=1)
        self.assertEqual(data["error"]["code"], "unresolved_account")
        self.assertEqual(data["error"]["details"]["unresolved"][0]["candidates"], ["Expenses:餐饮:咖啡"])
        self.assertEqual(self.js("check")["data"]["events"], before)

    def test_dry_run_writes_nothing(self):
        before = self.js("check")["data"]["events"]
        data = self.js("spend", "99", "--from", "支付宝", "--category", "餐饮", "--dry-run")["data"]
        self.assertFalse(data["written"])
        self.assertIsNone(data["id"])
        self.assertEqual(self.js("check")["data"]["events"], before)
        out, _ = self.run_cli("--ledger", str(self.root), "spend", "99", "--from", "支付宝", "--category", "餐饮", "--dry-run")
        self.assertIn("nothing written", out)

    def test_ccy_and_base_currency(self):
        self.js("spend", "5", "--from", "现金", "--category", "咖啡", "--ccy", "USD")
        self.assertEqual(self.js("balance", "--account", "Assets:现金")["data"]["balances"][0]["ccy"], "USD")

    def test_provenance_flags_reach_the_ledger(self):
        data = self.js("spend", "38", "--from", "支付宝", "--category", "咖啡", "--actor", "agent:t", "--confidence", "0.96",
                       "--source-type", "chat", "--source-ref", "午饭瑞幸 38", "--import-hash", "h1")["data"]
        raw = self.js("show", data["id"])["data"]["events"][0]
        self.assertEqual((raw["actor"]["type"], raw["confidence"], raw["source"]["ref"], raw["import_hash"]),
                         ("agent", 0.96, "午饭瑞幸 38", "h1"))
        self.assertEqual(raw["meta"]["intent"], "spend")
        again = self.js("spend", "38", "--from", "支付宝", "--category", "咖啡", "--import-hash", "h1", expect=1)
        self.assertEqual(again["error"]["code"], "duplicate_import")

    def test_bad_input(self):
        for amount in ("0", "abc"):
            self.assertEqual(self.js("spend", amount, "--from", "支付宝", "--category", "咖啡", expect=1)["error"]["code"], "invalid_amount")
        same = self.js("transfer", "1", "--from", "现金", "--to", "现金", expect=2)
        self.assertEqual(same["error"]["code"], "usage_error")
        self.run_cli("--ledger", str(self.root), "spend", expect=2)  # amount is required
        self.run_cli("--ledger", str(self.root), "spend", "5", "--to", "现金", expect=2)  # no such slot


class ConfirmBySlot(IntentCliCase):
    def test_low_confidence_entry_is_confirmed_as_is(self):
        ev = self.js("spend", "12", "--from", "现金", "--category", "地铁公交", "--actor", "agent:t", "--confidence", "0.5")["data"]
        self.assertEqual(ev["status"], "pending")
        self.js("confirm", ev["id"])
        self.assertEqual(self.js("pending")["data"], [])
        self.assertEqual(self.balances("Expenses")["Expenses:交通:地铁公交"], "12")

    def test_fill_the_missing_category_by_name(self):
        ev = self.js("spend", "45", "--from", "微信", "--category", "吃饭")["data"]
        wrong = self.js("confirm", ev["id"], "--category", "吃饭", expect=1)
        self.assertEqual(wrong["error"]["code"], "unresolved_account")
        self.js("confirm", ev["id"], "--category", "餐饮:聚餐")
        self.assertEqual(self.balances("Expenses")["Expenses:餐饮:聚餐"], "45")
        self.assertEqual(self.js("pending")["data"], [])

    def test_usage_errors(self):
        ev = self.js("spend", "45", "--from", "微信")["data"]
        both = self.js("confirm", ev["id"], "--category", "咖啡", "--posting", "Expenses:其他支出 45 CNY", expect=2)
        self.assertEqual(both["error"]["code"], "usage_error")
        wrong_slot = self.js("confirm", ev["id"], "--to", "银行卡", expect=2)
        self.assertEqual(wrong_slot["error"]["code"], "usage_error")
        plain = self.js("add", "--posting", "Expenses:其他支出 1 CNY", "--posting", "Assets:现金 -1 CNY")["data"][0]
        self.assertEqual(self.js("confirm", plain["id"], "--category", "咖啡", expect=2)["error"]["code"], "usage_error")


class AliasAndResolve(IntentCliCase):
    def test_alias_commands(self):
        self.js("alias", "add", "招行卡", "Assets:银行卡")
        self.assertEqual(self.js("alias", "list")["data"]["招行卡"], "Assets:银行卡")
        self.js("transfer", "100", "--from", "期初", "--to", "招行卡")
        self.assertEqual(self.balances("Assets:银行卡")["Assets:银行卡"], "100")
        self.assertEqual(self.js("alias", "add", "x", "Assets:不存在", expect=1)["error"]["code"], "unknown_account")
        self.js("alias", "remove", "招行卡")
        self.assertEqual(self.js("alias", "remove", "招行卡", expect=1)["error"]["code"], "unknown_alias")
        out, _ = self.run_cli("--ledger", str(self.root), "alias", "list")
        self.assertIn("alipay", out)

    def test_resolve_command(self):
        ok = self.js("resolve", "咖啡")["data"]
        self.assertEqual((ok["account"], ok["matched_by"]), ("Expenses:餐饮:咖啡", "segment"))
        self.assertEqual(self.js("resolve", "cash", "--type", "asset")["data"]["account"], "Assets:现金")
        wrong = self.js("resolve", "cash", "--type", "expense")["data"]
        self.assertEqual((wrong["account"], wrong["reason"]), (None, "wrong_type"))
        near = self.js("resolve", "咖")["data"]
        self.assertEqual((near["reason"], near["candidates"]), ("approximate", ["Expenses:餐饮:咖啡"]))
        out, _ = self.run_cli("--ledger", str(self.root), "resolve", "咖")
        self.assertIn("candidates:", out)
        out, _ = self.run_cli("--ledger", str(self.root), "resolve", "咖啡")
        self.assertIn("->", out)


class AgentFlow(IntentCliCase):
    def test_a_whole_agent_conversation(self):
        """What a skill will do: dry-run if unsure, record, list what is pending, confirm, query."""
        agent = ("--actor", "agent:claude-code")
        sure = self.js("spend", "38", "--from", "支付宝", "--category", "咖啡", "--payee", "瑞幸", "--confidence", "0.96", *agent)["data"]
        unsure = self.js("spend", "200", "--from", "支付宝", "--payee", "小王", "--confidence", "0.55", *agent)["data"]
        self.assertEqual((sure["status"], unsure["status"]), ("posted", "pending"))
        pending = self.js("pending")["data"]
        self.assertEqual([p["id"] for p in pending], [unsure["id"]])
        self.assertEqual(pending[0]["payee"], "小王")
        self.js("alias", "add", "借出", "Assets:应收")
        self.js("confirm", unsure["id"], "--posting", "Assets:应收 200 CNY", "--posting", "Assets:支付宝 -200 CNY")
        rows = self.js("query", "SELECT account, amount_text FROM v_postings WHERE payee = '小王' ORDER BY idx")["data"]["rows"]
        self.assertEqual(rows, [["Assets:应收", "200"], ["Assets:支付宝", "-200"]])
        self.assertEqual(self.js("check")["data"]["ok"], True)


if __name__ == "__main__":
    unittest.main()
