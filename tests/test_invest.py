"""Investing: lots, cost basis, realised and unrealised gains, and the ways it refuses to be wrong."""

import datetime as dt
import json
import unittest
from decimal import Decimal

from money_mom.inventory import Lot, choose_lots, replay
from money_mom.events import parse_event

from helpers import HUMAN, LedgerTestCase
from test_cli import CliTestCase

D = Decimal


class Lots(unittest.TestCase):
    def lot(self, units, price, day, symbol="AAPL"):
        return Lot("Assets:券商", symbol, D(units), D(price), "USD", dt.date(2026, 1, day), "t")

    def test_first_in_first_out_takes_the_oldest_lot_then_the_next(self):
        lots = [self.lot("10", "100", 5), self.lot("10", "120", 2), self.lot("10", "90", 9)]
        taken = choose_lots(lots, "Assets:券商", "AAPL", D("15"), "fifo")
        self.assertEqual([(l.date.day, u) for l, u in taken], [(2, D("10")), (5, D("5"))])

    def test_last_in_first_out_and_highest_cost_first(self):
        lots = [self.lot("10", "100", 5), self.lot("10", "120", 2), self.lot("10", "90", 9)]
        self.assertEqual([l.date.day for l, _ in choose_lots(lots, "Assets:券商", "AAPL", D("10"), "lifo")], [9])
        self.assertEqual([l.per_unit for l, _ in choose_lots(lots, "Assets:券商", "AAPL", D("10"), "hifo")], [D("120")])

    def test_asking_for_more_than_is_held_is_refused_and_other_symbols_and_accounts_are_not_touched(self):
        lots = [self.lot("10", "100", 5), self.lot("99", "1", 5, symbol="MSFT")]
        with self.assertRaises(ValueError):
            choose_lots(lots, "Assets:券商", "AAPL", D("10.5"), "fifo")
        with self.assertRaises(ValueError):
            choose_lots(lots, "Assets:别处", "AAPL", D("1"), "fifo")
        with self.assertRaises(ValueError):
            choose_lots(lots, "Assets:券商", "AAPL", D("1"), "average")


def event(kind="txn", **fields):
    raw = {"v": 1, "id": "e1", "kind": kind, "ts": "2026-10-07T12:00:00+08:00", "actor": {"type": "human", "name": "t"}}
    raw.update(fields)
    return parse_event(raw)


class CostParsing(unittest.TestCase):
    def txn(self, cost, ccy="AAPL"):
        return event(date="2026-01-02", status="posted", postings=[
            {"account": "Assets:券商", "amount": "10", "ccy": ccy, "cost": cost},
            {"account": "Assets:券商", "amount": "-1500", "ccy": "USD"},
        ])

    def test_a_lot_counts_for_its_cost_when_checked_for_balance(self):
        ev = self.txn({"per_unit": "150", "ccy": "USD", "date": "2026-01-02"})
        self.assertEqual(ev.postings[0].weight(), ("USD", D("1500")))
        self.assertEqual(ev.postings[1].weight(), ("USD", D("-1500")))

    def test_bad_costs_are_refused(self):
        for cost in ({"per_unit": "0", "ccy": "USD"}, {"per_unit": "-1", "ccy": "USD"}, {"per_unit": "150", "ccy": "AAPL"},
                     {"per_unit": "150"}, {"ccy": "USD"}, {"per_unit": "150", "ccy": "USD", "colour": "red"},
                     {"per_unit": "150", "ccy": "USD", "date": "soon"}, "150 USD"):
            with self.assertRaises(Exception, msg=str(cost)):
                self.txn(cost)

    def test_price_is_still_reserved(self):
        with self.assertRaises(Exception):
            event(date="2026-01-02", status="posted", postings=[
                {"account": "Assets:a", "amount": "1", "ccy": "X", "price": {}}, {"account": "Assets:b", "amount": "-1", "ccy": "X"}])


