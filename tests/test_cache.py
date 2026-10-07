import os
import sqlite3
import time
import unittest

from money_mom import Ledger, LedgerError
from money_mom.cache import CACHE_NAME, open_cache, run_query

from helpers import AGENT, LedgerTestCase


class CacheTestCase(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.open_accounts()
        self.fund("Assets:CMB", "1000.00", "2026-10-01")
        self.ledger.add_txn(
            "2026-10-07",
            [("Expenses:Dining:Coffee", "38.50", "CNY"), ("Assets:CMB", "-38.50", "CNY")],
            payee="瑞幸", actor=AGENT, confidence=0.9, source={"type": "chat", "ref": "午饭瑞幸"},
        )

    def query(self, sql, **kw):
        conn = open_cache(self.root, self.ledger.state)
        try:
            return run_query(conn, sql, **kw)
        finally:
            conn.close()

    def rows(self, sql):
        return self.query(sql)["rows"]


class Contents(CacheTestCase):
    def test_balances_view_matches_exact_balances(self):
        rows = {(a, c): amt for a, c, amt in self.rows("SELECT account, ccy, amount FROM v_balances")}
        for (account, ccy), exact in self.ledger.state.balances().items():
            self.assertAlmostEqual(rows[(account, ccy)], float(exact), places=6)

    def test_exact_text_amounts_are_kept(self):
        self.assertEqual(
            self.rows("SELECT amount_text FROM postings WHERE account = 'Expenses:Dining:Coffee'"), [["38.50"]]
        )

    def test_pending_and_voided_are_kept_out_of_v_postings(self):
        pend = self.ledger.add_txn("2026-10-08", [(None, "5", "CNY"), ("Assets:CMB", "-5", "CNY")], status="pending")
        gone = self.ledger.add_txn("2026-10-08", [("Expenses:Gift", "7", "CNY"), ("Assets:CMB", "-7", "CNY")])
        self.ledger.void(gone.id, "typo")
        in_view = {r[0] for r in self.rows("SELECT DISTINCT txn_id FROM v_postings")}
        self.assertNotIn(pend.id, in_view)
        self.assertNotIn(gone.id, in_view)
        pending = self.rows("SELECT id, postings FROM v_pending")
        self.assertEqual(pending, [[pend.id, "? 5 CNY; Assets:CMB -5 CNY"]])
        statuses = dict(self.rows("SELECT id, status FROM txns WHERE date = '2026-10-08'"))
        self.assertEqual(statuses[gone.id], "voided")

    def test_provenance_columns(self):
        row = self.rows("SELECT payee, actor_type, confidence, source_type, source_ref FROM txns WHERE payee IS NOT NULL")
        self.assertEqual(row, [["瑞幸", "agent", 0.9, "chat", "午饭瑞幸"]])

    def test_monthly_view_and_chinese_text_roundtrip(self):
        rows = self.rows("SELECT month, account FROM v_monthly WHERE account LIKE 'Expenses:%'")
        self.assertEqual(rows, [["2026-10", "Expenses:Dining:Coffee"]])

    def test_voided_assertions_are_marked(self):
        a = self.ledger.assert_balance("Assets:CMB", "2026-10-01", "1000.00", "CNY")
        self.ledger.void(a.id, "wrong day")
        self.assertEqual(self.rows("SELECT voided_by IS NOT NULL FROM assertions"), [[1]])

    def test_events_table_lists_everything_in_order(self):
        kinds = [r[0] for r in self.rows("SELECT kind FROM events ORDER BY seq")]
        self.assertEqual(kinds[:6], ["open"] * 6)
        self.assertEqual(kinds[-1], "txn")


class Freshness(CacheTestCase):
    def cache_path(self):
        return self.root / CACHE_NAME

    def test_unchanged_ledger_reuses_the_file(self):
        self.query("SELECT 1")
        first = self.cache_path().stat()
        time.sleep(0.02)
        self.query("SELECT 1")
        second = self.cache_path().stat()
        self.assertEqual((first.st_ino, first.st_mtime_ns), (second.st_ino, second.st_mtime_ns))

    def test_new_events_trigger_a_rebuild(self):
        before = self.rows("SELECT COUNT(*) FROM txns")[0][0]
        self.fund("Assets:CMB", "1.00", "2026-10-09")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM txns")[0][0], before + 1)

    def test_changes_made_by_another_process_are_noticed(self):
        self.query("SELECT 1")
        other = Ledger.open(self.root, clock=self.clock)
        other.add_txn("2026-10-09", [("Expenses:Gift", "3", "CNY"), ("Assets:CMB", "-3", "CNY")])
        self.ledger.reload()
        self.assertEqual(self.rows("SELECT COUNT(*) FROM txns WHERE date = '2026-10-09'")[0][0], 1)

    def test_editing_an_event_in_place_is_noticed(self):
        self.query("SELECT 1")
        path = next(self.ledger.ledger_dir.glob("*.jsonl"))
        text = path.read_text(encoding="utf-8")
        self.assertIn("38.50", text)
        path.write_text(text.replace("38.50", "38.60").replace("-38.50", "-38.60"), encoding="utf-8")
        ledger = Ledger.open(self.root)
        conn = open_cache(self.root, ledger.state)
        try:
            self.assertEqual(run_query(conn, "SELECT amount_text FROM postings WHERE amount_text LIKE '38.%'")["rows"], [["38.60"]])
        finally:
            conn.close()

    def test_garbage_cache_file_is_replaced(self):
        self.cache_path().write_bytes(b"this is not a database")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM accounts")[0][0], 6)

    def test_cache_from_another_layout_is_rebuilt(self):
        self.query("SELECT 1")
        raw = sqlite3.connect(self.cache_path())
        raw.execute("UPDATE meta SET value = '0' WHERE key = 'layout'")
        raw.commit()
        raw.close()
        self.assertEqual(self.rows("SELECT value FROM meta WHERE key = 'layout'"), [["1"]])

    def test_deleting_the_cache_loses_nothing(self):
        self.query("SELECT 1")
        os.remove(self.cache_path())
        self.assertEqual(self.rows("SELECT COUNT(*) FROM txns")[0][0], 2)

    def test_falls_back_to_memory_when_the_cache_cannot_be_written(self):
        self.root.chmod(0o500)
        self.addCleanup(self.root.chmod, 0o700)
        if os.access(self.root, os.W_OK):
            self.skipTest("directory permissions are not enforced here (running as root, or Windows)")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM txns")[0][0], 2)


class ReadOnlySandbox(CacheTestCase):
    def refused(self, sql):
        with self.assertRaises(LedgerError) as ctx:
            self.query(sql)
        self.assertEqual(ctx.exception.code, "query_error", sql)
        return ctx.exception

    def test_writes_ddl_and_pragmas_are_refused(self):
        for sql in (
            "DELETE FROM txns",
            "UPDATE txns SET status = 'posted'",
            "INSERT INTO meta VALUES ('x', 'y')",
            "DROP TABLE txns",
            "DROP VIEW v_postings",
            "CREATE TABLE evil (x)",
            "ATTACH DATABASE ':memory:' AS other",
            "PRAGMA writable_schema = ON",
            "VACUUM",
        ):
            self.refused(sql)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM txns")[0][0], 2)

    def test_the_authorizer_alone_protects_a_writable_connection(self):
        # The in-memory fallback is writable, so the authorizer is the only guard there.
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        for sql in ("DELETE FROM t", "DROP TABLE t", "INSERT INTO t VALUES (2)", "CREATE TABLE u (y)",
                    "ATTACH DATABASE ':memory:' AS other", "PRAGMA user_version = 7", "VACUUM"):
            with self.assertRaises(LedgerError, msg=sql) as ctx:
                run_query(conn, sql)
            self.assertEqual(ctx.exception.code, "query_error", sql)
        self.assertEqual(run_query(conn, "SELECT x FROM t")["rows"], [[1]])
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 0)
        conn.close()

    def test_only_one_statement_is_allowed(self):
        self.refused("SELECT 1; DROP TABLE txns")

    def test_extension_loading_is_refused(self):
        self.refused("SELECT load_extension('x')")

    def test_with_clauses_and_recursion_are_fine(self):
        rows = self.rows("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3) SELECT i FROM n")
        self.assertEqual(rows, [[1], [2], [3]])

    def test_syntax_errors_are_reported_as_query_errors(self):
        self.refused("SELEKT 1")
        self.refused("SELECT * FROM no_such_table")

    def test_row_limit_and_truncation(self):
        result = self.query("SELECT seq FROM events ORDER BY seq", limit=3)
        self.assertEqual(result["rows"], [[1], [2], [3]])
        self.assertTrue(result["truncated"])
        full = self.query("SELECT seq FROM events", limit=1000)
        self.assertFalse(full["truncated"])
        with self.assertRaises(LedgerError):
            self.query("SELECT 1", limit=0)

    def test_runaway_queries_are_stopped(self):
        with self.assertRaises(LedgerError) as ctx:
            self.query(
                "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c",
                timeout=0.3,
            )
        self.assertEqual(ctx.exception.code, "query_timeout")


if __name__ == "__main__":
    unittest.main()
