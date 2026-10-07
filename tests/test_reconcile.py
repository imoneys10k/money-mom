import datetime as dt
import unittest
from decimal import Decimal

from money_mom import LedgerError
from money_mom.importing import StatementRow, load_mapping, read_statement, run_import, save_mapping
from money_mom.reconcile import reconcile, rows_from_json, seal
from money_mom.templates import apply_template

from helpers import HUMAN, LedgerTestCase
from test_cli import CliTestCase

D = Decimal

CMB_TOML = """
[columns]
date = "记账日期"
amount = "金额"
payee = "对方"
description = "交易摘要"
balance = "余额"

[format]
date = "%Y-%m-%d"
sign = "inflow_positive"

[[rules]]
match = "Netflix"
account = "订阅"
[[rules]]
match = "Spotify"
account = "订阅"
[[rules]]
match = "房东"
account = "房租"
[[rules]]
match = "瑞幸"
account = "咖啡"
[[rules]]
match = "滴滴"
account = "打车"
"""

STATEMENT = """记账日期,交易摘要,对方,金额,余额
2026-09-02,转账支出,房东,-4500.00,7500.00
2026-09-03,消费,Netflix,-68.00,7432.00
2026-09-05,消费,瑞幸咖啡,-38.00,7394.00
2026-09-12,消费,滴滴出行,-43.50,7350.50
2026-09-20,消费,Spotify,-18.00,7332.50
"""


