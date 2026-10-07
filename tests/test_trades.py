"""Importing trades: a TradeGit journal (read-only) and rows an agent read off a statement."""

import json
import unittest

from test_cli import CliTestCase


def trade(i, ts, side, symbol, qty, price, *, currency="USD", fees=0.0, account="ibkr-main", asset="STK", **extra):
    return {"id": f"trd_{i}", "schema_version": 1, "kind": "trade", "ts": ts, "account": account, "broker": "IBKR", "symbol": symbol,
            "asset_class": asset, "currency": currency, "side": side, "quantity": qty, "price": price, "multiplier": 1,
            "fees": {"commission": fees}, "fees_total": fees, "dedup_key": f"ext:ibkr:{i}", **extra}


def cash(i, ts, kind, amount, *, symbol=None, currency="USD", account="ibkr-main"):
    rec = {"id": f"csh_{i}", "kind": "cash", "ts": ts, "account": account, "cash_type": kind, "amount": amount, "currency": currency,
           "dedup_key": f"ext:ibkr:c{i}"}
    if symbol:
        rec["symbol"] = symbol
    return rec


JOURNAL = [
    trade(1, "2026-02-02T15:30:00Z", "BUY", "AAPL", 10, 100.0, fees=1.0),
    trade(2, "2026-03-02T15:30:00Z", "BUY", "AAPL", 10, 120.0),
    trade(3, "2026-04-02T15:30:00Z", "SELL", "AAPL", 15, 130.0, fees=1.5),
    cash(4, "2026-04-10T00:00:00Z", "DIVIDEND", 25.0, symbol="AAPL"),
    cash(5, "2026-04-11T00:00:00Z", "TAX", -2.5, symbol="AAPL"),
    cash(6, "2026-01-05T00:00:00Z", "DEPOSIT", 5000.0),
    cash(7, "2026-05-01T00:00:00Z", "INTEREST", 1.2),
    cash(8, "2026-05-02T00:00:00Z", "FEE", -3.0),
]


class Base(CliTestCase):
    def setUp(self):
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.js("open", "Assets:IBKR", "--date", "2026-01-01")
        self.js("invest", "--date", "2026-01-01")
        self.files = self.dir / "files"
        self.files.mkdir()

    def journal(self, records, name="2026-05.jsonl"):
        path = self.files / name
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return path

    def run_import(self, path, *extra, expect=0, fmt="tradegit"):
        return self.js("trades", "import", str(path), "--format", fmt, "--account", "Assets:IBKR", *extra, expect=expect)

    def balance(self):
        rows = self.js("balance", "--account", "Assets:IBKR")["data"]["balances"]
        return {r["ccy"]: r["amount"] for r in rows}


