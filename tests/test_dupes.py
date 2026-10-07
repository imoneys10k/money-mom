"""Duplicate detection across sources: what counts as a candidate, how a statement row is held, how the user's
answer is recorded, and what must never be asked twice."""

import datetime as dt
import json
import unittest
from decimal import Decimal

from money_mom import LedgerError
from money_mom.dupes import Item, compare, find_candidates, suggest_keep
from money_mom.templates import apply_template

from helpers import HUMAN, LedgerTestCase
from test_cli import CliTestCase

D = Decimal


def item(**kw):
    base = dict(
        id="a", date=dt.date(2026, 9, 3), account="Assets:微信", amount=D("-18"), ccy="CNY", payee="", narration="",
        origin=("entry", "a"), via="", status="posted", held=False, has_hash=False, source="manual",
    )
    base.update(kw)
    return Item(**base)


def stmt(**kw):
    return item(origin=("import", kw.pop("sha", "file1")), has_hash=True, source="import:x.csv", **kw)


class Compare(unittest.TestCase):
    def test_same_payee_next_day_in_a_different_account_is_likely(self):
        c = compare(item(id="a", account="Assets:银行卡", payee="瑞幸咖啡"),
                    stmt(id="b", date=dt.date(2026, 9, 4), payee="财付通-瑞幸咖啡"), {})
        self.assertEqual(c.tier, "likely")
        self.assertIn("same_payee_or_description", c.evidence)
        self.assertIn("different_accounts", c.evidence)

    def test_equal_amounts_alone_are_only_possible(self):
        c = compare(item(id="a", account="Assets:银行卡"), stmt(id="b", payee="某店"), {})
        self.assertEqual((c.tier, c.days_apart), ("possible", 0))

    def test_the_window_limits_how_far_apart_they_may_be(self):
        far = stmt(id="b", date=dt.date(2026, 9, 6))
        self.assertIsNone(compare(item(id="a", account="Assets:银行卡"), far, {}))
        near = stmt(id="b", date=dt.date(2026, 9, 5))
        self.assertEqual(compare(item(id="a", account="Assets:银行卡"), near, {}).tier, "possible")
        self.assertIsNone(compare(item(id="a", account="Assets:银行卡"), near, {}, window=1))

    def test_amount_currency_and_sign_must_all_match(self):
        a = item(id="a", account="Assets:银行卡")
        for other in (stmt(id="b", amount=D("-18.01")), stmt(id="b", ccy="USD"), stmt(id="b", amount=D("18"))):
            self.assertIsNone(compare(a, other, {}))

    def test_rows_of_one_statement_never_duplicate_each_other(self):
        self.assertIsNone(compare(stmt(id="a", payee="地铁"), stmt(id="b", payee="地铁"), {}))
        self.assertIsNotNone(compare(stmt(id="a", sha="f1"), stmt(id="b", sha="f2", account="Assets:银行卡"), {}))

    def test_two_hand_entries_are_suspicious_only_on_the_same_day_in_the_same_account(self):
        self.assertIsNotNone(compare(item(id="a"), item(id="b", origin=("entry", "b")), {}))
        self.assertIsNone(compare(item(id="a"), item(id="b", origin=("entry", "b"), date=dt.date(2026, 9, 4)), {}))
        self.assertIsNone(compare(item(id="a"), item(id="b", origin=("entry", "b"), account="Assets:银行卡"), {}))

    def test_a_statement_row_and_a_hand_entry_of_the_same_day_and_account_are_likely(self):
        c = compare(item(id="a"), stmt(id="b"), {})
        self.assertEqual(c.tier, "likely")
        self.assertIn("statement_row_and_hand_entry", c.evidence)

    def test_the_payment_method_on_a_row_can_point_at_the_other_account(self):
        aliases = {"招行卡1234": "Assets:银行卡"}
        row = stmt(id="b", via="招商银行信用卡(1234)")
        c = compare(item(id="a", account="Assets:银行卡"), row, aliases)
        self.assertEqual(c.tier, "likely")
        self.assertIn("payment_method_points_at_the_other_account", c.evidence)
        # a different card number does not link
        other = compare(item(id="a", account="Assets:银行卡"), stmt(id="b", via="招商银行信用卡(9999)"), aliases)
        self.assertEqual(other.tier, "possible")

    def test_a_generic_word_in_the_account_name_is_not_a_link(self):
        c = compare(item(id="a", account="Assets:银行:储蓄卡"), stmt(id="b", via="某银行储蓄卡(1111)"), {})
        self.assertEqual(c.tier, "possible")

    def test_suggestion_prefers_the_one_not_held_then_the_statement_row(self):
        self.assertEqual(suggest_keep(item(id="a", held=True), item(id="b")), "b")
        self.assertEqual(suggest_keep(item(id="a"), stmt(id="b")), "b")
        self.assertEqual(suggest_keep(item(id="a"), item(id="b", date=dt.date(2026, 9, 4))), "a")


