import datetime as dt
import unittest
from decimal import Decimal

from money_mom.report import monthly_report, parse_month

from helpers import HUMAN, LedgerTestCase
from test_cli import CliTestCase

D = Decimal
TODAY = dt.date(2026, 10, 20)


class Months(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_month("2026-02"), (dt.date(2026, 2, 1), dt.date(2026, 2, 28)))
        self.assertEqual(parse_month("2028-02")[1], dt.date(2028, 2, 29))
        self.assertEqual(parse_month("2026-12")[1], dt.date(2026, 12, 31))

    def test_rejects_anything_else(self):
        for text in ("2026-13", "2026-00", "2026-9", "26-09", "2026/09", "september", "", "2026-09-01"):
            with self.assertRaises(Exception, msg=text) as ctx:
                parse_month(text)
            self.assertEqual(getattr(ctx.exception, "code", None), "usage_error", text)


class ReportCase(LedgerTestCase):
    def setUp(self):
        super().setUp()
        led = self.ledger
        for account in (
            "Assets:CMB", "Assets:Cash", "Liabilities:Card", "Expenses:Dining", "Expenses:Rent", "Expenses:Transport",
            "Income:Salary", "Equity:Conversions",
        ):
            led.open_account(account, "2026-07-01")
        led.open_account("Assets:USD", "2026-07-01", currencies=["USD"])

    def salary(self, date, amount="10000", ccy="CNY", account="Assets:CMB"):
        return self.ledger.add_txn(date, [(account, amount, ccy), ("Income:Salary", f"-{amount}", ccy)], payee="公司")

    def spend(self, date, account, amount, ccy="CNY", payee=None):
        return self.ledger.add_txn(date, [(account, amount, ccy), ("Assets:CMB", f"-{amount}", ccy)], payee=payee)

    def price(self, base, quote, rate, date):
        self.ledger.append(self.ledger.new_event(
            "price", actor=HUMAN, date=date, base=base, quote=quote, rate=rate, source={"type": "manual", "ref": "test"}
        ), clamp_ts=True)

    def report(self, month="2026-09", **kw):
        kw.setdefault("today", TODAY)
        return monthly_report(self.ledger, month, **kw)

    def cats(self, report):
        return {c["category"]: c for c in report["categories"]}


class Totals(ReportCase):
    def test_income_spending_net_and_rate(self):
        self.salary("2026-09-01")
        self.spend("2026-09-10", "Expenses:Dining", "520.00")
        self.spend("2026-09-15", "Expenses:Rent", "3000")
        r = self.report()
        self.assertEqual((r["income"]["total"], r["expenses"]["total"], r["net"]), ("10000.00", "3520.00", "6480.00"))
        self.assertEqual(r["savings_rate_percent"], "64.8")
        self.assertEqual(r["entries"], 3)
        self.assertFalse(r["partial"])
        self.assertEqual(r["income"]["by_currency"], {"CNY": "10000.00"})

    def test_other_months_are_left_out(self):
        self.spend("2026-08-31", "Expenses:Dining", "1")
        self.spend("2026-09-30", "Expenses:Dining", "2")
        self.spend("2026-10-01", "Expenses:Dining", "4")
        self.assertEqual(self.report()["expenses"]["total"], "2.00")

    def test_a_refund_reduces_the_category(self):
        self.spend("2026-09-10", "Expenses:Dining", "100")
        self.ledger.add_txn("2026-09-12", [("Expenses:Dining", "-30", "CNY"), ("Assets:CMB", "30", "CNY")])
        r = self.report()
        self.assertEqual(r["expenses"]["total"], "70.00")
        self.assertEqual(self.cats(r)["Dining"]["converted"], "70.00")

    def test_transfers_and_exchanges_are_not_spending_or_income(self):
        self.salary("2026-09-01", "1000")
        self.ledger.add_txn("2026-09-02", [("Assets:Cash", "200", "CNY"), ("Assets:CMB", "-200", "CNY")])
        self.ledger.add_txn("2026-09-03", [("Liabilities:Card", "-50", "CNY"), ("Assets:CMB", "50", "CNY")])
        self.ledger.add_txn("2026-09-04", [
            ("Assets:CMB", "-720", "CNY"), ("Equity:Conversions", "720", "CNY"),
            ("Equity:Conversions", "-100", "USD"), ("Assets:USD", "100", "USD"),
        ])
        r = self.report()
        self.assertEqual((r["income"]["total"], r["expenses"]["total"], r["entries"]), ("1000.00", "0.00", 1))
        self.assertEqual(r["categories"], [])

    def test_pending_entries_are_not_counted_but_are_mentioned(self):
        self.salary("2026-09-01")
        self.ledger.add_txn("2026-09-05", [("Expenses:Dining", "88", "CNY"), (None, "-88", "CNY")], status="pending", payee="某店")
        r = self.report()
        self.assertEqual(r["expenses"]["total"], "0.00")
        self.assertEqual(r["checks"]["pending_in_month"], 1)
        self.assertTrue(any("pending" in n for n in r["notes"]))

    def test_voided_entries_vanish(self):
        ev = self.spend("2026-09-05", "Expenses:Dining", "88")
        self.ledger.void(ev.id, "entered by mistake")
        self.assertEqual(self.report()["expenses"]["total"], "0.00")

    def test_a_zero_savings_rate_needs_income(self):
        self.spend("2026-09-10", "Expenses:Dining", "50")
        r = self.report()
        self.assertIsNone(r["savings_rate_percent"])
        self.assertEqual(r["net"], "-50.00")

    def test_an_empty_month_says_so_and_invents_nothing(self):
        r = self.report()
        self.assertEqual((r["entries"], r["categories"], r["top_spending"]), (0, [], []))
        self.assertIsNone(r["savings_rate_percent"])
        self.assertTrue(any("No income or spending" in n for n in r["notes"]))