class TradeGit(Base):
    def test_a_journal_becomes_lots_sales_dividends_and_cash_events(self):
        data = self.run_import(self.journal(JOURNAL))["data"]
        self.assertEqual((data["buys"], data["sells"], data["dividends"], data["other_cash"], data["imported"]), (2, 1, 1, 4, 8))
        bal = self.balance()
        # the 5000 deposit is pending (its other side is unknown); cash: -1001 -1200 +1948.5 +25 -2.5 +1.2 -3
        self.assertEqual((bal["AAPL"], bal["USD"]), ("5", "-231.8"))
        pnl = self.js("pnl", "--year", "2026")["data"]
        self.assertEqual(pnl["sales"][0]["realized_gain"], "350")
        self.assertEqual(pnl["sales"][0]["lots"][0]["per_unit"], "100")
        self.assertEqual([p["id"] for p in self.js("pending")["data"]].__len__(), 1)  # the deposit waits for its source account

    def test_importing_again_is_safe(self):
        path = self.journal(JOURNAL)
        self.run_import(path)
        again = self.run_import(path)["data"]
        self.assertEqual((again["imported"], again["duplicates_skipped"]), (0, 8))
        self.assertTrue(self.js("verify")["data"]["ok"])

    def test_corrections_and_voids_in_the_journal_are_applied(self):
        records = JOURNAL[:3] + [
            {"kind": "void", "voids": "trd_2", "ts": "2026-03-03T00:00:00Z"},
            trade(9, "2026-03-02T15:30:00Z", "BUY", "AAPL", 10, 110.0, supersedes="trd_9_old"),
        ]
        # the sale of 15 needs the second lot, which was voided and replaced by a 110 one
        data = self.run_import(self.journal(records))["data"]
        self.assertEqual((data["buys"], data["sells"]), (2, 1))
        self.assertEqual(self.js("pnl", "--year", "2026")["data"]["sales"][0]["cost_basis"], "1550")

    def test_a_record_that_supersedes_another_replaces_it(self):
        records = JOURNAL[:3] + [trade(9, "2026-03-02T15:30:00Z", "BUY", "AAPL", 10, 110.0, supersedes="trd_2")]
        data = self.run_import(self.journal(records))["data"]
        self.assertEqual((data["buys"], data["sells"]), (2, 1))
        self.assertEqual(self.js("pnl", "--year", "2026")["data"]["sales"][0]["cost_basis"], "1550")  # 10@100 + 5@110, not 5@120

    def test_what_cannot_be_recorded_safely_is_listed_and_never_guessed(self):
        records = JOURNAL[:2] + [
            trade(20, "2026-03-05T15:30:00Z", "BUY", "AAPL  260717C00200000", 1, 5.0, asset="OPT", multiplier=100),
            trade(21, "2026-03-06T15:30:00Z", "SHORT", "TSLA", 5, 200.0),
            trade(22, "2026-03-07T15:30:00Z", "SELL", "AAPL", 1, 0.0),
            cash(23, "2026-03-08T00:00:00Z", "ADJUSTMENT", 9.0),
            cash(24, "2026-03-09T00:00:00Z", "DIVIDEND", -4.0, symbol="AAPL"),
        ]
        data = self.run_import(self.journal(records))["data"]
        self.assertEqual(data["imported"], 2)
        reasons = " | ".join(u["reason"] for u in data["unsupported"])
        for word in ("asset class OPT", "short selling", "zero price", "ADJUSTMENT", "reversal"):
            self.assertIn(word, reasons)
        self.assertEqual(data["unsupported_count"], 5)

    def test_selling_shares_the_ledger_does_not_hold_stops_everything_and_says_which_row(self):
        records = [trade(1, "2026-02-02T15:30:00Z", "BUY", "AAPL", 10, 100.0), trade(2, "2026-03-02T15:30:00Z", "SELL", "AAPL", 25, 130.0)]
        path = self.journal(records)
        refused = self.run_import(path, expect=1)
        self.assertEqual(refused["error"]["code"], "invalid_trades")
        self.assertIn("2026-05.jsonl:2", refused["error"]["message"])
        self.assertEqual(self.js("holdings")["data"]["positions"], [])  # not even the purchase was written
        preview = self.run_import(path, "--dry-run")["data"]
        self.assertEqual((len(preview["problems"]), preview["written"]), (1, False))
        self.assertEqual(preview["problems"][0]["code"], "not_enough_shares")
        # the usual fix: bring the missing shares in as an opening position first
        self.js("buy", "15", "AAPL", "--price", "90", "--ccy", "USD", "--account", "Assets:IBKR", "--date", "2026-01-10", "--opening")
        done = self.run_import(path)["data"]
        self.assertEqual((done["written"], done["sells"]), (True, 1))
        self.assertEqual(self.js("pnl")["data"]["sales"][0]["cost_basis"], "2350")  # 15@90 + 10@100

    def test_source_accounts_are_mapped_to_ledger_accounts(self):
        self.js("open", "Assets:Schwab", "--date", "2026-01-01")
        records = [trade(1, "2026-02-02T15:30:00Z", "BUY", "AAPL", 1, 100.0, account="ibkr-main"),
                   trade(2, "2026-02-03T15:30:00Z", "BUY", "MSFT", 1, 300.0, account="schwab-ira")]
        path = self.journal(records)
        refused = self.js("trades", "import", str(path), "--account-map", "ibkr-main=Assets:IBKR", "--dry-run")["data"]
        self.assertEqual(refused["problems"][0]["code"], "unresolved_account")
        self.js("trades", "import", str(path), "--account-map", "ibkr-main=Assets:IBKR", "--account-map", "schwab-ira=Assets:Schwab")
        held = {(p["account"], p["symbol"]) for p in self.js("holdings")["data"]["positions"]}
        self.assertEqual(held, {("Assets:IBKR", "AAPL"), ("Assets:Schwab", "MSFT")})

    def test_a_folder_of_monthly_journals_and_a_date_range(self):
        (self.files / "journal" / "2026").mkdir(parents=True)
        for month, recs in (("2026-02", [JOURNAL[0]]), ("2026-03", [JOURNAL[1]]), ("2026-04", [JOURNAL[2]])):
            (self.files / "journal" / "2026" / f"{month}.jsonl").write_text(json.dumps(recs[0]) + "\n", encoding="utf-8")
        data = self.run_import(self.files / "journal", "--until", "2026-03-31")["data"]
        self.assertEqual((data["buys"], data["sells"], data["out_of_range"]), (2, 0, 1))

    def test_an_agent_needs_a_confidence_that_clears_the_threshold(self):
        path = self.journal(JOURNAL[:1])
        self.assertEqual(self.run_import(path, "--actor", "agent:claude-code", expect=1)["error"]["code"], "confidence_required")
        low = self.run_import(path, "--actor", "agent:claude-code", "--confidence", "0.5", expect=1)["error"]
        self.assertEqual(low["code"], "confidence_too_low")
        self.assertIn("not held as pending", low["message"])  # a held sale would break the lots after it
        self.assertEqual(self.run_import(path, "--actor", "agent:claude-code", "--confidence", "0.95")["data"]["buys"], 1)

    def test_a_trade_before_the_account_existed_is_refused_with_its_row(self):
        path = self.journal([trade(1, "2025-12-01T15:30:00Z", "BUY", "AAPL", 1, 100.0)])
        refused = self.run_import(path, expect=1)
        self.assertEqual(refused["error"]["code"], "invalid_trades")
        self.assertIn("not open", refused["error"]["message"])


