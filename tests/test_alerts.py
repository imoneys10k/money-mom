import datetime as dt
import unittest
from decimal import Decimal

from money_mom.alerts import RULES, find_alerts, normalise, summarise
from money_mom.errors import LedgerError

from helpers import HUMAN, LedgerTestCase
from test_cli import CliTestCase

D = Decimal
TODAY = dt.date(2026, 10, 7)


class AlertCase(LedgerTestCase):
    def setUp(self):
        super().setUp()
        for account in ("Assets:CMB", "Assets:USD", "Expenses:Subscriptions", "Expenses:Dining", "Expenses:Rent",
                        "Expenses:Gym", "Expenses:Shopping", "Expenses:Travel", "Income:Salary"):
            kw = {"currencies": ["USD"]} if account == "Assets:USD" else {}
            self.ledger.open_account(account, "2025-01-01", **kw)
        self.ledger.add_txn("2025-01-01", [("Assets:CMB", "500000", "CNY"), ("Income:Salary", "-500000", "CNY")])

    def spend(self, date, category, amount, payee=None, ccy="CNY"):
        source = "Assets:USD" if ccy == "USD" else "Assets:CMB"
        return self.ledger.add_txn(date, [(f"Expenses:{category}", str(amount), ccy), (source, f"-{amount}", ccy)], payee=payee)

    def monthly(self, payee, category, amounts, day=12, start=(2026, 4), ccy="CNY"):
        year, month = start
        events = []
        for amount in amounts:
            events.append(self.spend(f"{year:04d}-{month:02d}-{day:02d}", category, amount, payee, ccy))
            month += 1
            if month > 12:
                year, month = year + 1, 1
        return events

    def find(self, month="2026-09", **kw):
        kw.setdefault("today", TODAY)
        return find_alerts(self.ledger, month, **kw)

    def kinds(self, result):
        return [a["type"] for a in result["alerts"]]

    def subs(self, result):
        return {s["payee"]: s for s in result["subscriptions"]}