class Categories(ReportCase):
    def test_sorted_by_amount_with_shares_summing_to_about_100(self):
        self.spend("2026-09-10", "Expenses:Dining", "100")
        self.spend("2026-09-11", "Expenses:Rent", "300")
        self.spend("2026-09-12", "Expenses:Transport", "100")
        r = self.report()
        self.assertEqual([c["category"] for c in r["categories"]], ["Rent", "Dining", "Transport"])
        self.assertEqual([c["share_percent"] for c in r["categories"]], ["60.0", "20.0", "20.0"])

    def test_subcategories_roll_up_to_the_second_segment(self):
        self.ledger.open_account("Expenses:Dining:Coffee", "2026-07-01")
        self.spend("2026-09-10", "Expenses:Dining", "10")
        self.spend("2026-09-11", "Expenses:Dining:Coffee", "5")
        self.assertEqual(self.cats(self.report())["Dining"]["converted"], "15.00")

    def test_change_against_last_month_and_dropped_categories(self):
        self.spend("2026-08-10", "Expenses:Dining", "300")
        self.spend("2026-08-12", "Expenses:Transport", "80")
        self.spend("2026-09-10", "Expenses:Dining", "565.5")
        self.spend("2026-09-15", "Expenses:Rent", "3000")
        r = self.report()
        cats = self.cats(r)
        self.assertEqual((cats["Dining"]["previous"], cats["Dining"]["change"]), ("300.00", "265.50"))
        self.assertEqual((cats["Rent"]["previous"], cats["Rent"]["change"]), ("0.00", "3000.00"))
        self.assertEqual(r["previous"]["dropped_categories"], ["Transport"])
        self.assertEqual(r["previous"]["expenses_change"], "3185.50")
        self.assertEqual(r["previous"]["expenses_change_percent"], "838.3")

    def test_without_a_previous_month_changes_are_null_not_zero(self):
        self.spend("2026-09-10", "Expenses:Dining", "50")
        r = self.report()
        self.assertFalse(r["previous"]["has_data"])
        self.assertIsNone(self.cats(r)["Dining"]["change"])
        self.assertIsNone(self.cats(r)["Dining"]["previous"])
        self.assertNotIn("expenses_change", r["previous"])
        self.assertTrue(any("nothing to compare" in n for n in r["notes"]))

    def test_percent_change_is_null_when_last_month_had_no_spending(self):
        self.salary("2026-08-01")
        self.spend("2026-09-10", "Expenses:Dining", "50")
        r = self.report()
        self.assertTrue(r["previous"]["has_data"])
        self.assertEqual(r["previous"]["expenses_change"], "50.00")
        self.assertIsNone(r["previous"]["expenses_change_percent"])