class Rows(Base):
    ROWS = [
        {"date": "2026-02-02", "type": "buy", "symbol": "0700.HK", "units": "100", "price": "300", "ccy": "HKD", "fee": "5", "id": "h1"},
        {"date": "2026-03-02", "type": "sell", "symbol": "0700.HK", "units": "40", "price": "350", "ccy": "HKD", "id": "h2"},
        {"date": "2026-03-05", "type": "dividend", "symbol": "0700.HK", "amount": "80", "ccy": "HKD", "id": "h3"},
    ]

    def setUp(self):
        super().setUp()
        self.rows = self.files / "rows.json"
        self.rows.write_text(json.dumps(self.ROWS), encoding="utf-8")

    def test_rows_an_agent_read_off_a_statement(self):
        data = self.js("trades", "import", str(self.rows), "--format", "json", "--account", "Assets:IBKR")["data"]
        self.assertEqual((data["buys"], data["sells"], data["dividends"]), (1, 1, 1))
        pos = self.js("holdings", "--in", "HKD")["data"]["positions"][0]
        self.assertEqual((pos["symbol"], pos["units"], pos["average_cost"]), ("0700.HK", "60", {"HKD": "300"}))
        self.assertEqual(self.js("pnl")["data"]["sales"][0]["realized_gain"], "2000")
        again = self.js("trades", "import", str(self.rows), "--format", "json", "--account", "Assets:IBKR")["data"]
        self.assertEqual((again["imported"], again["duplicates_skipped"]), (0, 3))

    def test_a_row_without_an_id_is_still_idempotent_by_its_content(self):
        rows = [{k: v for k, v in r.items() if k != "id"} for r in self.ROWS[:1]]
        self.rows.write_text(json.dumps(rows), encoding="utf-8")
        self.js("trades", "import", str(self.rows), "--format", "json", "--account", "Assets:IBKR")
        self.assertEqual(self.js("trades", "import", str(self.rows), "--format", "json", "--account", "Assets:IBKR")["data"]["imported"], 0)

    def test_bad_rows_are_refused_with_the_row_number(self):
        for bad in ([{"date": "2026-02-02", "type": "swap", "ccy": "USD"}], [{"date": "2026-02-02", "type": "buy", "symbol": "A", "units": "x", "price": "1", "ccy": "USD"}],
                    [{"date": "2026-02-02", "type": "buy", "symbol": "A", "units": "1", "price": "1", "ccy": "USD", "colour": "red"}], [], "text"):
            self.rows.write_text(json.dumps(bad), encoding="utf-8")
            out = self.js("trades", "import", str(self.rows), "--format", "json", "--account", "Assets:IBKR", expect=1)
            self.assertEqual(out["error"]["code"], "invalid_statement", str(bad))


if __name__ == "__main__":
    unittest.main()