def err(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except LedgerError as e:
        return e
    raise AssertionError("expected a LedgerError")


def row(day, amount, payee="", description="", line=1, id="", balance=None):
    return StatementRow(line, dt.date.fromisoformat(day), D(amount), payee, description, id, balance, {})


class ReconcileCase(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)
        self.ledger.add_txn("2026-01-02", [("Assets:银行卡", "12000", "CNY"), ("Equity:期初余额", "-12000", "CNY")])
        self.dir = self.root.parent / "files"
        self.dir.mkdir()

    def spend(self, amount, date, payee, category="Expenses:其他支出", **kw):
        return self.ledger.add_txn(date, [(category, amount, "CNY"), ("Assets:银行卡", f"-{amount}", "CNY")], payee=payee, **kw)

    def rec(self, rows, **kw):
        return reconcile(self.ledger, "银行卡", rows, **kw)

    def statement_file(self, text=STATEMENT):
        path = self.dir / "cmb.csv"
        path.write_text(text, encoding="utf-8")
        save_mapping(self.ledger, "cmb", CMB_TOML)
        return path, load_mapping(self.ledger, "cmb")


class Matching(ReconcileCase):
    def test_everything_matches_when_the_ledger_was_imported_from_the_same_statement(self):
        path, mapping = self.statement_file()
        run_import(self.ledger, path, mapping, account="银行卡")
        report = self.rec(read_statement(path, mapping).rows)
        self.assertEqual((report["matched"], report["matched_by_import_hash"], report["clean"]), (5, 5, True))
        self.assertEqual((report["missing_in_ledger"], report["missing_in_statement"], report["amount_mismatches"]), ([], [], []))

    def test_hand_written_entries_match_by_amount_and_date(self):
        self.spend("68", "2026-09-03", "Netflix")
        report = self.rec([row("2026-09-03", "-68", "Netflix")])
        self.assertEqual((report["matched"], report["matched_by_import_hash"], report["clean"]), (1, 0, True))

    def test_the_bank_may_date_it_a_few_days_later(self):
        self.spend("68", "2026-09-03", "Netflix")
        self.assertTrue(self.rec([row("2026-09-06", "-68", "Netflix")])["clean"])
        # 4 days apart is too far; the range must cover the ledger entry for it to be reported as unknown to the statement
        far = self.rec([row("2026-09-07", "-68", "Netflix")], since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30))
        self.assertEqual((far["matched"], len(far["missing_in_ledger"]), len(far["missing_in_statement"])), (0, 1, 1))
        narrow = self.rec([row("2026-09-07", "-68", "Netflix")])  # the statement covers only the 7th
        self.assertEqual((narrow["matched"], len(narrow["missing_in_ledger"]), narrow["missing_in_statement"]), (0, 1, []))
        self.assertTrue(self.rec([row("2026-09-07", "-68", "Netflix")], tolerance_days=5)["clean"])

    def test_a_charge_the_statement_shows_twice_is_flagged_as_a_possible_double_charge(self):
        self.spend("68", "2026-09-03", "Netflix")
        report = self.rec([row("2026-09-03", "-68", "Netflix", line=2), row("2026-09-03", "-68", "Netflix", line=3)])
        self.assertEqual(report["matched"], 1)
        (missing,) = report["missing_in_ledger"]
        self.assertTrue(missing["possible_double_charge"])
        self.assertEqual(missing["amount"], "-68")

    def test_a_missing_row_is_not_a_double_charge_unless_it_repeats_one_that_matched(self):
        self.spend("68", "2026-09-03", "Netflix")
        report = self.rec([row("2026-09-03", "-68", "Netflix"), row("2026-09-12", "-43.50", "滴滴出行")])
        (missing,) = report["missing_in_ledger"]
        self.assertFalse(missing["possible_double_charge"])

    def test_matching_is_one_to_one(self):
        self.spend("10", "2026-09-03", "A")
        self.spend("10", "2026-09-03", "B")
        report = self.rec([row("2026-09-03", "-10", "A", line=1)])
        self.assertEqual((report["matched"], len(report["missing_in_statement"])), (1, 1))
        # the entry whose text is closer to the statement row is the one that gets matched
        self.assertEqual(report["missing_in_statement"][0]["text"], "B")

    def test_ledger_entries_the_statement_does_not_know_are_listed(self):
        self.spend("68", "2026-09-03", "Netflix")
        self.spend("99", "2026-09-10", "某商家")
        report = self.rec([row("2026-09-03", "-68", "Netflix"), row("2026-09-20", "-1", "x")])
        self.assertEqual([m["text"] for m in report["missing_in_statement"]], ["某商家"])

    def test_the_same_item_with_a_different_amount_is_called_out(self):
        self.spend("83", "2026-09-05", "瑞幸咖啡")
        report = self.rec([row("2026-09-05", "-38", "瑞幸咖啡")])
        (m,) = report["amount_mismatches"]
        self.assertEqual((m["statement"]["amount"], m["ledger"]["amount"], m["difference"]), ("-38", "-83", "45"))
        self.assertEqual((report["missing_in_ledger"], report["missing_in_statement"]), ([], []))
        self.assertFalse(report["clean"])

    def test_unrelated_items_with_different_amounts_are_not_paired(self):
        self.spend("83", "2026-09-05", "房东")
        report = self.rec([row("2026-09-05", "-38", "瑞幸咖啡")])
        self.assertEqual(report["amount_mismatches"], [])
        self.assertEqual((len(report["missing_in_ledger"]), len(report["missing_in_statement"])), (1, 1))

    def test_money_in_and_money_out_are_never_mistaken_for_each_other(self):
        self.ledger.add_txn("2026-09-05", [("Assets:银行卡", "50", "CNY"), ("Income:其他收入", "-50", "CNY")], payee="小王")
        report = self.rec([row("2026-09-05", "-50", "小王")])
        self.assertEqual(report["amount_mismatches"], [])
        self.assertEqual(report["matched"], 0)

    def test_pending_entries_are_shown_but_do_not_count(self):
        self.ledger.add_txn("2026-09-05", [(None, "30", "CNY"), ("Assets:银行卡", "-30", "CNY")], status="pending", payee="某人")
        report = self.rec([row("2026-09-05", "-30", "某人")])
        self.assertEqual(report["matched"], 0)
        self.assertEqual(len(report["missing_in_ledger"]), 1)
        self.assertEqual([(p["date"], p["amount"]) for p in report["pending_in_range"]], [("2026-09-05", "-30")])

    def test_voided_entries_are_ignored(self):
        ev = self.spend("68", "2026-09-03", "Netflix")
        self.ledger.void(ev.id, "wrong")
        report = self.rec([row("2026-09-03", "-68", "Netflix")])
        self.assertEqual((report["matched"], len(report["missing_in_ledger"])), (0, 1))

    def test_range_and_currency(self):
        self.spend("68", "2026-09-03", "Netflix")
        report = self.rec([row("2026-09-03", "-68", "Netflix"), row("2026-10-03", "-68", "Netflix")], until=dt.date(2026, 9, 30))
        self.assertEqual((report["statement_rows"], report["to"], report["clean"]), (1, "2026-09-30", True))
        self.ledger.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])
        self.ledger.add_txn("2026-09-03", [("Expenses:订阅", "5", "USD"), ("Assets:美元户", "-5", "USD")], payee="Netflix")
        usd = reconcile(self.ledger, "美元户", [row("2026-09-03", "-5", "Netflix")])
        self.assertEqual((usd["ccy"], usd["clean"]), ("USD", True))

    def test_an_empty_range_and_a_bad_account(self):
        self.assertEqual(err(self.rec, [row("2026-09-03", "-1")], since=dt.date(2026, 10, 1)).code, "invalid_statement")
        self.assertEqual(err(reconcile, self.ledger, "咖啡", [row("2026-09-03", "-1")]).code, "unresolved_account")


