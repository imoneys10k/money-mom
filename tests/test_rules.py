import datetime as dt
from decimal import Decimal

from money_mom import RuleError

from helpers import AGENT, HUMAN, LedgerTestCase

D = Decimal


class RuleTestCase(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.open_accounts()

    def assertRule(self, code, func, *args, **kwargs):
        with self.assertRaises(RuleError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))
        return ctx.exception

    def coffee(self, amount="38.00", date="2026-10-07", **kw):
        return self.ledger.add_txn(
            date,
            [("Expenses:Dining:Coffee", amount, "CNY"), ("Assets:CMB", f"-{amount}", "CNY")],
            **kw,
        )

    def bal(self, account, ccy="CNY", as_of=None):
        return self.ledger.state.balance_of(account, as_of).get(ccy, D(0))


class Balance(RuleTestCase):
    def test_balanced_transaction_updates_balances(self):
        self.coffee("38.00")
        self.assertEqual(self.bal("Expenses:Dining:Coffee"), D("38.00"))
        self.assertEqual(self.bal("Assets:CMB"), D("-38.00"))

    def test_unbalanced_is_rejected_and_nothing_is_written(self):
        before = self.total_lines()
        err = self.assertRule(
            "unbalanced", self.ledger.add_txn, "2026-10-07",
            [("Expenses:Dining:Coffee", "38.00", "CNY"), ("Assets:CMB", "-37.99", "CNY")],
        )
        self.assertEqual(err.details["off_by"], {"CNY": "0.01"})
        self.assertEqual(self.total_lines(), before)
        self.assertEqual(self.ledger.state.balances(), {})

    def test_decimal_precision_is_exact(self):
        self.ledger.add_txn(
            "2026-10-07",
            [("Expenses:Gift", "0.1", "CNY"), ("Expenses:Gift", "0.2", "CNY"), ("Assets:CMB", "-0.3", "CNY")],
        )
        self.assertEqual(self.bal("Assets:CMB"), D("-0.3"))

    def test_each_currency_must_balance_on_its_own(self):
        self.assertRule(
            "unbalanced", self.ledger.add_txn, "2026-10-07",
            [("Expenses:Gift", "10", "CNY"), ("Assets:CMB", "-10", "USD")],
        )
        self.ledger.add_txn(
            "2026-10-07",
            [("Expenses:Gift", "10", "CNY"), ("Assets:CMB", "-10", "CNY"),
             ("Expenses:Gift", "5", "USD"), ("Assets:CMB", "-5", "USD")],
        )

    def test_float_amounts_are_refused_before_any_event_is_built(self):
        with self.assertRaises(TypeError):
            self.ledger.add_txn("2026-10-07", [("Expenses:Gift", 0.1, "CNY"), ("Assets:CMB", -0.1, "CNY")])


class Accounts(RuleTestCase):
    def test_unknown_account(self):
        self.assertRule(
            "unknown_account", self.ledger.add_txn, "2026-10-07",
            [("Expenses:Nope", "1", "CNY"), ("Assets:CMB", "-1", "CNY")],
        )

    def test_date_must_not_precede_open(self):
        self.assertRule("date_before_open", self.coffee, date="2026-09-30")

    def test_currency_constraint(self):
        self.assertRule(
            "currency_not_allowed", self.ledger.add_txn, "2026-10-07",
            [("Assets:Alipay", "1", "USD"), ("Assets:CMB", "-1", "USD")],
        )

    def test_cannot_reopen(self):
        self.assertRule("account_exists", self.ledger.open_account, "Assets:CMB", "2026-10-02")

    def test_close_requires_zero_balance_then_blocks_later_postings(self):
        self.coffee("10.00")
        self.assertRule("account_not_empty", self.ledger.close_account, "Assets:CMB", "2026-10-08")
        self.fund("Assets:CMB", "10.00", "2026-10-07")
        self.ledger.close_account("Assets:CMB", "2026-10-08")
        self.assertRule("account_closed", self.coffee, date="2026-10-09")

    def test_close_rejects_postings_after_the_close_date(self):
        self.ledger.add_txn("2026-10-09", [("Expenses:Gift", "1", "CNY"), ("Assets:Receivable:小王", "-1", "CNY")])
        self.ledger.add_txn("2026-10-09", [("Assets:Receivable:小王", "1", "CNY"), ("Expenses:Gift", "-1", "CNY")])
        self.assertRule("postings_after_close", self.ledger.close_account, "Assets:Receivable:小王", "2026-10-08")


