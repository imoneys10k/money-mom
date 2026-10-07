import datetime as dt
import json
import threading
import unittest
from decimal import Decimal

from money_mom import Ledger, LedgerError, RuleError, ValidationError

from helpers import AGENT, HUMAN, TZ, LedgerTestCase, StepClock


class InitAndOpen(LedgerTestCase):
    def test_layout_and_config(self):
        self.assertTrue((self.root / "money-mom.toml").is_file())
        self.assertTrue((self.root / "ledger").is_dir())
        self.assertIn("cache.sqlite", (self.root / ".gitignore").read_text())
        self.assertEqual(self.ledger.config["base_currency"], "CNY")
        self.assertEqual(self.ledger.config["tone"], "normal")

    def test_init_twice_and_open_missing(self):
        with self.assertRaises(LedgerError) as ctx:
            Ledger.init(self.root)
        self.assertEqual(ctx.exception.code, "already_initialized")
        with self.assertRaises(LedgerError) as ctx:
            Ledger.open(self.root.parent / "nowhere")
        self.assertEqual(ctx.exception.code, "not_a_ledger")

    def test_bad_config_values(self):
        with self.assertRaises(LedgerError):
            Ledger.init(self.root.parent / "a", base_currency="cny")
        with self.assertRaises(LedgerError):
            Ledger.init(self.root.parent / "b", tone="angry")
        (self.root / "money-mom.toml").write_text("schema = 99\nbase_currency = \"CNY\"\n")
        with self.assertRaises(LedgerError) as ctx:
            Ledger.open(self.root)
        self.assertEqual(ctx.exception.code, "unsupported_version")

    def test_new_ledger_has_no_events(self):
        self.assertEqual(self.total_lines(), 0)
        self.assertEqual(self.ledger.check(), [])


class Persistence(LedgerTestCase):
    def test_reopen_gives_identical_state(self):
        self.open_accounts()
        self.fund("Assets:CMB", "1234.56")
        self.ledger.add_txn(
            "2026-10-07", [(None, "5", "CNY"), ("Assets:CMB", "-5", "CNY")], status="pending", actor=AGENT
        )
        reopened = Ledger.open(self.root)
        self.assertEqual(reopened.state.balances(), self.ledger.state.balances())
        self.assertEqual(len(reopened.state.pending()), 1)
        self.assertEqual(reopened.state.balance_of("Assets:CMB")["CNY"], Decimal("1234.56"))
        self.assertEqual([e.id for e in reopened.state.events], [e.id for e in self.ledger.state.events])

    def test_chinese_text_is_stored_readably(self):
        self.open_accounts()
        text = "".join(self.event_files().values())
        self.assertIn("Assets:Receivable:小王", text)
        self.assertNotIn("\\u", text)

    def test_one_event_per_line_compact_json(self):
        self.open_accounts()
        for line in "".join(self.event_files().values()).splitlines():
            self.assertEqual(json.dumps(json.loads(line), ensure_ascii=False, separators=(",", ":")), line)

    def test_files_are_chosen_by_utc_month_of_ts(self):
        cases = [
            (dt.datetime(2026, 10, 31, 23, 30, tzinfo=TZ), "2026-10.jsonl"),
            (dt.datetime(2026, 11, 1, 1, 0, tzinfo=TZ), "2026-10.jsonl"),  # still Oct 31 in UTC
            (dt.datetime(2026, 11, 1, 9, 0, tzinfo=TZ), "2026-11.jsonl"),
        ]
        ledger = Ledger.init(self.root.parent / "months", clock=StepClock(cases[0][0] - dt.timedelta(seconds=1)))
        for stamp, _ in cases:
            ledger._clock = lambda s=stamp: s
            ledger.open_account(f"Assets:A{stamp.hour}{stamp.day}", "2026-10-01")
        lines = {
            p.name: len(p.read_text(encoding="utf-8").splitlines())
            for p in ledger.ledger_dir.glob("*.jsonl")
        }
        self.assertEqual(lines, {"2026-10.jsonl": 2, "2026-11.jsonl": 1})
        self.assertEqual(Ledger.open(ledger.root).state.accounts.keys(), ledger.state.accounts.keys())


class AllOrNothing(LedgerTestCase):
    def test_a_bad_event_in_a_batch_writes_nothing(self):
        self.open_accounts()
        before = self.event_files()
        good = self.ledger.new_event(
            "txn", actor=HUMAN, date="2026-10-07", status="posted",
            postings=self.ledger._postings([("Expenses:Gift", "5", "CNY"), ("Assets:CMB", "-5", "CNY")]),
        )
        bad = self.ledger.new_event(
            "txn", actor=HUMAN, date="2026-10-07", status="posted",
            postings=self.ledger._postings([("Expenses:Gift", "5", "CNY"), ("Assets:CMB", "-4", "CNY")]),
        )
        with self.assertRaises(RuleError) as ctx:
            self.ledger.append_many([good, bad])
        self.assertEqual(ctx.exception.code, "unbalanced")
        self.assertIn("event #2", ctx.exception.where)
        self.assertEqual(self.event_files(), before)
        self.assertNotIn(good["id"], self.ledger.state.txns)

    def test_later_events_in_a_batch_see_earlier_ones(self):
        self.open_accounts()
        self.fund("Assets:CMB", "100.00")
        first = self.ledger.new_event(
            "txn", actor=HUMAN, date="2026-10-02", status="posted",
            postings=self.ledger._postings([("Expenses:Gift", "5", "CNY"), ("Assets:CMB", "-5", "CNY")]),
        )
        check = self.ledger.new_event(
            "assert", actor=HUMAN, date="2026-10-02", account="Assets:CMB", amount="95.00", ccy="CNY"
        )
        self.ledger.append_many([first, check])
        self.assertEqual(len(self.ledger.state.assertions), 1)

    def test_empty_batch_is_a_noop(self):
        self.assertEqual(self.ledger.append_many([]), [])