class ClosingBalance(ReconcileCase):
    def setUp(self) -> None:
        super().setUp()
        self.spend("68", "2026-09-03", "Netflix")
        self.spend("83", "2026-09-05", "瑞幸咖啡")

    def test_the_balance_check_is_independent_of_the_row_check(self):
        # the ledger stands at 11849 on the 5th (12000 - 68 - 83); the statement lists only the first charge
        agrees = self.rec([row("2026-09-03", "-68", "Netflix")], closing_balance=D("11849"), closing_date=dt.date(2026, 9, 5))
        self.assertEqual((agrees["balance"]["ok"], agrees["balance"]["difference"], agrees["balance"]["ledger"]), (True, "0", "11849"))
        self.assertEqual((agrees["clean"], agrees["can_assert"]), (False, False))  # a ledger entry is not on the statement
        off = self.rec([row("2026-09-03", "-68", "Netflix")], closing_balance=D("11932"), closing_date=dt.date(2026, 9, 5))
        self.assertEqual((off["balance"]["ok"], off["balance"]["difference"]), (False, "83"))
        both = reconcile(self.ledger, "银行卡", [row("2026-09-03", "-68", "Netflix"), row("2026-09-05", "-83", "瑞幸咖啡")],
                         closing_balance=D("11849"), closing_date=dt.date(2026, 9, 5))
        self.assertEqual((both["balance"]["ok"], both["clean"], both["can_assert"]), (True, True, True))

    def test_a_difference_is_explained_when_the_listed_items_account_for_it(self):
        rows = [row("2026-09-03", "-68", "Netflix"), row("2026-09-05", "-38", "瑞幸咖啡"), row("2026-09-12", "-43.50", "滴滴出行")]
        report = self.rec(rows, closing_balance=D("11850.50"), closing_date=dt.date(2026, 9, 12))
        b = report["balance"]
        # ledger 11849 vs statement 11850.50: +45 (the typo) and -43.50 (the missing taxi) = +1.50
        self.assertEqual((b["ledger"], b["difference"], b["ok"], b["explained_by_the_differences_listed"]), ("11849", "1.50", False, True))

    def test_a_difference_nothing_explains_is_not_called_explained(self):
        rows = [row("2026-09-03", "-68", "Netflix"), row("2026-09-05", "-83", "瑞幸咖啡")]
        report = self.rec(rows, closing_balance=D("11000"), closing_date=dt.date(2026, 9, 5))
        self.assertEqual((report["balance"]["ok"], report["balance"]["explained_by_the_differences_listed"]), (False, False))

    def test_rows_that_all_match_are_not_clean_when_the_closing_balance_is_off(self):
        # every row matches, yet the bank says 11000 and the ledger says 11849: something before the period is wrong
        rows = [row("2026-09-03", "-68", "Netflix"), row("2026-09-05", "-83", "瑞幸咖啡")]
        report = self.rec(rows, closing_balance=D("11000"), closing_date=dt.date(2026, 9, 5))
        self.assertEqual((report["missing_in_ledger"], report["missing_in_statement"], report["amount_mismatches"]), ([], [], []))
        self.assertEqual((report["balance"]["ok"], report["clean"], report["can_assert"]), (False, False, False))

    def test_both_halves_of_the_closing_pair_are_needed(self):
        self.assertEqual(err(self.rec, [row("2026-09-03", "-68")], closing_balance=D(1)).code, "usage_error")

    def test_without_a_closing_balance_nothing_can_be_sealed(self):
        report = self.rec([row("2026-09-03", "-68", "Netflix"), row("2026-09-05", "-83", "瑞幸咖啡")])
        self.assertEqual((report["clean"], report["can_assert"], report["balance"]), (True, False, None))


