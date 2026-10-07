"""The hash chain: every event carries a hash of everything before it, so an edit, a removal, a reordering or a
hand-added line shows up in `money-mom verify`. It is tamper-EVIDENCE for a ledger folder that is only a file
system: someone who recomputes every later value can still rewrite history, and the tests say so."""

import json
import unittest

from money_mom import Ledger, LedgerError
from money_mom.state import CHAIN_GENESIS, chain_link

from helpers import HUMAN, LedgerTestCase
from test_cli import CliTestCase


class ChainBasics(LedgerTestCase):
    def setUp(self):
        super().setUp()
        self.open_accounts()
        self.fund()
        self.ledger.add_txn("2026-10-02", [("Assets:CMB", "-20", "CNY"), ("Expenses:Dining:Coffee", "20", "CNY")], payee="a")
        self.ledger.add_txn("2026-10-03", [("Assets:CMB", "-30", "CNY"), ("Expenses:Dining:Coffee", "30", "CNY")], payee="b")

    def lines(self):
        path = next(iter(sorted(self.ledger.ledger_dir.glob("*.jsonl"))))
        return path, path.read_text(encoding="utf-8").split("\n")[:-1]

    def rewrite(self, lines):
        path, _ = self.lines()
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def problems(self):
        return Ledger.open(self.root).verify_chain()

    def test_every_new_event_carries_a_chain_value_that_follows_from_the_previous_one(self):
        previous = CHAIN_GENESIS
        _, lines = self.lines()
        for line in lines:
            raw = json.loads(line)
            self.assertEqual(raw["chain"], chain_link(previous, raw))
            previous = raw["chain"]
        self.assertEqual(Ledger.open(self.root).state.chain_head, previous)
        self.assertEqual(self.problems(), [])

    def test_a_value_passed_in_by_the_caller_is_replaced(self):
        raw = self.ledger.new_event("open", actor=HUMAN, account="Assets:X", date="2026-10-01")
        raw["chain"] = "0" * 64
        self.ledger.append(raw)
        self.assertEqual(self.problems(), [])

    def test_editing_an_amount_is_found_at_that_event(self):
        _, lines = self.lines()
        index = next(i for i, l in enumerate(lines) if '"payee":"a"' in l)
        lines[index] = lines[index].replace('"-20"', '"-2"').replace('"20"', '"2"')
        self.rewrite(lines)
        found = self.problems()
        self.assertEqual([p.code for p in found][:1], ["chain_broken"])
        self.assertEqual(found[0].event_id, json.loads(lines[index])["id"])
        self.assertEqual(len(found), 1)  # reported once, where it happened

    def test_removing_an_event_is_found_at_the_next_one(self):
        _, lines = self.lines()
        index = next(i for i, l in enumerate(lines) if '"payee":"a"' in l)
        removed = lines.pop(index)
        self.rewrite(lines)
        found = self.problems()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].event_id, json.loads(lines[index])["id"])
        self.assertNotEqual(found[0].event_id, json.loads(removed)["id"])

    def test_reordering_is_refused_on_read(self):
        _, lines = self.lines()
        lines[-1], lines[-2] = lines[-2], lines[-1]
        self.rewrite(lines)
        with self.assertRaises(LedgerError) as ctx:  # the ledger already insists on ordered timestamps
            self.problems()
        self.assertEqual(ctx.exception.code, "ts_regression")

    def test_a_line_added_by_hand_without_a_chain_value_is_found(self):
        _, lines = self.lines()
        raw = json.loads(lines[-1])
        raw.pop("chain")
        raw["id"] = "handmade1"
        self.rewrite(lines + [json.dumps(raw, ensure_ascii=False, separators=(",", ":"))])
        found = self.problems()
        self.assertEqual([(p.code, p.event_id) for p in found], [("chain_missing", "handmade1")])

    def test_recomputing_the_edited_event_alone_is_still_caught_at_the_next_one(self):
        _, lines = self.lines()
        index = next(i for i, l in enumerate(lines) if '"payee":"a"' in l)
        previous = json.loads(lines[index - 1])["chain"]
        raw = json.loads(lines[index])
        raw["postings"][0]["amount"] = "-2"
        raw["postings"][1]["amount"] = "2"
        raw["chain"] = chain_link(previous, raw)
        lines[index] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        self.rewrite(lines)
        found = self.problems()
        self.assertEqual([p.event_id for p in found], [json.loads(lines[index + 1])["id"]])

    def test_the_honest_limit_recomputing_everything_after_the_edit_is_not_detected(self):
        # Without a copy of the head kept elsewhere, a rewrite that recomputes every later value is invisible.
        _, lines = self.lines()
        index = next(i for i, l in enumerate(lines) if '"payee":"a"' in l)
        previous = json.loads(lines[index - 1])["chain"]
        for i in range(index, len(lines)):
            raw = json.loads(lines[i])
            if i == index:
                raw["postings"][0]["amount"] = "-2"
                raw["postings"][1]["amount"] = "2"
            raw["chain"] = chain_link(previous, raw)
            previous = raw["chain"]
            lines[i] = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        self.rewrite(lines)
        self.assertEqual(self.problems(), [])
        self.assertNotEqual(Ledger.open(self.root).state.chain_head, "")  # which is why `verify` prints the head

    def test_key_order_and_spacing_do_not_matter_only_content(self):
        _, lines = self.lines()
        lines = [json.dumps(json.loads(l), ensure_ascii=False, indent=None, sort_keys=False, separators=(", ", ": ")) for l in lines]
        self.rewrite(lines)
        self.assertEqual(self.problems(), [])

    def test_a_ledger_written_before_chaining_is_protected_from_the_first_new_write_on(self):
        path, lines = self.lines()
        legacy = []
        for line in lines:
            raw = json.loads(line)
            raw.pop("chain")
            legacy.append(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
        self.rewrite(legacy)
        old = Ledger.open(self.root)
        self.assertEqual((old.state.chained_events, old.state.unchained_events, old.state.chain_problems), (0, len(legacy), []))
        old.add_txn("2026-10-04", [("Assets:CMB", "-5", "CNY"), ("Expenses:Dining:Coffee", "5", "CNY")])
        fresh = Ledger.open(self.root)
        self.assertEqual((fresh.state.chained_events, fresh.state.unchained_events, fresh.verify_chain()), (1, len(legacy), []))
        # editing an old, unchained event is caught by the first chained one, which covers the whole history
        text = path.read_text(encoding="utf-8").split("\n")
        index = next(i for i, l in enumerate(text) if '"payee":"a"' in l)
        text[index] = text[index].replace('"-20"', '"-21"').replace('"20"', '"21"')
        path.write_text("\n".join(text), encoding="utf-8")
        found = Ledger.open(self.root).verify_chain()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "chain_broken")