class Detect(LedgerTestCase):
    def setUp(self):
        super().setUp()
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)
        self.ledger.add_txn("2026-01-02", [("Assets:微信", "1000", "CNY"), ("Equity:期初余额", "-1000", "CNY")])
        self.ledger.add_txn("2026-01-02", [("Assets:银行卡", "1000", "CNY"), ("Equity:期初余额", "-1000", "CNY")])

    def spend(self, account, amount="18", date="2026-09-03", payee="瑞幸咖啡", **extra):
        return self.ledger.add_txn(
            date, [(account, f"-{amount}", "CNY"), ("Expenses:餐饮:咖啡", amount, "CNY")], payee=payee, **extra
        )

    def test_transfers_and_voided_entries_are_not_compared(self):
        self.ledger.add_txn("2026-09-03", [("Assets:微信", "-18", "CNY"), ("Assets:银行卡", "18", "CNY")])
        self.spend("Assets:银行卡", "18")
        self.assertEqual(find_candidates(self.ledger.state, {}), [])
        a, b = self.spend("Assets:微信", "7"), self.spend("Assets:微信", "7")
        self.assertEqual(len(find_candidates(self.ledger.state, {})), 1)
        self.ledger.void(b.id, "mistake")
        self.assertEqual(find_candidates(self.ledger.state, {}), [])

    def test_a_judged_pair_is_not_listed_again_and_voiding_the_review_brings_it_back(self):
        a, b = self.spend("Assets:微信", "7"), self.spend("Assets:微信", "7")
        review = self.ledger.append(self.ledger.new_event("review", actor=HUMAN, targets=[a.id, b.id], verdict="different"))
        self.assertEqual(find_candidates(self.ledger.state, {}), [])
        self.ledger.void(review.id, "changed my mind")
        self.assertEqual(len(find_candidates(self.ledger.state, {})), 1)

    def test_review_events_are_validated(self):
        a, b = self.spend("Assets:微信", "7"), self.spend("Assets:微信", "7")
        bad = [
            dict(targets=[a.id, a.id], verdict="different"), dict(targets=[a.id], verdict="different"),
            dict(targets=[a.id, b.id], verdict="maybe"), dict(targets=[a.id, b.id], verdict="same"),
            dict(targets=[a.id, b.id], verdict="same", keep="zzz"), dict(targets=[a.id, b.id], verdict="different", keep=a.id),
            dict(targets=[a.id, "nope"], verdict="different"),
        ]
        for fields in bad:
            with self.assertRaises(LedgerError, msg=str(fields)):
                self.ledger.append(self.ledger.new_event("review", actor=HUMAN, **fields))
        self.assertEqual(self.ledger.state.reviews, {})


WX_MAP = """
ccy = "CNY"
[columns]
date = "日期"
amount = "金额"
payee = "对方"
description = "说明"
id = "单号"
via = "支付方式"
[format]
date = "%Y-%m-%d"
sign = "inflow_positive"
[[rules]]
match = "瑞幸"
account = "咖啡"
[[rules]]
match = "滴滴"
account = "打车"
"""
WX_CSV = """日期,金额,对方,说明,单号,支付方式
2026-09-03,-18.00,瑞幸咖啡,拿铁,W1,零钱
2026-09-04,-43.50,滴滴出行,打车,W2,零钱
2026-09-05,-9.90,某小店,零食,W3,零钱
"""