class Recurring(AlertCase):
    def test_a_monthly_charge_is_recognised_with_its_evidence(self):
        events = self.monthly("Netflix", "Subscriptions", ["68.00"] * 6)  # Apr..Sep
        s = self.subs(self.find())["Netflix"]
        self.assertEqual((s["cadence"], s["charges"], s["typical"], s["latest"]), ("monthly", 6, "68.00", "68.00"))
        self.assertTrue(s["amount_is_steady"])
        self.assertEqual(s["status"], "active")
        self.assertEqual(s["last_date"], "2026-09-12")
        self.assertEqual(s["next_expected"], "2026-10-12")
        self.assertEqual(s["entries"], [e.id for e in events])
        self.assertEqual(s["per_month"], "68.00")

    def test_not_enough_charges_is_not_a_subscription(self):
        self.monthly("Netflix", "Subscriptions", ["68.00"] * 2, start=(2026, 8))
        self.assertEqual(self.find()["subscriptions"], [])

    def test_irregular_spacing_is_not_a_subscription(self):
        for date in ("2026-04-03", "2026-04-29", "2026-06-20", "2026-07-02", "2026-09-30"):
            self.spend(date, "Dining", "50", "海底捞")
        self.assertEqual(self.find()["subscriptions"], [])

    def test_weekly_and_yearly_cadences(self):
        for week in range(6):
            self.spend((dt.date(2026, 8, 3) + dt.timedelta(days=7 * week)).isoformat(), "Dining", "30", "菜市场")
        self.spend("2025-09-15", "Subscriptions", "99", "iCloud Year")
        self.spend("2026-09-15", "Subscriptions", "99", "iCloud Year")
        subs = self.subs(self.find())
        self.assertEqual(subs["菜市场"]["cadence"], "weekly")
        self.assertEqual(subs["iCloud Year"]["cadence"], "yearly")
        self.assertEqual(subs["iCloud Year"]["per_month"], "8.25")

    def test_three_weekly_charges_are_not_yet_a_pattern(self):
        for week in range(3):
            self.spend((dt.date(2026, 9, 1) + dt.timedelta(days=7 * week)).isoformat(), "Dining", "30", "菜市场")
        self.assertEqual(self.find()["subscriptions"], [])

    def test_a_pattern_needs_most_of_its_gaps_to_fit(self):
        # gaps 30, 60, 5, 30, 30: the median is 30 but only 3 of 5 gaps fit a month
        for date in ("2026-04-01", "2026-05-01", "2026-06-30", "2026-07-05", "2026-08-04", "2026-09-03"):
            self.spend(date, "Dining", "50", "海底捞")
        self.assertEqual(self.find()["subscriptions"], [])
        # one skipped month among six charges still counts (4 of 5 gaps fit)
        for date in ("2026-03-01", "2026-04-01", "2026-05-01", "2026-07-01", "2026-08-01", "2026-09-01"):
            self.spend(date, "Gym", "300", "健身房")
        self.assertEqual(list(self.subs(self.find())), ["健身房"])

    def test_entries_without_a_payee_are_never_grouped(self):
        for month in range(4, 10):
            self.spend(f"2026-{month:02d}-12", "Subscriptions", "68")
        result = self.find()
        self.assertEqual(result["subscriptions"], [])
        self.assertTrue(any("payee" in n for n in result["notes"]))

    def test_payees_match_exactly_after_normalising_case_and_spacing(self):
        self.assertEqual(normalise("  NetFlix   Inc "), normalise("netflix inc"))
        self.assertEqual(normalise("ＮＥＴＦＬＩＸ"), normalise("netflix"))
        self.assertNotEqual(normalise("Netflix"), normalise("Netflix Inc"))
        for month, payee in zip(range(4, 10), ("Netflix", "NETFLIX", "netflix ", "Netflix", "NetFlix", "netflix")):
            self.spend(f"2026-{month:02d}-12", "Subscriptions", "68", payee)
        self.assertEqual(len(self.find()["subscriptions"]), 1)

    def test_different_currencies_are_different_charges(self):
        self.monthly("Spotify", "Subscriptions", ["10.00"] * 3, start=(2026, 7), ccy="USD")
        self.monthly("Spotify", "Subscriptions", ["12.00"] * 1, start=(2026, 4))
        subs = self.find()["subscriptions"]
        self.assertEqual([(s["payee"], s["ccy"]) for s in subs], [("Spotify", "USD")])

    def test_lines_of_one_entry_are_one_charge(self):
        for month in range(4, 10):
            self.ledger.add_txn(f"2026-{month:02d}-12", [
                ("Expenses:Subscriptions", "30", "CNY"), ("Expenses:Dining", "8", "CNY"), ("Assets:CMB", "-38", "CNY")
            ], payee="Bundle")
        s = self.subs(self.find())["Bundle"]
        self.assertEqual((s["charges"], s["latest"]), (6, "38.00"))
        self.assertEqual(len(self.find()["subscriptions"]), 1)

    def test_refunds_are_not_charges(self):
        self.monthly("Netflix", "Subscriptions", ["68.00"] * 4, start=(2026, 5))
        self.ledger.add_txn("2026-09-20", [("Expenses:Subscriptions", "-68", "CNY"), ("Assets:CMB", "68", "CNY")], payee="Netflix")
        self.assertEqual(self.subs(self.find())["Netflix"]["charges"], 4)

    def test_monthly_total_converts_and_flags_missing_rates(self):
        self.monthly("Netflix", "Subscriptions", ["68.00"] * 5)
        self.monthly("Spotify", "Subscriptions", ["10.00"] * 4, start=(2026, 6), ccy="USD")
        result = self.find()
        total = result["subscription_total"]
        self.assertTrue(total["partial"])
        self.assertEqual((total["per_month"], total["missing"], total["count"], total["varying"]), ("68.00", ["USD"], 2, 0))
        self.assertIsNone(self.subs(result)["Spotify"]["per_month"])
        self.ledger.append(self.ledger.new_event("price", actor=HUMAN, date="2026-09-30", base="USD", quote="CNY",
                                                 rate="7", source={"type": "manual", "ref": "t"}), clamp_ts=True)
        total = self.find()["subscription_total"]
        self.assertEqual((total["per_month"], total["partial"]), ("138.00", False))