class Sealing(ReconcileCase):
    def clean_report(self):
        self.spend("68", "2026-09-03", "Netflix")
        return self.rec([row("2026-09-03", "-68", "Netflix")], closing_balance=D("11932"), closing_date=dt.date(2026, 9, 30))

    def test_a_clean_reconciliation_can_be_sealed_and_locks_the_period(self):
        report = self.clean_report()
        self.assertTrue(report["can_assert"])
        sealed = seal(self.ledger, report)
        self.assertEqual((sealed["asserted"], sealed["date"], sealed["balance"]), (True, "2026-09-30", "11932"))
        late = err(self.spend, "5", "2026-09-10", "迟到的")
        self.assertEqual(late.code, "period_locked")

    def test_an_unclean_one_is_refused_with_the_reasons_and_writes_nothing(self):
        self.spend("83", "2026-09-05", "瑞幸咖啡")
        report = self.rec([row("2026-09-05", "-38", "瑞幸咖啡"), row("2026-09-12", "-43.50", "滴滴出行")],
                          closing_balance=D("11918.50"), closing_date=dt.date(2026, 9, 30))
        before = self.total_lines()
        e = err(seal, self.ledger, report)
        self.assertEqual(e.code, "reconcile_not_clean")
        text = " ".join(e.details["reasons"])
        for expected in ("not in the ledger", "amount(s) disagree", "closing balance differs"):
            self.assertIn(expected, text)
        self.assertEqual(self.total_lines(), before)

    def test_it_needs_a_closing_balance(self):
        self.spend("68", "2026-09-03", "Netflix")
        report = self.rec([row("2026-09-03", "-68", "Netflix")])
        self.assertIn("no closing balance", " ".join(err(seal, self.ledger, report).details["reasons"]))

    def test_ledger_entries_the_statement_lacks_also_block_sealing(self):
        self.spend("68", "2026-09-03", "Netflix")
        self.spend("99", "2026-09-10", "某商家")
        report = self.rec([row("2026-09-03", "-68", "Netflix"), row("2026-09-20", "-1", "x")],
                          closing_balance=D("11800"), closing_date=dt.date(2026, 9, 30))
        self.assertIn("not on the statement", " ".join(err(seal, self.ledger, report).details["reasons"]))


class JsonRows(unittest.TestCase):
    def test_rows_an_agent_read_from_a_pdf(self):
        rows = rows_from_json([{"date": "2026-09-03", "amount": "-68.00", "payee": "Netflix", "id": "r14"},
                               {"date": "2026-09-05", "amount": "1,200.50", "description": "工资", "balance": "5000"}])
        self.assertEqual([(r.date, r.amount, r.payee, r.id) for r in rows],
                         [(dt.date(2026, 9, 3), D("-68.00"), "Netflix", "r14"), (dt.date(2026, 9, 5), D("1200.50"), "", "")])
        self.assertEqual(rows[1].balance, D(5000))
        self.assertEqual([r.line for r in rows], [1, 2])

    def test_bad_rows(self):
        bad = [
            [], "text", [1], [{"amount": "1"}], [{"date": "2026-09-03"}], [{"date": "2026-09-03", "amount": 1.5}],
            [{"date": "2026-09-03", "amount": "0"}], [{"date": "09/03/2026", "amount": "1"}],
            [{"date": "2026-09-03", "amount": "abc"}], [{"date": "2026-09-03", "amount": "1", "colour": "red"}],
        ]
        for items in bad:
            self.assertEqual(err(rows_from_json, items).code, "invalid_statement", repr(items))

    def test_integer_amounts_are_fine(self):
        self.assertEqual(rows_from_json([{"date": "2026-09-03", "amount": -68}])[0].amount, D(-68))