class PendingAndConfirm(RuleTestCase):
    def pending(self):
        return self.ledger.add_txn(
            "2026-10-07",
            [(None, "200.00", "CNY"), ("Assets:CMB", "-200.00", "CNY")],
            status="pending", payee="小王", actor=AGENT, confidence=0.55,
        )

    def test_pending_with_unknown_account_is_kept_out_of_balances(self):
        self.pending()
        self.assertEqual(self.ledger.state.balances(), {})
        self.assertEqual(len(self.ledger.state.pending()), 1)

    def test_pending_does_not_need_to_balance_but_posted_cannot_have_null(self):
        self.assertRule(
            "unresolved_account", self.ledger.add_txn, "2026-10-07",
            [(None, "200.00", "CNY"), ("Assets:CMB", "-200.00", "CNY")],
        )

    def test_pending_accounts_that_are_named_must_exist(self):
        self.assertRule(
            "unknown_account", self.ledger.add_txn, "2026-10-07",
            [("Expenses:Nope", "1", "CNY"), ("Assets:CMB", "-1", "CNY")], status="pending",
        )

    def test_confirm_resolves_and_posts(self):
        ev = self.pending()
        self.ledger.confirm(ev.id, [("Assets:Receivable:小王", "200.00", "CNY"), ("Assets:CMB", "-200.00", "CNY")])
        self.assertEqual(self.bal("Assets:Receivable:小王"), D("200.00"))
        self.assertEqual(self.ledger.state.pending(), [])

    def test_confirm_without_postings_still_needs_a_resolved_account(self):
        ev = self.pending()
        self.assertRule("unresolved_account", self.ledger.confirm, ev.id)

    def test_bad_confirm_leaves_it_pending(self):
        ev = self.pending()
        self.assertRule(
            "unbalanced", self.ledger.confirm, ev.id,
            [("Assets:Receivable:小王", "200.00", "CNY"), ("Assets:CMB", "-199.00", "CNY")],
        )
        self.assertEqual(self.ledger.state.txns[ev.id].status, "pending")

    def test_cannot_confirm_twice_or_confirm_a_posted_txn(self):
        ev = self.pending()
        good = [("Assets:Receivable:小王", "200.00", "CNY"), ("Assets:CMB", "-200.00", "CNY")]
        self.ledger.confirm(ev.id, good)
        self.assertRule("not_pending", self.ledger.confirm, ev.id, good)
        posted = self.coffee()
        self.assertRule("not_pending", self.ledger.confirm, posted.id)
        self.assertRule("unknown_target", self.ledger.confirm, "nope")


class Void(RuleTestCase):
    def test_void_posted_reverses_balances_but_keeps_history(self):
        ev = self.coffee("38.00")
        self.ledger.void(ev.id, "duplicate")
        self.assertEqual(self.bal("Assets:CMB"), D(0))
        self.assertEqual(self.ledger.state.txns[ev.id].status, "voided")
        self.assertEqual(self.total_lines(), 6 + 2)  # 6 opens + txn + void

    def test_void_pending_and_errors(self):
        ev = self.ledger.add_txn(
            "2026-10-07", [(None, "1", "CNY"), ("Assets:CMB", "-1", "CNY")], status="pending"
        )
        void = self.ledger.void(ev.id, "no longer relevant")
        self.assertRule("already_voided", self.ledger.void, ev.id, "again")
        self.assertRule("unknown_target", self.ledger.void, "nope", "x")
        self.assertRule("unknown_target", self.ledger.void, void.id, "voiding a void is not a thing")

    def test_correction_is_void_plus_new_txn(self):
        wrong = self.coffee("83.00")
        self.ledger.void(wrong.id, "typo")
        self.coffee("38.00")
        self.assertEqual(self.bal("Expenses:Dining:Coffee"), D("38.00"))


class Assertions(RuleTestCase):
    def test_pass_and_fail(self):
        self.fund("Assets:CMB", "1000.00", "2026-10-02")
        self.ledger.assert_balance("Assets:CMB", "2026-10-02", "1000.00", "CNY")
        err = self.assertRule("assertion_failed", self.ledger.assert_balance, "Assets:CMB", "2026-10-02", "999.00", "CNY")
        self.assertEqual(err.details, {"expected": "999.00", "actual": "1000.00"})

    def test_assertion_is_end_of_day(self):
        self.fund("Assets:CMB", "1000.00", "2026-10-03")
        self.ledger.assert_balance("Assets:CMB", "2026-10-02", 0, "CNY")
        self.ledger.assert_balance("Assets:CMB", "2026-10-03", 1000, "CNY")

    def test_zero_and_unknown(self):
        self.ledger.assert_balance("Assets:Alipay", "2026-10-07", 0, "CNY")
        self.assertRule("unknown_account", self.ledger.assert_balance, "Assets:Nope", "2026-10-07", 0, "CNY")

    def test_pending_does_not_count(self):
        self.ledger.add_txn("2026-10-02", [(None, "50", "CNY"), ("Assets:CMB", "-50", "CNY")], status="pending")
        self.ledger.assert_balance("Assets:CMB", "2026-10-02", 0, "CNY")