class Flow(CliTestCase):
    def setUp(self):
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.js("open", "Assets:券商", "--date", "2026-01-01")
        self.js("transfer", "100000", "--ccy", "USD", "--from", "期初", "--to", "Assets:券商", "--date", "2026-01-02")
        self.js("invest", "--date", "2026-01-01")

    def buy(self, units, symbol, price, date, *extra, expect=0):
        return self.js("buy", units, symbol, "--price", price, "--ccy", "USD", "--account", "Assets:券商", "--date", date, *extra, expect=expect)

    def sell(self, units, symbol, price, date, *extra, expect=0):
        return self.js("sell", units, symbol, "--price", price, "--ccy", "USD", "--account", "Assets:券商", "--date", date, *extra, expect=expect)

    def cash(self):
        data = self.js("balance", "--account", "Assets:券商")["data"]
        rows = data if isinstance(data, list) else data.get("balances", data)
        return {r["ccy"]: r["amount"] for r in rows} if isinstance(rows, list) else rows

    def test_buying_creates_a_lot_and_takes_the_cash_and_fee(self):
        data = self.buy("10", "AAPL", "150", "2026-02-02", "--fee", "1.25")["data"]
        self.assertEqual((data["cost"], data["fee"]), ("1500", "1.25"))
        bal = self.cash()
        self.assertEqual(bal["USD"], "98498.75")
        self.assertEqual(bal["AAPL"], "10")
        held = self.js("holdings", "--as-of", "2026-03-01", "--in", "USD", "--mark", "AAPL=160:USD")["data"]
        pos = held["positions"][0]
        self.assertEqual((pos["units"], pos["average_cost"], pos["market_value"], pos["unrealized_gain"]),
                         ("10", {"USD": "150"}, "1600", {"USD": "100"}))
        self.assertEqual(held["converted"]["unrealized_gain"], "100.00")

    def test_selling_takes_lots_first_in_first_out_and_writes_the_gain_into_the_entry(self):
        self.buy("10", "AAPL", "100", "2026-02-02")
        self.buy("10", "AAPL", "120", "2026-03-02")
        sold = self.sell("15", "AAPL", "130", "2026-04-02", "--fee", "2")["data"]
        self.assertEqual((sold["proceeds"], sold["cost_basis"], sold["realized_gain"]), ("1950", "1600", "350"))
        self.assertEqual([(l["units"], l["per_unit"]) for l in sold["lots"]], [("10", "100"), ("5", "120")])
        bal = self.cash()
        self.assertEqual((bal["AAPL"], bal["USD"]), ("5", "99748"))  # 100000 - 1000 - 1200 + 1950 - 2
        gains = self.js("query", "SELECT SUM(amount_text * 1) FROM v_postings WHERE account LIKE 'Income:投资:已实现%'")["data"]["rows"]
        self.assertEqual(float(gains[0][0]), -350.0)
        left = self.js("holdings", "--as-of", "2026-05-01", "--lots", "--in", "USD", "--mark", "AAPL=130:USD")["data"]["positions"][0]
        self.assertEqual((left["units"], left["lots"][0]["per_unit"], left["lots"][0]["units"]), ("5", "120", "5"))

    def test_lifo_and_hifo_choose_differently(self):
        self.buy("10", "AAPL", "100", "2026-02-02")
        self.buy("10", "AAPL", "120", "2026-03-02")
        self.buy("10", "AAPL", "90", "2026-03-20")
        self.assertEqual(self.sell("10", "AAPL", "130", "2026-04-02", "--lots", "hifo")["data"]["cost_basis"], "1200")
        self.assertEqual(self.sell("10", "AAPL", "130", "2026-04-03", "--lots", "lifo")["data"]["cost_basis"], "900")

    def test_selling_more_than_is_held_is_refused_and_nothing_is_written(self):
        self.buy("10", "AAPL", "100", "2026-02-02")
        before = self.js("check")["data"]["events"]
        refused = self.sell("11", "AAPL", "130", "2026-04-02", expect=1)
        self.assertEqual(refused["error"]["code"], "not_enough_shares")
        self.assertEqual(self.js("check")["data"]["events"], before)
        # shares bought later cannot be sold earlier
        self.buy("5", "MSFT", "300", "2026-06-01")
        self.assertEqual(self.sell("5", "MSFT", "310", "2026-05-01", expect=1)["error"]["code"], "not_enough_shares")

    def test_a_purchase_whose_shares_were_sold_cannot_be_voided(self):
        buy_id = self.buy("10", "AAPL", "100", "2026-02-02")["data"]["id"]
        sale_id = self.sell("10", "AAPL", "130", "2026-04-02")["data"]["id"]
        refused = self.js("void", buy_id, "--reason", "oops", expect=1)
        self.assertEqual(refused["error"]["code"], "lot_error")
        self.js("void", sale_id, "--reason", "oops")  # undo the sale first, then the purchase can go
        self.js("void", buy_id, "--reason", "oops")
        self.assertEqual(self.js("holdings", "--as-of", "2026-05-01")["data"]["positions"], [])

    def test_an_opening_position_brings_in_shares_without_moving_cash(self):
        data = self.buy("100", "AAPL", "50", "2026-01-05", "--opening")["data"]
        self.assertEqual(data["kind"], "opening")
        self.assertEqual(self.cash()["USD"], "100000")
        sold = self.sell("40", "AAPL", "60", "2026-06-01")["data"]
        self.assertEqual(sold["realized_gain"], "400")

    def test_dividends_with_tax_withheld(self):
        data = self.js("dividend", "100", "AAPL", "--to", "Assets:券商", "--ccy", "USD", "--tax", "10", "--date", "2026-05-15")["data"]
        self.assertEqual((data["gross"], data["tax"], data["net"]), ("100", "10", "90"))
        self.assertEqual(self.cash()["USD"], "100090")
        report = self.js("pnl", "--year", "2026")["data"]
        self.assertEqual(report["by_currency"]["USD"]["dividends"], "100")
        self.assertEqual(report["by_currency"]["USD"]["dividend_tax"], "10")

    def test_pnl_lists_each_sale_with_its_lots_and_the_fee_next_to_the_gain(self):
        self.buy("10", "AAPL", "100", "2026-02-02")
        self.sell("4", "AAPL", "110", "2026-03-04", "--fee", "1")
        data = self.js("pnl", "--year", "2026", "--in", "USD")["data"]
        sale = data["sales"][0]
        self.assertEqual((sale["realized_gain"], sale["fee"], sale["gain_after_fee"]), ("40", "1", "39"))
        self.assertEqual(sale["lots"], [{"units": "4", "per_unit": "100", "bought": "2026-02-02", "days_held": 30}])
        self.assertEqual(self.js("pnl", "--year", "2025")["data"]["sales"], [])
        self.assertEqual(data["by_symbol"][0]["symbol"], "AAPL")

    def test_holdings_never_values_an_unpriced_position_at_zero(self):
        self.buy("10", "AAPL", "100", "2026-02-02")
        self.js("rates", "set", "USD", "CNY", "7", "--source", "test", "--date", "2026-03-01")
        held = self.js("holdings", "--as-of", "2026-03-01")["data"]
        pos = held["positions"][0]
        # no stored price and no mark: the only price is what it last traded at, and it says so
        self.assertEqual((pos["price"], pos["price_source"]), ("100", "last trade"))
        self.js("rates", "set", "AAPL", "USD", "130", "--source", "my broker app", "--date", "2026-03-01")
        held = self.js("holdings", "--as-of", "2026-03-01")["data"]
        self.assertEqual((held["positions"][0]["price_source"], held["converted"]["market_value"], held["converted"]["partial"]), ("stored price", "9100.00", False))
        # a position in a symbol with no trade price at all would be listed as missing: simulated by an unknown mark currency
        only_fx = self.js("holdings", "--as-of", "2026-03-01", "--in", "JPY")["data"]
        self.assertTrue(only_fx["converted"]["partial"])
        self.assertIn("AAPL", only_fx["converted"]["missing"])

    def test_the_investing_accounts_are_needed_and_can_be_created_once(self):
        fresh = self.dir / "fresh"
        self.run_cli("--ledger", str(fresh), "init", "--template", "cn", "--date", "2026-01-01")
        self.run_cli("--ledger", str(fresh), "open", "Assets:券商", "--date", "2026-01-01")
        out = self.js_at(fresh, "buy", "1", "AAPL", "--price", "1", "--ccy", "USD", "--account", "Assets:券商", "--fee", "1", "--date", "2026-01-02", expect=1)
        self.assertEqual(out["error"]["code"], "no_investment_accounts")
        first = self.js("invest")["data"]
        self.assertEqual(first["opened"], [])  # setUp already created them

    def js_at(self, root, *args, expect=0):
        out, _ = self.run_cli("--ledger", str(root), "--json", *args, expect=expect)
        return json.loads(out)

    def raw_sale(self, status="posted"):
        return json.dumps([{"date": "2026-04-02", "status": status, "narration": "hand made", "postings": [
            {"account": "Assets:券商", "amount": "-5", "ccy": "AAPL", "cost": {"per_unit": "100", "ccy": "USD", "date": "2026-02-02"}},
            {"account": "Assets:券商", "amount": "500", "ccy": "USD"}]}])

    def test_a_hand_written_entry_cannot_take_shares_from_a_lot_that_does_not_exist(self):
        refused = self.js("add", "--from-json", "-", stdin=self.raw_sale(), expect=1)
        self.assertEqual(refused["error"]["code"], "lot_error")
        self.buy("10", "AAPL", "100", "2026-02-02")
        self.js("add", "--from-json", "-", stdin=self.raw_sale())  # now the lot is there
        self.assertEqual(self.js("holdings", "--as-of", "2026-05-01")["data"]["positions"][0]["units"], "5")

    def test_a_pending_sale_is_checked_against_the_lots_when_it_is_confirmed(self):
        pending = self.js("add", "--from-json", "-", stdin=self.raw_sale("pending"))["data"][0]["id"]
        self.assertEqual(self.js("confirm", pending, expect=1)["error"]["code"], "lot_error")
        self.buy("10", "AAPL", "100", "2026-02-02")
        self.js("confirm", pending)

    def test_the_sale_must_be_in_the_currency_the_shares_were_bought_in(self):
        self.buy("10", "0700.HK", "300", "2026-02-02")
        refused = self.js("sell", "5", "0700.HK", "--price", "310", "--ccy", "HKD", "--account", "Assets:券商", "--date", "2026-04-02", expect=1)
        self.assertEqual(refused["error"]["code"], "currency_mismatch")

    def test_the_tax_withheld_must_be_less_than_the_dividend(self):
        refused = self.js("dividend", "10", "AAPL", "--to", "Assets:券商", "--ccy", "USD", "--tax", "10", "--date", "2026-05-15", expect=2)
        self.assertEqual(refused["error"]["code"], "usage_error")

    def test_a_price_in_another_currency_is_only_compared_with_the_cost_through_a_stored_rate(self):
        self.buy("10", "AAPL", "100", "2026-02-02")
        none = self.js("holdings", "--as-of", "2026-03-01", "--mark", "AAPL=800:HKD")["data"]["positions"][0]
        self.assertEqual((none["market_value"], none["unrealized_gain"]), ("8000", None))  # no HKD->USD rate: no gain is made up
        self.js("rates", "set", "HKD", "USD", "0.125", "--source", "test", "--date", "2026-03-01")
        some = self.js("holdings", "--as-of", "2026-03-01", "--mark", "AAPL=800:HKD")["data"]["positions"][0]
        self.assertEqual({k: D(v) for k, v in some["unrealized_gain"].items()}, {"USD": D(0)})  # 8000 HKD = 1000 USD = the cost

    def test_the_currency_is_never_guessed_and_numbers_must_be_numbers(self):
        self.assertEqual(self.js("buy", "10", "AAPL", "--price", "150", "--account", "Assets:券商", expect=2)["error"]["code"], "usage_error")
        self.assertEqual(self.buy("ten", "AAPL", "150", "2026-02-02", expect=2)["error"]["code"], "usage_error")
        self.assertEqual(self.buy("10", "AAPL", "-5", "2026-02-02", expect=2)["error"]["code"], "usage_error")
        self.assertEqual(self.buy("10", "aapl", "150", "2026-02-02")["data"]["symbol"], "AAPL")

    def test_an_agent_must_state_a_confidence(self):
        denied = self.buy("10", "AAPL", "150", "2026-02-02", "--actor", "agent:claude-code", expect=1)
        self.assertEqual(denied["error"]["code"], "confidence_required")
        self.buy("10", "AAPL", "150", "2026-02-02", "--actor", "agent:claude-code", "--confidence", "0.95")

    def test_fractional_shares_and_exact_arithmetic(self):
        self.buy("0.5", "AAPL", "190.123", "2026-02-02")
        sold = self.sell("0.5", "AAPL", "200.001", "2026-02-03")["data"]
        self.assertEqual((sold["proceeds"], sold["cost_basis"], sold["realized_gain"]), ("100.0005", "95.0615", "4.9390"))

    def test_the_chain_and_check_stay_healthy_after_trading(self):
        self.buy("10", "AAPL", "100", "2026-02-02")
        self.sell("3", "AAPL", "110", "2026-03-04")
        self.assertTrue(self.js("verify")["data"]["ok"])
        self.assertTrue(self.js("check")["data"]["ok"])


