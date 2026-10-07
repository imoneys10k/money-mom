import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from money_mom import __version__
from money_mom.cli import main

SRC = str(Path(__file__).resolve().parent.parent / "src")


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.root = self.dir / "ledger"
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ("MONEY_MOM_HOME", "MONEY_MOM_ACTOR"):
            os.environ.pop(name, None)

    def run_cli(self, *args, stdin=None, expect=0):
        out, err = io.StringIO(), io.StringIO()
        stdin_ctx = mock.patch.object(sys, "stdin", io.StringIO(stdin)) if stdin is not None else contextlib.nullcontext()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), stdin_ctx:
            try:
                code = main(list(args))
            except SystemExit as exit_:
                code = exit_.code
        self.assertEqual(code, expect, f"{args}\nstdout: {out.getvalue()}\nstderr: {err.getvalue()}")
        return out.getvalue(), err.getvalue()

    def js(self, *args, expect=0, **kw):
        out, _ = self.run_cli("--ledger", str(self.root), "--json", *args, expect=expect, **kw)
        return json.loads(out)

    def setup_ledger(self):
        self.js("init")
        for account, *extra in (
            ("Assets:CMB",), ("Assets:Alipay", "--currency", "CNY"), ("Expenses:Dining:Coffee",),
            ("Expenses:Gift",), ("Assets:Receivable:小王",), ("Income:Salary",),
        ):
            self.js("open", account, "--date", "2026-10-01", *extra)
        self.js("add", "--date", "2026-10-01", "--posting", "Assets:CMB 1000.00 CNY",
                "--posting", "Income:Salary -1000.00 CNY", "--narration", "salary")


class Basics(CliTestCase):
    def test_init_and_errors_are_json(self):
        data = self.js("init", "--base-currency", "HKD", "--tone", "zen")
        self.assertEqual(data["data"]["base_currency"], "HKD")
        again = self.js("init", expect=1)
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"]["code"], "already_initialized")

    def test_json_and_ledger_flags_work_before_or_after_the_subcommand(self):
        self.js("init")
        out, _ = self.run_cli("accounts", "--json", "--ledger", str(self.root))
        self.assertTrue(json.loads(out)["ok"])
        out, _ = self.run_cli("--json", "--ledger", str(self.root), "accounts")
        self.assertTrue(json.loads(out)["ok"])

    def test_environment_defaults(self):
        os.environ["MONEY_MOM_HOME"] = str(self.root)
        os.environ["MONEY_MOM_ACTOR"] = "agent:from-env"
        self.run_cli("init")
        self.run_cli("open", "Assets:CMB", "--date", "2026-10-01")
        self.run_cli("open", "Equity:Opening", "--date", "2026-10-01")
        self.run_cli("add", "--date", "2026-10-02", "--posting", "Assets:CMB 5 CNY", "--posting", "Equity:Opening -5 CNY")
        out, _ = self.run_cli("--json", "query", "SELECT actor_type, actor_name FROM txns")
        self.assertEqual(json.loads(out)["data"]["rows"], [["agent", "from-env"]])

    def test_not_a_ledger_is_a_clean_error(self):
        data = self.js("balance", expect=1)
        self.assertEqual(data["error"]["code"], "not_a_ledger")
        _, err = self.run_cli("--ledger", str(self.root), "balance", expect=1)
        self.assertTrue(err.startswith("error: [not_a_ledger]"))

    def test_version_flag(self):
        out, _ = self.run_cli("--version")
        self.assertIn(__version__, out)