class PriceChange(AlertCase):
    def test_a_steady_charge_that_rises_is_reported_with_the_percentage(self):
        self.monthly("Netflix", "Subscriptions", ["68.00"] * 5 + ["78.00"])
        result = self.find()
        alert = next(a for a in result["alerts"] if a["type"] == "price_change")
        self.assertEqual((alert["from"], alert["to"], alert["percent"], alert["date"]), ("68.00", "78.00", "14.7", "2026-09-12"))
        self.assertEqual(len(alert["entries"]), 2)
        self.assertEqual(self.subs(result)["Netflix"]["price_change"]["to"], "78.00")

    def test_small_wobble_in_the_earlier_charges_still_counts_as_steady(self):
        self.monthly("Netflix", "Subscriptions", ["68.00", "68.40", "68.00", "68.40", "68.00", "78.00"])
        self.assertIn("price_change", self.kinds(self.find()))

    def test_a_drop_is_reported_too(self):
        self.monthly("Netflix", "Subscriptions", ["78.00"] * 5 + ["68.00"])
        alert = next(a for a in self.find()["alerts"] if a["type"] == "price_change")
        self.assertEqual(alert["percent"], "-12.8")

    def test_changes_within_two_percent_are_not_news(self):
        self.monthly("Netflix", "Subscriptions", ["68.00"] * 5 + ["68.90"])
        self.assertNotIn("price_change", self.kinds(self.find()))

    def test_a_charge_that_always_varies_is_not_a_price_change(self):
        self.monthly("电费", "Subscriptions", ["120", "98", "143", "87", "131", "160"])
        result = self.find()
        self.assertNotIn("price_change", self.kinds(result))
        self.assertFalse(self.subs(result)["电费"]["amount_is_steady"])

    def test_it_is_reported_in_the_month_it_happened_only(self):
        self.monthly("Netflix", "Subscriptions", ["68.00"] * 5 + ["78.00"])
        self.assertNotIn("price_change", self.kinds(self.find("2026-10")))
        self.assertIn("price_change", self.kinds(self.find("2026-09")))


class NextDue(unittest.TestCase):
    def test_calendar_months_clamp_to_the_end_of_short_months(self):
        from money_mom.alerts import _next_due
        d = dt.date
        self.assertEqual(_next_due(d(2026, 1, 31), "monthly", 30), d(2026, 2, 28))
        self.assertEqual(_next_due(d(2028, 1, 31), "monthly", 30), d(2028, 2, 29))
        self.assertEqual(_next_due(d(2026, 12, 15), "monthly", 30), d(2027, 1, 15))
        self.assertEqual(_next_due(d(2026, 3, 30), "monthly", 30), d(2026, 4, 30))
        self.assertEqual(_next_due(d(2028, 2, 29), "yearly", 365), d(2029, 2, 28))
        self.assertEqual(_next_due(d(2026, 9, 1), "weekly", 7), d(2026, 9, 8))