class Locking(RuleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fund("Assets:CMB", "1000.00", "2026-10-01")
        self.assertion = self.ledger.assert_balance("Assets:CMB", "2026-10-05", "1000.00", "CNY")

    def test_locked_period_rejects_new_postings(self):
        self.assertRule("period_locked", self.coffee, date="2026-10-05")
        self.assertRule("period_locked", self.coffee, date="2026-10-02")
        self.coffee(date="2026-10-06")

    def test_other_accounts_are_not_locked(self):
        self.ledger.add_txn("2026-10-02", [("Expenses:Gift", "5", "CNY"), ("Assets:Alipay", "-5", "CNY")])

    def test_agent_cannot_override_a_lock(self):
        self.assertRule(
            "lock_override_denied", self.coffee, date="2026-10-02",
            actor=AGENT, meta={"override_lock": "agent says so"},
        )

    def test_blank_override_reason_does_not_count(self):
        self.assertRule("period_locked", self.coffee, date="2026-10-02", meta={"override_lock": "  "})

    def test_human_override_is_recorded_and_check_flags_the_old_assertion(self):
        self.coffee("10.00", date="2026-10-02", meta={"override_lock": "forgot a coffee"})
        problems = self.ledger.check()
        self.assertEqual([p.code for p in problems], ["assertion_failed"])
        self.assertEqual(problems[0].event_id, self.assertion.id)
        self.ledger.void(self.assertion.id, "superseded after late entry")
        self.assertEqual(self.ledger.check(), [])
        self.ledger.assert_balance("Assets:CMB", "2026-10-05", "990.00", "CNY")
        self.assertEqual(self.ledger.check(), [])

    def test_voiding_into_a_locked_period_is_also_locked(self):
        ev = self.coffee("10.00", date="2026-10-06")
        self.ledger.assert_balance("Assets:CMB", "2026-10-06", "990.00", "CNY")
        self.assertRule("period_locked", self.ledger.void, ev.id, "oops")
        self.ledger.void(ev.id, "oops", meta={"override_lock": "bank corrected it"})

    def test_voided_assertion_releases_the_lock(self):
        self.ledger.void(self.assertion.id, "asserted the wrong day")
        self.coffee(date="2026-10-02")

    def test_confirm_is_locked_too(self):
        ev = self.ledger.add_txn(
            "2026-10-02", [(None, "5", "CNY"), ("Assets:CMB", "-5", "CNY")], status="pending", actor=AGENT
        )
        good = [("Expenses:Gift", "5", "CNY"), ("Assets:CMB", "-5", "CNY")]
        self.assertRule("period_locked", self.ledger.confirm, ev.id, good, actor=AGENT)
        self.assertEqual(self.ledger.state.txns[ev.id].status, "pending")


class ImportsAndOrdering(RuleTestCase):
    def test_duplicate_import_hash(self):
        first = self.coffee(import_hash="h1")
        err = self.assertRule("duplicate_import", self.coffee, import_hash="h1")
        self.assertEqual(err.details["existing"], first.id)
        self.coffee(import_hash="h1", meta={"allow_duplicate_import": True})
        self.coffee(import_hash="h2")

    def test_reimport_allowed_after_void(self):
        first = self.coffee(import_hash="h1")
        self.ledger.void(first.id, "wrong account")
        self.coffee(import_hash="h1")

    def test_ts_cannot_go_backwards(self):
        self.coffee()
        raw = self.ledger.new_event(
            "txn", actor=HUMAN, date="2026-10-07", status="posted",
            postings=self.ledger._postings([("Expenses:Gift", "1", "CNY"), ("Assets:CMB", "-1", "CNY")]),
        )
        raw["ts"] = "2020-01-01T00:00:00+08:00"
        self.assertRule("ts_regression", self.ledger.append, raw)

    def test_new_event_clamps_a_clock_that_ran_backwards(self):
        self.coffee()
        self.clock.now -= dt.timedelta(hours=1)
        self.coffee()  # must not raise

    def test_duplicate_id(self):
        ev = self.coffee()
        raw = dict(ev.raw)
        self.assertRule("duplicate_id", self.ledger.append, raw)