class Biggest(ReportCase):
    def test_ranked_and_limited(self):
        for day, amount, payee in (("01", "10", "a"), ("02", "300", "b"), ("03", "50", "c"), ("04", "300", "d")):
            self.spend(f"2026-09-{day}", "Expenses:Dining", amount, payee=payee)
        top = self.report(top=3)["top_spending"]
        self.assertEqual([t["payee"] for t in top], ["b", "d", "c"])  # ties keep date order
        self.assertEqual(self.report(top=0)["top_spending"], [])

    def test_refunds_are_not_listed_as_big_spending(self):
        self.ledger.add_txn("2026-09-12", [("Expenses:Dining", "-30", "CNY"), ("Assets:CMB", "30", "CNY")])
        self.assertEqual(self.report()["top_spending"], [])


class Currencies(ReportCase):
    def test_converted_at_the_rate_of_the_last_day(self):
        self.price("USD", "CNY", "7.00", "2026-09-30")
        self.price("USD", "CNY", "9.00", "2026-10-02")
        self.spend("2026-09-10", "Expenses:Dining", "100", "CNY")
        self.ledger.add_txn("2026-09-11", [("Expenses:Dining", "10", "USD"), ("Assets:USD", "-10", "USD")])
        r = self.report()
        self.assertEqual(r["expenses"]["by_currency"], {"CNY": "100.00", "USD": "10.00"})
        self.assertEqual(r["expenses"]["total"], "170.00")
        self.assertFalse(r["partial"])

    def test_a_missing_rate_marks_the_figures_partial_and_never_counts_zero(self):
        self.spend("2026-09-10", "Expenses:Dining", "100", "CNY")
        self.ledger.open_account("Assets:EUR", "2026-07-01", currencies=["EUR"])
        self.ledger.add_txn("2026-09-11", [("Expenses:Dining", "10", "EUR"), ("Assets:EUR", "-10", "EUR")])
        r = self.report()
        self.assertTrue(r["partial"])
        self.assertEqual(r["missing"], ["EUR"])
        self.assertEqual(r["expenses"]["total"], "100.00")
        self.assertEqual(r["expenses"]["by_currency"]["EUR"], "10.00")
        self.assertEqual(self.cats(r)["Dining"]["partial"], True)
        self.assertIsNone(r["savings_rate_percent"])
        self.assertTrue(any("EUR" in n for n in r["notes"]))
        self.assertIsNone(r["previous"].get("expenses_change"))

    def test_shares_are_withheld_when_the_total_is_incomplete(self):
        self.spend("2026-09-10", "Expenses:Dining", "100", "CNY")
        self.ledger.open_account("Assets:EUR", "2026-07-01", currencies=["EUR"])
        self.ledger.add_txn("2026-09-11", [("Expenses:Rent", "10", "EUR"), ("Assets:EUR", "-10", "EUR")])
        self.assertIsNone(self.cats(self.report())["Dining"]["share_percent"])

    def test_a_category_in_only_a_missing_currency_has_no_converted_figure(self):
        self.ledger.open_account("Assets:EUR", "2026-07-01", currencies=["EUR"])
        self.ledger.add_txn("2026-09-11", [("Expenses:Rent", "10", "EUR"), ("Assets:EUR", "-10", "EUR")])
        row = self.cats(self.report())["Rent"]
        self.assertIsNone(row["converted"])
        self.assertEqual(row["by_currency"], {"EUR": "10.00"})

    def test_in_another_currency(self):
        self.price("USD", "CNY", "8", "2026-09-30")
        self.spend("2026-09-10", "Expenses:Dining", "80", "CNY")
        r = self.report(target="USD")
        self.assertEqual((r["in"], r["expenses"]["total"]), ("USD", "10.00"))

    def test_stale_rates_are_flagged(self):
        self.price("USD", "CNY", "7", "2026-08-01")
        self.ledger.add_txn("2026-09-11", [("Expenses:Dining", "10", "USD"), ("Assets:USD", "-10", "USD")])
        self.assertEqual(self.report()["checks"]["stale_rates"], ["USD"])