class Overdue(AlertCase):
    def test_a_charge_that_stops_is_flagged_after_the_grace_period(self):
        self.monthly("Gym", "Gym", ["300"] * 4, start=(2026, 5))  # last: Aug 12, due Sep 12
        result = self.find("2026-09")
        s = self.subs(result)["Gym"]
        self.assertEqual(s["status"], "overdue")
        self.assertEqual((s["next_expected"], s["days_late"]), ("2026-09-12", 18))
        self.assertIn("overdue", self.kinds(result))
        self.assertTrue(any("cancelled" in n for n in result["notes"]))

    def test_within_the_grace_period_it_is_still_active(self):
        self.monthly("Gym", "Gym", ["300"] * 4, start=(2026, 5))  # last Aug 12, due Sep 11
        result = self.find("2026-09", today=dt.date(2026, 9, 15))
        self.assertEqual(self.subs(result)["Gym"]["status"], "active")
        self.assertNotIn("overdue", self.kinds(result))

    def test_long_gone_charges_are_lapsed_not_nagged_about(self):
        self.monthly("Old", "Gym", ["300"] * 4, start=(2026, 1))  # last Apr 12
        result = self.find("2026-09")
        self.assertEqual(self.subs(result)["Old"]["status"], "lapsed")
        self.assertNotIn("overdue", self.kinds(result))
        self.assertEqual(result["subscription_total"]["count"], 0)

    def test_the_past_is_judged_as_of_that_month(self):
        self.monthly("Gym", "Gym", ["300"] * 4, start=(2026, 4))
        self.assertEqual(self.subs(self.find("2026-07"))["Gym"]["status"], "active")


class LargeExpense(AlertCase):
    def history(self):
        for month, amount in zip(range(3, 9), (40, 55, 50, 45, 60, 52)):
            self.spend(f"2026-{month:02d}-10", "Shopping", amount, "超市")

    def test_an_expense_far_above_the_usual_and_the_largest_yet_is_flagged(self):
        self.history()
        event = self.spend("2026-09-15", "Shopping", "400", "电商")
        alert = next(a for a in self.find()["alerts"] if a["type"] == "large_expense")
        self.assertEqual((alert["amount"], alert["usual"], alert["category"], alert["entries"]), ("400.00", "51.00", "Shopping", [event.id]))
        self.assertEqual(alert["times_usual"], "7.8")

    def test_a_record_from_before_the_look_back_does_not_hide_a_new_alert(self):
        self.history()
        self.spend("2026-02-01", "Shopping", "900", "电商")  # more than seven months earlier
        self.spend("2026-09-15", "Shopping", "400", "电商")
        self.assertIn("large_expense", self.kinds(self.find()))

    def test_not_enough_history_means_silence(self):
        for month in (6, 7, 8):
            self.spend(f"2026-{month:02d}-10", "Shopping", "50")
        self.spend("2026-09-15", "Shopping", "5000")
        self.assertNotIn("large_expense", self.kinds(self.find()))

    def test_a_big_but_not_record_breaking_expense_is_normal(self):
        self.history()
        self.spend("2026-02-01", "Shopping", "900")  # before the look-back window: ignored
        self.spend("2026-03-20", "Shopping", "300")  # inside the window: the record is now 300
        self.spend("2026-09-15", "Shopping", "290")
        self.assertNotIn("large_expense", self.kinds(self.find()))

    def test_it_must_be_three_times_the_median(self):
        self.history()
        self.spend("2026-09-15", "Shopping", "150")  # 2.9 x 51
        self.assertNotIn("large_expense", self.kinds(self.find()))

    def test_categories_and_currencies_are_compared_separately(self):
        self.history()
        self.spend("2026-09-15", "Travel", "4000")
        self.assertNotIn("large_expense", self.kinds(self.find()))