class Recording(CliTestCase):
    def test_add_balance_check_roundtrip_with_exact_amounts(self):
        self.setup_ledger()
        self.js("add", "--date", "2026-10-07", "--posting", "Expenses:Dining:Coffee 38.50 CNY",
                "--posting", "Assets:CMB -38.50 CNY", "--payee", "瑞幸",
                "--actor", "agent:t", "--confidence", "0.96", "--source-type", "chat", "--source-ref", "午饭瑞幸")
        rows = {r["account"]: r["amount"] for r in self.js("balance")["data"]["balances"]}
        self.assertEqual(rows["Assets:CMB"], "961.50")
        self.assertEqual(rows["Expenses:Dining:Coffee"], "38.50")
        check = self.js("check")["data"]
        self.assertEqual((check["ok"], check["transactions"], check["pending"]), (True, 2, 0))

    def test_balance_filters(self):
        self.setup_ledger()
        only = self.js("balance", "--account", "Assets")["data"]["balances"]
        self.assertEqual([r["account"] for r in only], ["Assets:CMB"])
        none = self.js("balance", "--as-of", "2026-09-30")["data"]["balances"]
        self.assertEqual(none, [])
        everything = self.js("balance", "--all")["data"]["balances"]
        self.assertEqual(everything, self.js("balance")["data"]["balances"])  # no zero balances exist yet

    def test_human_output_is_readable(self):
        self.setup_ledger()
        out, _ = self.run_cli("--ledger", str(self.root), "balance")
        self.assertIn("Assets:CMB", out)
        self.assertIn("1000.00", out)
        out, _ = self.run_cli("--ledger", str(self.root), "check")
        self.assertTrue(out.startswith("OK:"))

    def test_unbalanced_is_refused_with_exit_1_and_nothing_written(self):
        self.setup_ledger()
        before = self.js("check")["data"]["events"]
        data = self.js("add", "--posting", "Expenses:Gift 5 CNY", "--posting", "Assets:CMB -4 CNY", expect=1)
        self.assertEqual(data["error"]["code"], "unbalanced")
        self.assertEqual(self.js("check")["data"]["events"], before)

    def test_from_json_file_stdin_and_atomic_batches(self):
        self.setup_ledger()
        one = {"date": "2026-10-02", "payee": "小王", "narration": "还钱",
               "postings": [{"account": "Assets:CMB", "amount": "200", "ccy": "CNY"},
                            {"account": "Assets:Receivable:小王", "amount": "-200", "ccy": "CNY"}],
               "source": {"type": "chat", "ref": "小王还了 200"}, "confidence": 0.8}
        path = self.dir / "txn.json"
        path.write_text(json.dumps(one, ensure_ascii=False), encoding="utf-8")
        self.js("add", "--from-json", str(path))
        self.js("add", "--from-json", "-", stdin=json.dumps({**one, "date": "2026-10-03"}, ensure_ascii=False))
        bad = {**one, "postings": [{"account": "Assets:CMB", "amount": "1", "ccy": "CNY"},
                                   {"account": "Expenses:Gift", "amount": "-2", "ccy": "CNY"}]}
        before = self.js("check")["data"]["transactions"]
        data = self.js("add", "--from-json", "-", stdin=json.dumps([one, bad]), expect=1)
        self.assertEqual(data["error"]["code"], "unbalanced")
        self.assertIn("event #2", data["error"]["where"])
        self.assertEqual(self.js("check")["data"]["transactions"], before)
        batch = self.js("add", "--from-json", "-", stdin=json.dumps([{**one, "date": "2026-10-04"}, {**one, "date": "2026-10-05"}]))
        self.assertEqual(len(batch["data"]), 2)

    def test_json_input_is_validated_strictly(self):
        self.setup_ledger()
        bad_field = json.dumps({"postings": [], "colour": "red"})
        self.assertEqual(self.js("add", "--from-json", "-", stdin=bad_field, expect=2)["error"]["code"], "usage_error")
        self.assertEqual(self.js("add", "--from-json", "-", stdin="{oops", expect=2)["error"]["code"], "usage_error")
        float_amount = json.dumps({"postings": [{"account": "Assets:CMB", "amount": 1.5, "ccy": "CNY"},
                                                {"account": "Expenses:Gift", "amount": "-1.5", "ccy": "CNY"}]})
        self.assertEqual(self.js("add", "--from-json", "-", stdin=float_amount, expect=1)["error"]["code"], "invalid_amount")

    def test_usage_errors_exit_2(self):
        self.setup_ledger()
        for args in (
            ("add",),
            ("add", "--posting", "Assets:CMB 1"),
            ("add", "--posting", "a b c d", "--posting", "x"),
            ("add", "--from-json", "-", "--posting", "Assets:CMB 1 CNY"),
            ("add", "--posting", "Assets:CMB 1 CNY", "--posting", "Expenses:Gift -1 CNY", "--source-ref", "x"),
        ):
            data = self.js(*args, expect=2, stdin="{}")
            self.assertEqual(data["error"]["code"], "usage_error", args)
        self.run_cli("--ledger", str(self.root), "--actor", "robot", "balance", expect=2)
        self.run_cli("--ledger", str(self.root), "nonsense", expect=2)