class ChainCli(CliTestCase):
    def setUp(self):
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.js("transfer", "100", "--from", "期初", "--to", "银行卡", "--date", "2026-01-02")
        self.js("spend", "10", "--from", "银行卡", "--category", "咖啡", "--date", "2026-01-03")

    def month_file(self):
        return next((self.root / "ledger").glob("*.jsonl"))

    def test_verify_reports_a_clean_ledger_and_its_head(self):
        data = self.js("verify")["data"]
        self.assertTrue(data["ok"])
        self.assertEqual(data["chained"], data["events"])
        self.assertEqual(len(data["head"]), 64)
        out, _ = self.run_cli("--ledger", str(self.root), "verify")
        self.assertIn("OK:", out)
        self.assertIn("head ", out)

    def test_verify_check_and_doctor_all_fail_on_an_edit(self):
        path = self.month_file()
        path.write_text(path.read_text(encoding="utf-8").replace("2026-01-03", "2026-01-04", 1), encoding="utf-8")
        bad = self.js("verify", expect=1)["data"]
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["problems"][0]["code"], "chain_broken")
        self.assertFalse(self.js("check", expect=1)["data"]["ok"])
        doctor = self.js("doctor", expect=1)["data"]
        self.assertFalse(next(c for c in doctor["checks"] if c["name"] == "chain")["ok"])
        out, _ = self.run_cli("--ledger", str(self.root), "verify", expect=1)
        self.assertIn("TAMPERING OR EDITS FOUND", out)

    def test_a_healthy_ledger_passes_doctor_and_check(self):
        self.assertTrue(self.js("check")["data"]["ok"])
        chain = next(c for c in self.js("doctor")["data"]["checks"] if c["name"] == "chain")
        self.assertTrue(chain["ok"])


if __name__ == "__main__":
    unittest.main()