class Spike(AlertCase):
    def steady(self):
        for month in (6, 7, 8):
            self.spend(f"2026-{month:02d}-05", "Dining", "1000", "餐厅")
            self.spend(f"2026-{month:02d}-06", "Rent", "3000", "房东")

    def test_a_sudden_jump_over_the_three_month_average(self):
        self.steady()
        self.spend("2026-09-05", "Dining", "1000", "餐厅")
        self.spend("2026-09-20", "Dining", "900", "聚餐")
        self.spend("2026-09-06", "Rent", "3000", "房东")
        alert = next(a for a in self.find()["alerts"] if a["type"] == "category_spike")
        self.assertEqual((alert["category"], alert["amount"], alert["average_before"], alert["times_average"]),
                         ("Dining", "1900.00", "1000.00", "1.9"))
        self.assertNotIn("Rent", [a.get("category") for a in self.find()["alerts"] if a["type"] == "category_spike"])

    def test_under_one_and_a_half_times_is_normal(self):
        self.steady()
        self.spend("2026-09-05", "Dining", "1400", "餐厅")
        self.assertNotIn("category_spike", self.kinds(self.find()))

    def test_it_needs_three_months_with_entries(self):
        for month in (7, 8):
            self.spend(f"2026-{month:02d}-05", "Dining", "100", "餐厅")
        self.spend("2026-09-05", "Dining", "5000", "餐厅")
        self.assertNotIn("category_spike", self.kinds(self.find()))

    def test_a_category_that_is_new_has_no_average_to_compare_with(self):
        self.steady()
        self.spend("2026-09-05", "Travel", "5000")
        self.assertNotIn("category_spike", [a["type"] for a in self.find()["alerts"] if a.get("category") == "Travel"])

    def test_a_trivial_category_does_not_raise_an_alert(self):
        self.steady()
        for month in (6, 7, 8):
            self.spend(f"2026-{month:02d}-07", "Gym", "10")
        self.spend("2026-09-07", "Gym", "30")  # 3x its average but 0.6% of the month's spending
        self.spend("2026-09-05", "Rent", "3000")
        self.spend("2026-09-09", "Dining", "1000")
        self.assertNotIn("Gym", [a.get("category") for a in self.find()["alerts"]])


class Duplicate(AlertCase):
    def test_same_payee_same_amount_within_two_days(self):
        a = self.spend("2026-09-10", "Dining", "88.00", "海底捞")
        b = self.spend("2026-09-12", "Dining", "88.00", "海底捞 ")
        alert = next(x for x in self.find()["alerts"] if x["type"] == "possible_duplicate")
        self.assertEqual((alert["amount"], alert["days_apart"], alert["entries"]), ("88.00", 2, [a.id, b.id]))

    def test_three_days_apart_or_different_amount_or_currency_is_not(self):
        self.spend("2026-09-10", "Dining", "88.00", "海底捞")
        self.spend("2026-09-13", "Dining", "88.00", "海底捞")
        self.spend("2026-09-14", "Dining", "88.50", "海底捞")
        self.spend("2026-09-14", "Dining", "88.00", "海底捞", ccy="USD")
        self.assertNotIn("possible_duplicate", self.kinds(self.find()))

    def test_without_a_payee_it_is_not_judged(self):
        self.spend("2026-09-10", "Dining", "88.00")
        self.spend("2026-09-10", "Dining", "88.00")
        self.assertNotIn("possible_duplicate", self.kinds(self.find()))

    def test_only_the_asked_month_is_checked(self):
        self.spend("2026-08-10", "Dining", "88.00", "海底捞")
        self.spend("2026-08-11", "Dining", "88.00", "海底捞")
        self.assertNotIn("possible_duplicate", self.kinds(self.find("2026-09")))
        self.assertIn("possible_duplicate", self.kinds(self.find("2026-08")))

    def test_voided_entries_do_not_count(self):
        self.spend("2026-09-10", "Dining", "88.00", "海底捞")
        second = self.spend("2026-09-11", "Dining", "88.00", "海底捞")
        self.ledger.void(second.id, "entered twice")
        self.assertNotIn("possible_duplicate", self.kinds(self.find()))