class Cli(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.js("transfer", "12000", "--from", "期初", "--to", "银行卡", "--date", "2026-01-02")
        self.files = self.dir / "files"
        self.files.mkdir()
        self.csv = self.files / "cmb.csv"
        self.csv.write_text(STATEMENT, encoding="utf-8")
        toml = self.files / "cmb.toml"
        toml.write_text(CMB_TOML, encoding="utf-8")
        self.js("import", "save-map", "cmb", str(toml))

    def reconcile(self, *extra, expect=0):
        return self.js("reconcile", "银行卡", str(self.csv), "--map", "cmb", *extra, expect=expect)

    def test_import_then_reconcile_is_clean_and_can_be_sealed(self):
        self.js("import", "run", str(self.csv), "--map", "cmb", "--account", "银行卡")
        data = self.reconcile("--closing-from-statement")["data"]
        self.assertEqual((data["clean"], data["matched_by_import_hash"], data["can_assert"]), (True, 5, True))
        sealed = self.reconcile("--closing-from-statement", "--assert")["data"]["sealed"]
        self.assertEqual((sealed["date"], sealed["balance"]), ("2026-09-20", "7332.50"))
        late = self.js("spend", "5", "--from", "银行卡", "--category", "咖啡", "--date", "2026-09-10", expect=1)
        self.assertEqual(late["error"]["code"], "period_locked")

    def test_a_ledger_with_mistakes_gets_a_useful_report(self):
        self.js("spend", "68", "--from", "银行卡", "--category", "订阅", "--payee", "Netflix", "--date", "2026-09-03")
        self.js("spend", "83", "--from", "银行卡", "--category", "咖啡", "--payee", "瑞幸咖啡", "--date", "2026-09-05")
        data = self.reconcile("--closing-balance", "7332.50", "--closing-date", "2026-09-20")["data"]
        self.assertFalse(data["clean"])
        self.assertEqual(len(data["amount_mismatches"]), 1)
        self.assertEqual(len(data["missing_in_ledger"]), 3)
        out, _ = self.run_cli("--ledger", str(self.root), "reconcile", "银行卡", str(self.csv), "--map", "cmb",
                              "--closing-balance", "7332.50", "--closing-date", "2026-09-20")
        self.assertIn("on the statement but NOT in the ledger (3)", out)
        self.assertIn("same item, different amount (1)", out)
        self.assertIn("Not clean yet.", out)
        self.assertIn("money-mom import run", out)

    def test_assert_is_refused_for_an_unclean_reconciliation(self):
        data = self.reconcile("--closing-from-statement", "--assert", expect=1)
        self.assertEqual(data["error"]["code"], "reconcile_not_clean")
        self.assertEqual(self.js("check")["data"]["events"], 47 + 1)

    def test_missing_rows_can_be_imported_and_the_result_is_clean(self):
        self.js("spend", "68", "--from", "银行卡", "--category", "订阅", "--payee", "Netflix", "--date", "2026-09-03")
        self.assertFalse(self.reconcile()["data"]["clean"])
        self.js("import", "run", str(self.csv), "--map", "cmb", "--account", "银行卡")
        # the hand-written Netflix entry and the imported one are now both in the ledger: the extra one shows up
        again = self.reconcile("--closing-from-statement")["data"]
        self.assertEqual(len(again["missing_in_statement"]), 1)

    def test_rows_from_a_pdf_via_json(self):
        rows = '[{"date": "2026-09-03", "amount": "-68", "payee": "Netflix"}]'
        self.js("spend", "68", "--from", "银行卡", "--category", "订阅", "--payee", "Netflix", "--date", "2026-09-03")
        data = self.js("reconcile", "银行卡", "--from-json", "-", "--closing-balance", "11932", "--closing-date", "2026-09-30", stdin=rows)["data"]
        self.assertEqual((data["clean"], data["matched"]), (True, 1))

    def test_usage_errors(self):
        for args in (
            ("reconcile", "银行卡"),
            ("reconcile", "银行卡", str(self.csv)),
            ("reconcile", "银行卡", str(self.csv), "--map", "cmb", "--from-json", "-"),
            ("reconcile", "银行卡", str(self.csv), "--map", "cmb", "--closing-balance", "100"),
        ):
            self.assertEqual(self.js(*args, expect=2, stdin="[]")["error"]["code"], "usage_error", args)
        no_balance = self.files / "nobal.toml"
        no_balance.write_text(CMB_TOML.replace('balance = "余额"\n', ""), encoding="utf-8")
        self.js("import", "save-map", "nobal", str(no_balance))
        data = self.js("reconcile", "银行卡", str(self.csv), "--map", "nobal", "--closing-from-statement", expect=2)
        self.assertIn("no balance column", data["error"]["message"])


if __name__ == "__main__":
    unittest.main()