if __name__ == "__main__":
    unittest.main()


class ByAccount(CliTestCase):
    def setUp(self):
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.js("open", "Assets:券商", "--date", "2026-01-01")
        self.js("invest", "--date", "2026-01-01")
        self.js("transfer", "5000", "--from", "期初", "--to", "银行卡", "--date", "2026-01-02")
        self.js("transfer", "2000", "--ccy", "USD", "--from", "期初", "--to", "Assets:券商", "--date", "2026-01-02")
        self.js("buy", "10", "AAPL", "--price", "100", "--ccy", "USD", "--account", "Assets:券商", "--date", "2026-02-02")
        self.js("rates", "set", "USD", "CNY", "7", "--source", "test", "--date", "2026-03-01")

    def test_each_account_is_converted_and_a_holding_without_a_price_makes_it_partial(self):
        data = self.js("networth", "--by-account", "--as-of", "2026-03-01")["data"]
        by = {a["account"]: a for a in data["accounts"]}
        self.assertEqual(by["Assets:银行卡"]["total"], "5000.00")
        self.assertEqual((by["Assets:券商"]["total"], by["Assets:券商"]["partial"], by["Assets:券商"]["missing"]), ("7000.00", True, ["AAPL"]))
        self.assertEqual((data["partial"], data["missing"]), (True, ["AAPL"]))
        self.js("rates", "set", "AAPL", "USD", "130", "--source", "broker app", "--date", "2026-03-01")
        priced = self.js("networth", "--by-account", "--as-of", "2026-03-01")["data"]
        by = {a["account"]: a for a in priced["accounts"]}
        # 1000 USD cash + 10 AAPL at 130 USD = 2300 USD = 16100 CNY
        self.assertEqual((by["Assets:券商"]["total"], by["Assets:券商"]["partial"]), ("16100.00", False))
        self.assertEqual((priced["assets"], priced["net_worth"], priced["partial"]), ("21100.00", "21100.00", False))
        self.assertEqual(by["Assets:券商"]["share_of_assets"], "76.3")
        # the plain networth agrees with the sum of the accounts
        self.assertEqual(self.js("networth", "--as-of", "2026-03-01")["data"]["total"], priced["net_worth"])

    def test_the_text_view_lists_every_account(self):
        out, _ = self.run_cli("--ledger", str(self.root), "networth", "--by-account", "--as-of", "2026-03-01")
        self.assertIn("Net worth by account", out)
        self.assertIn("Assets:银行卡", out)
        self.assertIn("PARTIAL", out)