class Output(AlertCase):
    def test_every_alert_has_a_summary_and_evidence(self):
        self.monthly("Netflix", "Subscriptions", ["68.00"] * 5 + ["78.00"])
        self.spend("2026-09-14", "Dining", "88.00", "海底捞")
        self.spend("2026-09-15", "Dining", "88.00", "海底捞")
        for alert in self.find()["alerts"]:
            self.assertTrue(alert["summary"])
            self.assertTrue(alert["entries"])
            for entry in alert["entries"]:
                self.assertIn(entry, self.ledger.state.txns)

    def test_alerts_are_ordered_and_the_rules_are_included(self):
        self.monthly("Netflix", "Subscriptions", ["68.00"] * 5 + ["78.00"])
        self.spend("2026-09-14", "Dining", "88.00", "海底捞")
        self.spend("2026-09-15", "Dining", "88.00", "海底捞")
        result = self.find()
        self.assertEqual(self.kinds(result)[:2], ["price_change", "possible_duplicate"])
        self.assertEqual(result["rules"], RULES)

    def test_a_short_history_says_so_and_invents_nothing(self):
        self.spend("2026-09-10", "Dining", "50", "海底捞")
        result = self.find()
        self.assertEqual((result["alerts"], result["subscriptions"]), ([], []))
        self.assertTrue(any("three months" in n for n in result["notes"]))

    def test_an_empty_ledger_is_fine(self):
        result = self.find("2026-09")
        self.assertEqual(result["alerts"], [])

    def test_a_month_that_has_not_started_is_refused(self):
        with self.assertRaises(LedgerError) as ctx:
            self.find("2026-11")
        self.assertEqual(ctx.exception.code, "usage_error")

    def test_summaries_name_the_figures(self):
        text = summarise({"type": "price_change", "payee": "N", "to": "78.00", "ccy": "CNY", "from": "68.00",
                          "percent": "14.7", "date": "2026-09-12"})
        for part in ("N", "78.00", "68.00", "14.7"):
            self.assertIn(part, text)

    def test_nothing_is_written(self):
        self.monthly("Netflix", "Subscriptions", ["68.00"] * 5)
        before = self.total_lines()
        self.find()
        self.assertEqual(self.total_lines(), before)


class Cli(CliTestCase):
    def test_alerts_command(self):
        self.js("init")
        for account in ("Assets:CMB", "Expenses:Subscriptions"):
            self.js("open", account, "--date", "2026-01-01")
        for month in ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09"):
            self.js("add", "--date", f"{month}-12", "--posting", "Expenses:Subscriptions 68.00 CNY",
                    "--posting", "Assets:CMB -68.00 CNY", "--payee", "Netflix")
        self.js("add", "--date", "2026-10-02", "--posting", "Expenses:Subscriptions 78.00 CNY",
                "--posting", "Assets:CMB -78.00 CNY", "--payee", "Netflix")
        data = self.js("alerts", "--month", "2026-10")["data"]
        self.assertEqual(data["alerts"][0]["type"], "price_change")
        self.assertEqual(data["subscriptions"][0]["payee"], "Netflix")
        out, _ = self.run_cli("--ledger", str(self.root), "alerts", "--month", "2026-10")
        self.assertIn("Netflix", out)
        self.assertIn("price_change", out)
        self.assertIn("78.00", out)

    def test_the_text_says_which_amounts_vary(self):
        self.js("init")
        for account in ("Assets:CMB", "Expenses:Utilities"):
            self.js("open", account, "--date", "2026-01-01")
        for month, amount in zip(("05", "06", "07", "08", "09"), ("120.00", "98.00", "143.00", "87.00", "131.00")):
            self.js("add", "--date", f"2026-{month}-12", "--posting", f"Expenses:Utilities {amount} CNY",
                    "--posting", f"Assets:CMB -{amount} CNY", "--payee", "电力公司")
        out, _ = self.run_cli("--ledger", str(self.root), "alerts", "--month", "2026-09")
        self.assertIn("varies", out)
        self.assertIn("1 of them vary in amount", out)

    def test_nothing_to_report_reads_calmly(self):
        self.setup_ledger()
        out, _ = self.run_cli("--ledger", str(self.root), "alerts", "--month", "2026-10")
        self.assertIn("Nothing stands out", out)
        self.assertIn("No recurring charges recognised", out)

    def test_bad_input(self):
        self.setup_ledger()
        self.assertEqual(self.js("alerts", "--month", "oct", expect=2)["error"]["code"], "usage_error")
        self.assertEqual(self.js("alerts", "--month", "2099-01", expect=2)["error"]["code"], "usage_error")


if __name__ == "__main__":
    unittest.main()