class Flow(CliTestCase):
    def setUp(self):
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.js("transfer", "1000", "--from", "期初", "--to", "微信", "--date", "2026-01-02")
        self.js("transfer", "1000", "--from", "期初", "--to", "银行卡", "--date", "2026-01-02")
        self.files = self.dir / "files"
        self.files.mkdir()
        (self.files / "wx.toml").write_text(WX_MAP, encoding="utf-8")
        (self.files / "wx.csv").write_text(WX_CSV, encoding="utf-8")
        self.js("import", "save-map", "wx", str(self.files / "wx.toml"))

    def run_import(self, **kw):
        return self.js("import", "run", str(self.files / "wx.csv"), "--map", "wx", "--account", "微信")["data"]

    def wallet(self):
        out, _ = self.run_cli("--ledger", str(self.root), "balance", "--account", "Assets:微信")
        return out

    def spend_by_hand(self):
        return self.js("spend", "18", "--from", "微信", "--category", "咖啡", "--payee", "瑞幸咖啡", "--date", "2026-09-03")["data"]

    def test_a_row_that_matches_a_hand_entry_is_held_and_not_counted(self):
        self.spend_by_hand()
        data = self.run_import()
        self.assertEqual((data["rows_read"], data["held_for_duplicates"], data["posted"], data["pending"]), (3, 1, 1, 2))
        row = data["possible_duplicates"][0]
        self.assertEqual((row["tier"], row["amount"], row["payee"]), ("likely", "-18.00", "瑞幸咖啡"))
        # only the hand entry and the one booked row count: 1000 - 18 - 43.5
        self.assertIn("938.50", self.wallet())
        pending = self.js("pending")["data"]
        self.assertEqual(sorted(p["held_for_duplicate_review"] for p in pending), [False, True])  # the held row and the row with no rule

    def test_importing_without_a_match_holds_nothing(self):
        data = self.run_import()
        self.assertEqual((data["held_for_duplicates"], data["posted"], data["pending"]), (0, 2, 1))
        self.assertEqual(self.js("dupes")["data"]["count"], 0)

    def test_same_payment_voids_the_held_row_and_a_second_import_does_not_bring_it_back(self):
        hand = self.spend_by_hand()
        self.run_import()
        cand = self.js("dupes")["data"]["candidates"][0]
        held_id = next(i for i in cand["ids"] if i != hand["id"]) if "id" in hand else None
        self.assertEqual(cand["suggest_keep"], hand["id"])  # the entry that is not held
        done = self.js("dupes", "resolve", *cand["ids"], "--same", "--keep", cand["suggest_keep"])["data"]
        self.assertEqual((done["kept"], done["voided"] == held_id), (hand["id"], True))
        self.assertEqual(self.js("dupes")["data"]["count"], 0)
        shown = self.js("show", held_id)["data"]
        self.assertEqual(json.dumps(shown, ensure_ascii=False).count("duplicate of"), 1)
        again = self.run_import()
        self.assertEqual((again["imported"], again["held_for_duplicates"], again["duplicates_skipped"]), (0, 0, 3))

    def test_different_payments_release_the_held_row_into_the_books_and_are_not_asked_again(self):
        hand = self.spend_by_hand()
        self.run_import()
        cand = self.js("dupes")["data"]["candidates"][0]
        done = self.js("dupes", "resolve", *cand["ids"], "--different")["data"]
        self.assertEqual(len(done["released"]), 1)
        self.assertIn("920.50", self.wallet())  # both 18 payments now count
        self.assertEqual(self.js("dupes")["data"]["count"], 0)
        self.assertEqual(self.run_import()["held_for_duplicates"], 0)

    def test_a_held_row_with_two_candidates_waits_until_both_are_judged(self):
        a = self.spend_by_hand()
        b = self.js("spend", "18", "--from", "微信", "--category", "咖啡", "--payee", "瑞幸咖啡", "--date", "2026-09-03")["data"]
        self.assertIn("duplicate_candidates", b)  # the second hand entry already looks like the first
        self.js("dupes", "resolve", a["id"], b["id"], "--different")
        self.run_import()
        pairs = self.js("dupes")["data"]["candidates"]
        self.assertEqual(len(pairs), 2)
        first = self.js("dupes", "resolve", *pairs[0]["ids"], "--different")["data"]
        self.assertEqual(first["released"], [])
        self.assertEqual(len(first["still_asking"]), 1)
        second = self.js("dupes", "resolve", *pairs[1]["ids"], "--different")["data"]
        self.assertEqual(len(second["released"]), 1)

    def test_an_agent_must_pass_what_the_user_said(self):
        self.spend_by_hand()
        self.run_import()
        ids = self.js("dupes")["data"]["candidates"][0]["ids"]
        denied = self.js("dupes", "resolve", *ids, "--different", "--actor", "agent:claude-code", expect=1)
        self.assertEqual(denied["error"]["code"], "user_decision_required")
        self.js("dupes", "resolve", *ids, "--different", "--actor", "agent:claude-code", "--user-said", "不是同一笔，我喝了两杯")
        show = json.dumps(self.js("query", "SELECT id, kind FROM events WHERE kind = 'review'")["data"], ensure_ascii=False)
        self.assertIn("review", show)
        raw = (self.root / "ledger").glob("*.jsonl")
        text = "".join(p.read_text(encoding="utf-8") for p in raw)
        self.assertIn("不是同一笔，我喝了两杯", text)

    def test_resolve_needs_a_keep_for_same_and_refuses_unknown_or_voided_ids(self):
        hand = self.spend_by_hand()
        self.run_import()
        ids = self.js("dupes")["data"]["candidates"][0]["ids"]
        self.assertEqual(self.js("dupes", "resolve", *ids, "--same", expect=1)["error"]["code"], "invalid_review")
        self.assertEqual(self.js("dupes", "resolve", *ids, "--same", "--keep", "zzz", expect=1)["error"]["code"], "invalid_review")
        self.assertEqual(self.js("dupes", "resolve", ids[0], "nope", "--different", expect=1)["error"]["code"], "unknown_target")
        self.js("void", ids[0], "--reason", "x")
        self.assertEqual(self.js("dupes", "resolve", *ids, "--different", expect=1)["error"]["code"], "already_voided")
        self.assertTrue(hand)

    def test_recheck_does_not_release_a_held_row(self):
        self.spend_by_hand()
        (self.files / "wx.csv").write_text(WX_CSV.replace("瑞幸咖啡", "某咖啡店"), encoding="utf-8")
        self.js("spend", "18", "--from", "微信", "--category", "咖啡", "--payee", "某咖啡店", "--date", "2026-09-03")
        data = self.run_import()
        self.assertEqual(data["held_for_duplicates"], 1)
        self.js("import", "rule-add", "--map", "wx", "--match", "某咖啡", "--account", "咖啡")
        done = self.js("import", "recheck", "--map", "wx")["data"]
        self.assertEqual(done["resolved"], 0)
        self.assertEqual(len([p for p in self.js("pending")["data"] if p["held_for_duplicate_review"]]), 1)

    def test_an_entry_that_repeats_a_statement_row_is_flagged_when_it_is_written(self):
        self.run_import()
        data = self.js("spend", "18", "--from", "微信", "--category", "咖啡", "--payee", "瑞幸咖啡", "--date", "2026-09-03")["data"]
        self.assertEqual(data["duplicate_candidates"][0]["tier"], "likely")
        out, _ = self.run_cli("--ledger", str(self.root), "spend", "9.90", "--from", "微信", "--category", "零食", "--date", "2026-09-05")
        self.assertIn("This may duplicate", out)
        self.assertIn("never decide for them", out)

    def test_the_payment_method_links_a_wallet_row_to_the_card_that_paid(self):
        self.js("alias", "add", "招行卡1234", "Assets:银行卡")
        self.js("spend", "18", "--from", "银行卡", "--category", "咖啡", "--payee", "瑞幸", "--date", "2026-09-03")
        (self.files / "wx.csv").write_text(WX_CSV.replace("W1,零钱", "W1,招商银行信用卡(1234)"), encoding="utf-8")
        data = self.run_import()
        row = data["possible_duplicates"][0]
        self.assertIn("payment_method_points_at_the_other_account", row["evidence"])
        self.assertEqual(row["matches"][0]["account"], "Assets:银行卡")
        self.assertEqual(row["matches"][0]["payment_method"], None)

    def test_the_text_output_tells_the_agent_what_to_do(self):
        self.spend_by_hand()
        out, _ = self.run_cli("--ledger", str(self.root), "import", "run", str(self.files / "wx.csv"), "--map", "wx", "--account", "微信")
        self.assertIn("held as pending", out)
        listing, _ = self.run_cli("--ledger", str(self.root), "dupes")
        self.assertIn("[likely]", listing)
        self.assertIn("dupes resolve", listing)
        self.assertIn("never decide for them", listing)
        empty = self.dir / "empty"
        self.run_cli("--ledger", str(empty), "init")
        text, _ = self.run_cli("--ledger", str(empty), "dupes")
        self.assertIn("No duplicate candidates", text)


if __name__ == "__main__":
    unittest.main()