class Concurrency(LedgerTestCase):
    def test_a_stale_writer_validates_against_the_latest_disk_state(self):
        other = Ledger.open(self.root, clock=self.clock)
        other.open_account("Assets:CMB", "2026-10-01")
        other.open_account("Expenses:Gift", "2026-10-01")
        # `self.ledger` has not seen those accounts, but must not reject them as unknown.
        self.ledger.add_txn("2026-10-02", [("Expenses:Gift", "1", "CNY"), ("Assets:CMB", "-1", "CNY")])
        self.assertEqual(self.ledger.state.balance_of("Assets:CMB")["CNY"], Decimal("-1"))

    def test_a_writer_blocks_while_another_holds_the_lock(self):
        from money_mom.ledger import _exclusive_lock

        self.open_accounts()
        finished = threading.Event()

        def writer():
            led = Ledger.open(self.root, clock=StepClock(dt.datetime(2026, 10, 9, tzinfo=TZ)))
            led.add_txn("2026-10-09", [("Expenses:Gift", "1", "CNY"), ("Assets:CMB", "-1", "CNY")])
            finished.set()

        with _exclusive_lock(self.ledger.ledger_dir / ".lock"):
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertFalse(finished.wait(0.5), "writer got through while the lock was held")
        self.assertTrue(finished.wait(10), "writer never resumed after the lock was released")
        thread.join()

    def test_parallel_writers_do_not_lose_or_corrupt_events(self):
        self.open_accounts()
        errors = []

        def work(n):
            try:
                led = Ledger.open(self.root, clock=StepClock(dt.datetime(2026, 10, 8, 0, 0, tzinfo=TZ)))
                for i in range(5):
                    led.add_txn("2026-10-08", [("Expenses:Gift", "1", "CNY"), ("Assets:CMB", "-1", "CNY")],
                                narration=f"w{n}-{i}")
            except Exception as err:  # noqa: BLE001
                errors.append(err)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        final = Ledger.open(self.root)
        self.assertEqual(final.state.balance_of("Expenses:Gift")["CNY"], Decimal("20"))
        self.assertEqual(len(final.state.txns), 20)


class CorruptionIsReportedNotHidden(LedgerTestCase):
    def month_file(self):
        self.open_accounts()
        return self.ledger.ledger_dir / "2026-10.jsonl"

    def reopen_error(self):
        with self.assertRaises(LedgerError) as ctx:
            Ledger.open(self.root)
        return ctx.exception

    def test_bad_json_names_the_line(self):
        path = self.month_file()
        path.write_text(path.read_text(encoding="utf-8") + "{not json}\n", encoding="utf-8")
        err = self.reopen_error()
        self.assertEqual(err.code, "invalid_json")
        self.assertEqual(err.where, "2026-10.jsonl:7")

    def test_truncated_last_write(self):
        path = self.month_file()
        path.write_text(path.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
        self.assertEqual(self.reopen_error().code, "corrupt_tail")

    def test_duplicate_keys_are_rejected(self):
        path = self.month_file()
        first, *rest = path.read_text(encoding="utf-8").splitlines()
        bad = first.replace('"kind":"open"', '"kind":"open","kind":"close"')
        path.write_text("\n".join([bad, *rest]) + "\n", encoding="utf-8")
        self.assertEqual(self.reopen_error().code, "duplicate_key")

    def test_nan_is_rejected(self):
        path = self.month_file()
        path.write_text(path.read_text(encoding="utf-8") + '{"v":1,"confidence":NaN}\n', encoding="utf-8")
        self.assertEqual(self.reopen_error().code, "invalid_event")

    def test_rule_violations_on_disk_are_reported_with_a_location(self):
        path = self.month_file()
        self.fund()
        lines = path.read_text(encoding="utf-8").splitlines()
        del lines[1]  # drop the `open` of Assets:CMB, which the funding txn uses
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        err = self.reopen_error()
        self.assertEqual(err.code, "unknown_account")
        self.assertEqual(err.where, "2026-10.jsonl:6")

    def test_event_in_the_wrong_month_file(self):
        path = self.month_file()
        text = path.read_text(encoding="utf-8")
        path.unlink()
        (self.ledger.ledger_dir / "2026-09.jsonl").write_text(text, encoding="utf-8")
        self.assertEqual(self.reopen_error().code, "misplaced_event")

    def test_stray_files_are_ignored(self):
        self.month_file()
        (self.ledger.ledger_dir / "notes.jsonl").write_text("garbage\n")
        (self.ledger.ledger_dir / ".lock.bak").write_text("x")
        Ledger.open(self.root)

    def test_refuses_to_append_after_a_truncated_tail(self):
        path = self.month_file()
        path.write_text(path.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
        with self.assertRaises(LedgerError) as ctx:
            self.ledger.open_account("Assets:New", "2026-10-01")
        self.assertEqual(ctx.exception.code, "corrupt_tail")


if __name__ == "__main__":
    unittest.main()