class NetWorth(ReportCase):
    def test_start_end_and_change(self):
        self.salary("2026-08-15", "1000")
        self.salary("2026-09-02", "500")
        self.ledger.add_txn("2026-09-03", [("Liabilities:Card", "-200", "CNY"), ("Expenses:Dining", "200", "CNY")])
        w = self.report()["net_worth"]
        self.assertEqual((w["start"]["total"], w["end"]["total"], w["change"]), ("1000.00", "1300.00", "300.00"))
        self.assertEqual(w["start"]["as_of"], "2026-08-31")

    def test_unconvertible_balances_give_no_change(self):
        self.ledger.open_account("Assets:EUR", "2026-07-01", currencies=["EUR"])
        self.ledger.add_txn("2026-09-01", [("Assets:EUR", "10", "EUR"), ("Income:Salary", "-10", "EUR")])
        w = self.report()["net_worth"]
        self.assertTrue(w["end"]["partial"])
        self.assertIsNone(w["change"])


class InProgress(ReportCase):
    def test_current_month_is_month_to_date(self):
        self.spend("2026-10-05", "Expenses:Dining", "10")
        self.spend("2026-10-25", "Expenses:Dining", "99")  # in the future: not today's business, but it is posted
        r = monthly_report(self.ledger, None, today=TODAY)
        self.assertEqual((r["month"], r["in_progress"], r["as_of"]), ("2026-10", True, "2026-10-20"))
        self.assertTrue(any("month to date" in n for n in r["notes"]))

    def test_a_finished_month_is_not_in_progress(self):
        r = self.report("2026-09")
        self.assertFalse(r["in_progress"])
        self.assertEqual(r["as_of"], "2026-09-30")

    def test_a_month_that_has_not_started_is_refused(self):
        with self.assertRaises(Exception) as ctx:
            self.report("2026-11")
        self.assertEqual(ctx.exception.code, "usage_error")


class Assertions(ReportCase):
    def test_accounts_whose_balance_was_checked_in_the_month(self):
        self.salary("2026-09-01", "1000")
        self.ledger.assert_balance("Assets:CMB", "2026-09-30", "1000.00", "CNY")
        self.assertEqual(self.report()["checks"]["accounts_with_assertion"], ["Assets:CMB"])
        self.assertEqual(self.report("2026-08")["checks"]["accounts_with_assertion"], [])


class Cli(CliTestCase):
    def test_report_command(self):
        self.setup_ledger()
        self.js("add", "--date", "2026-10-03", "--posting", "Expenses:Gift 88.00 CNY", "--posting", "Assets:CMB -88.00 CNY")
        data = self.js("report", "--month", "2026-10")["data"]
        self.assertEqual((data["income"]["total"], data["expenses"]["total"]), ("1000.00", "88.00"))
        out, _ = self.run_cli("--ledger", str(self.root), "report", "--month", "2026-10")
        self.assertIn("Where it went", out)
        self.assertIn("Gift", out)
        self.assertIn("88.00", out)

    def test_bad_input_is_a_usage_error(self):
        self.setup_ledger()
        for args in (("--month", "oct"), ("--top", "-1")):
            self.assertEqual(self.js("report", *args, expect=2)["error"]["code"], "usage_error")
        self.assertEqual(self.js("report", "--in", "nonsense", expect=1)["error"]["code"], "unknown_currency")

    def test_in_accepts_a_currency_word(self):
        self.setup_ledger()
        self.assertEqual(self.js("report", "--month", "2026-10", "--in", "人民币")["data"]["in"], "CNY")

    def test_it_never_writes(self):
        self.setup_ledger()
        before = sorted(p.name for p in self.root.rglob("*.jsonl"))
        sizes = {p: p.stat().st_size for p in self.root.rglob("*.jsonl")}
        self.js("report", "--month", "2026-10")
        self.assertEqual(sorted(p.name for p in self.root.rglob("*.jsonl")), before)
        self.assertEqual({p: p.stat().st_size for p in self.root.rglob("*.jsonl")}, sizes)


if __name__ == "__main__":
    unittest.main()