class PendingFlow(CliTestCase):
    def test_pending_confirm_void_and_show(self):
        self.setup_ledger()
        added = self.js("add", "--status", "pending", "--date", "2026-10-07", "--payee", "小王",
                        "--posting", "? 200 CNY", "--posting", "Assets:Alipay -200 CNY",
                        "--actor", "agent:t", "--confidence", "0.55")["data"][0]
        listed = self.js("pending")["data"]
        self.assertEqual([p["id"] for p in listed], [added["id"]])
        self.assertIsNone(listed[0]["postings"][0]["account"])
        self.assertEqual(listed[0]["confidence"], 0.55)
        self.run_cli("--ledger", str(self.root), "pending")
        self.js("confirm", added["id"], "--posting", "Assets:Receivable:小王 200 CNY", "--posting", "Assets:Alipay -200 CNY")
        self.assertEqual(self.js("pending")["data"], [])
        shown = self.js("show", added["id"])["data"]
        self.assertEqual((shown["kind"], shown["status"]), ("txn", "posted"))
        self.assertEqual([e["kind"] for e in shown["events"]], ["txn", "confirm"])
        self.js("void", added["id"], "--reason", "小王说搞错了")
        self.assertEqual(self.js("show", added["id"])["data"]["status"], "voided")
        self.assertEqual(self.js("show", "nope", expect=1)["error"]["code"], "unknown_target")
        self.js("void", added["id"], "--reason", "again", expect=1)

    def test_unresolved_account_cannot_be_posted(self):
        self.setup_ledger()
        data = self.js("add", "--posting", "? 5 CNY", "--posting", "Assets:CMB -5 CNY", expect=1)
        self.assertEqual(data["error"]["code"], "unresolved_account")


class LockingFromTheCli(CliTestCase):
    def test_assert_lock_agent_denied_human_allowed(self):
        self.setup_ledger()
        self.js("assert", "Assets:CMB", "1000.00", "CNY", "--date", "2026-10-05")
        late = ("add", "--date", "2026-10-02", "--posting", "Expenses:Gift 1 CNY", "--posting", "Assets:CMB -1 CNY")
        self.assertEqual(self.js(*late, expect=1)["error"]["code"], "period_locked")
        denied = self.js(*late, "--actor", "agent:t", "--override-lock", "I want to", expect=1)
        self.assertEqual(denied["error"]["code"], "lock_override_denied")
        self.js(*late, "--override-lock", "forgot a gift")
        problems = self.js("check", expect=1)["data"]
        self.assertFalse(problems["ok"])
        self.assertEqual(problems["problems"][0]["code"], "assertion_failed")
        _, err = self.run_cli("--ledger", str(self.root), "check", expect=1)
        self.assertIn("PROBLEMS", _)
        wrong = self.js("assert", "Assets:CMB", "1.00", "CNY", "--date", "2026-10-06", expect=1)
        self.assertEqual(wrong["error"]["code"], "assertion_failed")


class QueryCommand(CliTestCase):
    def test_query_matches_balance_and_is_read_only(self):
        self.setup_ledger()
        result = self.js("query", "SELECT account, amount_text FROM postings WHERE account = 'Assets:CMB'")["data"]
        self.assertEqual(result["rows"], [["Assets:CMB", "1000.00"]])
        out, _ = self.run_cli("--ledger", str(self.root), "query", "SELECT name FROM accounts ORDER BY name LIMIT 2")
        self.assertIn("Assets:Alipay", out)
        refused = self.js("query", "DELETE FROM txns", expect=1)
        self.assertEqual(refused["error"]["code"], "query_error")
        self.assertEqual(self.js("check")["data"]["transactions"], 1)
        truncated = self.js("query", "SELECT seq FROM events", "--limit", "2")["data"]
        self.assertTrue(truncated["truncated"])
        out, _ = self.run_cli("--ledger", str(self.root), "query", "SELECT 1 WHERE 0")
        self.assertIn("(no rows)", out)

    def test_accounts_listing(self):
        self.setup_ledger()
        listed = {a["name"]: a for a in self.js("accounts")["data"]}
        self.assertEqual(listed["Assets:Alipay"]["currencies"], ["CNY"])
        self.assertIsNone(listed["Assets:CMB"]["closed"])
        self.run_cli("--ledger", str(self.root), "accounts")


class RealProcess(unittest.TestCase):
    def run_module(self, *args):
        env = {**os.environ, "PYTHONPATH": SRC + os.pathsep + os.environ.get("PYTHONPATH", "")}
        return subprocess.run([sys.executable, "-m", "money_mom", *args], capture_output=True, env=env, timeout=60)

    def test_module_entry_point_and_utf8_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "mm")
            self.assertEqual(self.run_module("--ledger", root, "init").returncode, 0)
            for account in ("Assets:招行", "Equity:期初"):
                self.assertEqual(self.run_module("--ledger", root, "open", account, "--date", "2026-10-01").returncode, 0)
            added = self.run_module("--ledger", root, "add", "--date", "2026-10-02",
                                    "--posting", "Assets:招行 5 CNY", "--posting", "Equity:期初 -5 CNY")
            self.assertEqual(added.returncode, 0, added.stderr)
            result = self.run_module("--ledger", root, "--json", "balance")
            self.assertEqual(result.returncode, 0)
            decoded = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(decoded["data"]["balances"][0]["account"], "Assets:招行")
            failed = self.run_module("--ledger", root, "add", "--posting", "Assets:招行 1 CNY", "--posting", "Equity:期初 -2 CNY")
            self.assertEqual(failed.returncode, 1)
            self.assertIn("unbalanced", failed.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
