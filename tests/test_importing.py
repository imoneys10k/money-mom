import datetime as dt
import json
import unittest
from decimal import Decimal
from pathlib import Path

from money_mom import Ledger, LedgerError
from money_mom.importing import (
    _money, add_rule, inspect_statement, list_mappings, load_mapping, mapping_path, parse_mapping, read_statement,
    recheck, row_hashes, run_import, save_mapping,
)
from money_mom.templates import apply_template

from helpers import AGENT, HUMAN, LedgerTestCase
from test_cli import CliTestCase

D = Decimal

HEADER = "交易时间,交易分类,交易对方,商品说明,收/支,金额,交易状态,交易订单号"
ALIPAY_ROWS = [
    "2026-09-03 08:12:01 ,餐饮美食,瑞幸咖啡 ,生椰拿铁 ,支出,38.00,交易成功,T0001\t",
    "2026-09-05 12:30:10 ,餐饮美食,美团 ,外卖订单 ,支出,52.50,交易成功,T0002",
    "2026-09-05 12:31:00 ,餐饮美食,美团 ,外卖订单 ,支出,52.50,交易成功,T0003",
    "2026-09-08 09:00:00 ,收入,小王 ,还钱 ,收入,200.00,交易成功,T0004",
    '2026-09-09 10:00:00 ,日用,某某商贸有限公司 ,日用品 ,支出,"1,299.00",交易成功,T0005',
    "2026-09-10 10:00:00 ,其他,微信 ,充值 ,不计收支,500.00,交易成功,T0006",
    "2026-09-12 10:00:00 ,餐饮美食,海底捞 ,聚餐 ,支出,486.00,交易关闭,T0007",
]
PREAMBLE = ["支付宝交易记录明细查询", "账号:[demo@example.com]", "------电子客户回单------"]
FOOTER = ["共7笔记录"]

ALIPAY_TOML = """
name = "alipay-demo"
ccy = "CNY"

[columns]
date = "交易时间"
amount = "金额"
direction = "收/支"
payee = "交易对方"
description = "商品说明"
id = "交易订单号"

[format]
date = "%Y-%m-%d %H:%M:%S"
sign = "unsigned"

[direction]
in = ["收入"]
out = ["支出"]
ignore = ["不计收支"]

[[skip]]
column = "交易状态"
values = ["交易关闭"]

[[rules]]
match = "瑞幸"
account = "咖啡"

[[rules]]
match = "小王"
account = "应收"
"""


def err(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except LedgerError as e:
        return e
    raise AssertionError("expected a LedgerError")


class FileCase(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)
        self.dir = self.root.parent / "files"
        self.dir.mkdir()

    def csv(self, name="alipay.csv", rows=None, *, preamble=PREAMBLE, footer=FOOTER, header=HEADER, encoding="gb18030", newline="\r\n"):
        lines = list(preamble) + [header] + list(ALIPAY_ROWS if rows is None else rows) + list(footer)
        path = self.dir / name
        path.write_bytes((newline.join(lines) + newline).encode(encoding))
        return path

    def mapping(self, text=ALIPAY_TOML, name="alipay-demo"):
        save_mapping(self.ledger, name, text)
        return load_mapping(self.ledger, name)

    def run_import(self, path=None, **kw):
        kw.setdefault("account", "支付宝")
        return run_import(self.ledger, path or self.csv(), kw.pop("mapping", None) or self.mapping(), **kw)

    def bal(self, account, ccy="CNY"):
        return self.ledger.state.balance_of(account).get(ccy, D(0))


class MoneyCells(unittest.TestCase):
    def test_what_banks_write(self):
        cases = {
            "38": D("38"), "38.50": D("38.50"), " 1,299.00 ": D("1299.00"), "¥38.5": D("38.5"), "￥ 1,000": D(1000),
            "-12.30": D("-12.30"), "+5": D(5), "(12.30)": D("-12.30"), "12.30-": D("-12.30"), "3.50元": D("3.50"),
            ".5": D("0.5"), "": None, "   ": None, "１２３": D(123),
        }
        for text, expected in cases.items():
            self.assertEqual(_money(text), expected, repr(text))

    def test_text_is_not_a_number_and_does_not_crash(self):
        for text in ("交易分类", "abc", "--5", "1.2.3", "12,34", "N/A", "共7笔记录", "第3页", "T0001", "2026-09-03"):
            with self.assertRaises(ValueError, msg=text):
                _money(text)


class MappingParsing(unittest.TestCase):
    def test_a_good_mapping(self):
        m = parse_mapping(ALIPAY_TOML, "alipay-demo")
        self.assertEqual((m.name, m.ccy, m.sign, m.date_formats), ("alipay-demo", "CNY", "unsigned", ("%Y-%m-%d %H:%M:%S",)))
        self.assertEqual((m.direction_in, m.direction_out, m.direction_ignore), (("收入",), ("支出",), ("不计收支",)))
        self.assertEqual([r.account for r in m.rules], ["咖啡", "应收"])
        self.assertEqual(m.skip_when, (("交易状态", ("交易关闭",), None),))

    def test_full_width_names_are_normalised_like_the_file(self):
        m = parse_mapping('[columns]\ndate = "时间"\namount = "金额（元）"\n[format]\ndate = "%Y-%m-%d"\n')
        self.assertEqual(m.columns["amount"], "金额(元)")

    def test_defaults(self):
        m = parse_mapping('[columns]\ndate = "d"\namount = "a"\n[format]\ndate = "%Y-%m-%d"\n')
        self.assertEqual(m.sign, "inflow_positive")
        self.assertEqual(m.header, ("d", "a"))
        pair = parse_mapping('[columns]\ndate = "d"\namount_in = "in"\namount_out = "out"\n[format]\ndate = "%Y-%m-%d"\n')
        self.assertEqual(pair.sign, "inflow_positive")

    def test_it_refuses_what_it_cannot_run(self):
        base = '[columns]\ndate = "d"\namount = "a"\n[format]\ndate = "%Y-%m-%d"\n'
        bad = {
            "not toml": "[columns",
            "unknown top key": base + "colour = 1\n",
            "unknown column key": base.replace('amount = "a"', 'amount = "a"\nwhat = "x"'),
            "no columns": '[format]\ndate = "%Y"\n',
            "no date column": '[columns]\namount = "a"\n[format]\ndate = "%Y"\n',
            "no amount at all": '[columns]\ndate = "d"\n[format]\ndate = "%Y"\n',
            "both amount kinds": base.replace('amount = "a"', 'amount = "a"\namount_in = "i"'),
            "no date format": '[columns]\ndate = "d"\namount = "a"\n',
            "bad sign": base.replace('[format]', '[format]\nsign = "sideways"'),
            "unsigned without direction": base.replace('[format]', '[format]\nsign = "unsigned"'),
            "direction without in/out": '[columns]\ndate = "d"\namount = "a"\ndirection = "x"\n[format]\ndate = "%Y"\n',
            "bad ccy": 'ccy = "cny"\n' + base,
            "bad encoding": 'encoding = "klingon"\n' + base,
            "bad delimiter": 'delimiter = "ab"\n' + base,
            "rule without match": base + '[[rules]]\naccount = "x"\n',
            "rule with both": base + '[[rules]]\nmatch = "x"\naccount = "y"\nskip = true\n',
            "rule with neither": base + '[[rules]]\nmatch = "x"\n',
            "rule bad field": base + '[[rules]]\nmatch = "x"\nfield = "colour"\naccount = "y"\n',
            "rule bad regex": base + '[[rules]]\nmatch = "("\nregex = true\naccount = "y"\n',
            "skip without column": base + '[[skip]]\nvalues = ["x"]\n',
        }
        for label, text in bad.items():
            e = err(parse_mapping, text)
            self.assertEqual(e.code, "invalid_mapping" if label != "bad ccy" else e.code, label)
            self.assertIn(e.code, {"invalid_mapping", "invalid_currency"}, label)


class Reading(FileCase):
    def read(self, path=None, mapping=None, **kw):
        return read_statement(path or self.csv(), mapping or parse_mapping(ALIPAY_TOML), **kw)

    def test_gbk_preamble_footer_padding_and_totals(self):
        st = self.read()
        self.assertEqual((st.encoding, st.delimiter, st.header_line, st.preamble_lines), ("gb18030", ",", 4, 3))
        self.assertEqual(len(st.rows), 5)
        first = st.rows[0]
        self.assertEqual((first.date, first.amount, first.payee, first.description, first.id),
                         (dt.date(2026, 9, 3), D("-38.00"), "瑞幸咖啡", "生椰拿铁", "T0001"))
        self.assertEqual([r.amount for r in st.rows], [D("-38.00"), D("-52.50"), D("-52.50"), D("200.00"), D("-1299.00")])
        self.assertEqual(dict(st.ignored), {"short line (summary or footer)": 1, "skipped by [[skip]]": 1, "direction '不计收支'": 1})
        self.assertEqual(st.ignored_examples, ["line 12: 共7笔记录"])
        self.assertEqual(st.filename, "alipay.csv")
        self.assertEqual(len(st.sha256), 64)

    def test_a_long_preamble_does_not_hide_the_table_from_inspect(self):
        long_preamble = [f"说明第{i}行" for i in range(40)]
        data = inspect_statement(self.csv(preamble=long_preamble))
        self.assertEqual((data["header_line"], data["rows"]), (41, 7))
        self.assertIn("交易时间", data["columns_in_file"])

    def test_utf8_with_bom_and_other_delimiters(self):
        path = self.csv(encoding="utf-8-sig")
        self.assertEqual(self.read(path).encoding, "utf-8-sig")
        lines = [HEADER.replace(",", "\t")] + [r.replace(",", "\t").replace("1\t299.00", "1299.00") for r in ALIPAY_ROWS]
        tab = self.dir / "tab.tsv"
        tab.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        st = self.read(tab)
        self.assertEqual((st.delimiter, len(st.rows)), ("\t", 5))

    def test_forced_encoding_that_cannot_decode_is_an_error(self):
        e = err(self.read, self.csv(), encoding="ascii")
        self.assertEqual(e.code, "invalid_statement")

    def test_the_three_ways_amounts_carry_direction(self):
        text = lambda extra: f'[columns]\ndate = "d"\n{extra}\n[format]\ndate = "%Y-%m-%d"\n'
        def read(rows, toml, header="d,a,x"):
            path = self.dir / "m.csv"
            path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
            return [r.amount for r in read_statement(path, parse_mapping(toml)).rows]
        self.assertEqual(read(["2026-09-01,-10,", "2026-09-02,25,"], text('amount = "a"')), [D(-10), D(25)])
        credit_card = '[columns]\ndate = "d"\namount = "a"\n[format]\ndate = "%Y-%m-%d"\nsign = "outflow_positive"\n'
        self.assertEqual(read(["2026-09-01,10,", "2026-09-02,-25,"], credit_card), [D(-10), D(25)])
        self.assertEqual(read(["2026-09-01,0,40", "2026-09-02,15,0", "2026-09-03,,5"], text('amount_in = "a"\namount_out = "x"')),
                         [D(-40), D(15), D(-5)])

    def test_parentheses_thousands_and_zero(self):
        path = self.dir / "p.csv"
        path.write_text('d,a\n2026-09-01,"(1,234.50)"\n2026-09-02,"1,000"\n2026-09-03,0.00\n', encoding="utf-8")
        mapping = parse_mapping('[columns]\ndate = "d"\namount = "a"\n[format]\ndate = "%Y-%m-%d"\n')
        st = read_statement(path, mapping)
        self.assertEqual([r.amount for r in st.rows], [D("-1234.50"), D("1000")])
        self.assertEqual(dict(st.ignored), {"zero amount": 1})

    def test_unreadable_rows_stop_everything_and_say_where(self):
        rows = list(ALIPAY_ROWS) + ["not a date,x,y,z,支出,5,交易成功,T9", "2026-09-20 00:00:00,x,y,z,退款,5,交易成功,T10",
                                    "2026-09-21 00:00:00,x,y,z,支出,abc,交易成功,T11"]
        e = err(self.read, self.csv(rows=rows))
        self.assertEqual(e.code, "invalid_statement")
        self.assertEqual(e.details["count"], 3)
        text = " ".join(e.details["problems"])
        self.assertIn("cannot read the date 'not a date'", text)
        self.assertIn("direction '退款'", text)
        self.assertIn("cannot read an amount", text)
        self.assertIn("nothing was imported", e.message)

    def test_missing_header_or_column(self):
        e = err(self.read, self.csv(header="a,b,c"))
        self.assertEqual(e.code, "invalid_statement")
        self.assertIn("header row", e.message)
        m = parse_mapping(ALIPAY_TOML.replace('payee = "交易对方"', 'payee = "没有这一列"').replace('header', 'header'),)
        e = err(read_statement, self.csv(), m)
        self.assertIn("没有这一列", e.message)

    def test_hashes_are_stable_distinct_for_identical_rows_and_keyed_by_id(self):
        rows = self.read().rows
        a = row_hashes(rows, "Assets:支付宝", "CNY")
        self.assertEqual(a, row_hashes(rows, "Assets:支付宝", "CNY"))
        self.assertEqual(len(set(a)), len(a))
        self.assertNotEqual(a, row_hashes(rows, "Assets:银行卡", "CNY"))
        twin = [rows[1], rows[1]]  # the very same row twice in one file
        h = row_hashes(twin, "Assets:支付宝", "CNY")
        self.assertNotEqual(h[0], h[1])
        self.assertTrue(all(x.startswith("imp1-") for x in a))
        # without an id the date, amount and text decide, so a changed description is a different row
        from dataclasses import replace
        no_id = [replace(r, id="") for r in rows]
        changed = [replace(no_id[0], description="别的")] + no_id[1:]
        self.assertNotEqual(row_hashes(no_id, "A", "CNY")[0], row_hashes(changed, "A", "CNY")[0])
        # with an id the description does not matter
        renamed = [replace(rows[0], description="别的")] + rows[1:]
        self.assertEqual(row_hashes(rows, "A", "CNY")[0], row_hashes(renamed, "A", "CNY")[0])


class Importing(FileCase):
    def test_rule_matched_rows_post_and_the_rest_wait(self):
        result = self.run_import()
        self.assertEqual((result["rows_read"], result["imported"], result["posted"], result["pending"]), (5, 5, 2, 3))
        self.assertTrue(result["written"])
        self.assertEqual(self.bal("Expenses:餐饮:咖啡"), D(38))
        self.assertEqual(self.bal("Assets:应收"), D(-200))  # the friend paid us back: the receivable shrinks
        self.assertEqual(self.bal("Assets:支付宝"), D(-38) + D(200))  # only posted rows count
        self.assertEqual(len(self.ledger.state.pending()), 3)

    def test_directions_and_pending_shape(self):
        self.run_import()
        by_payee = {r.event.payee: r for r in self.ledger.state.txns.values()}
        pending = by_payee["美团"]
        self.assertEqual(pending.status, "pending")
        self.assertEqual([(p.account, p.amount) for p in pending.postings], [("Assets:支付宝", D("-52.50")), (None, D("52.50"))])
        inflow = by_payee["小王"]
        self.assertEqual([(p.account, p.amount) for p in inflow.postings], [("Assets:支付宝", D("200.00")), ("Assets:应收", D("-200.00"))])

    def test_unresolved_payees_are_grouped_for_one_question_each(self):
        result = self.run_import()
        top = {u["payee"]: u for u in result["unresolved_payees"]}
        self.assertEqual((top["美团"]["count"], top["美团"]["total"]), (2, "-105.00"))
        self.assertEqual(top["某某商贸有限公司"]["total"], "-1299.00")
        self.assertEqual(result["unresolved_payees"][0]["payee"], "美团")  # the most frequent first

    def test_provenance_of_each_entry(self):
        self.run_import()
        ev = next(r.event for r in self.ledger.state.txns.values() if r.event.payee == "瑞幸咖啡")
        self.assertEqual((ev.source["type"], ev.source["ref"], len(ev.source["sha256"])), ("import", "alipay.csv", 64))
        self.assertEqual(ev.meta["import"], {"mapping": "alipay-demo", "line": 5, "file": "alipay.csv"})
        self.assertTrue(ev.import_hash.startswith("imp1-"))
        self.assertEqual((ev.narration, ev.date), ("生椰拿铁", dt.date(2026, 9, 3)))
        self.assertNotIn("/", ev.source["ref"])  # a file name, never a path

    def test_importing_the_same_file_again_adds_nothing(self):
        self.run_import()
        before = self.total_lines()
        again = self.run_import()
        self.assertEqual((again["imported"], again["duplicates_skipped"], again["written"]), (0, 5, False))
        self.assertEqual(self.total_lines(), before)

    def test_identical_rows_in_one_file_are_both_kept(self):
        rows = ["2026-09-05 12:30:10 ,餐饮美食,美团 ,外卖订单 ,支出,52.50,交易成功,\t"] * 2
        result = self.run_import(self.csv(rows=rows))
        self.assertEqual(result["imported"], 2)
        self.run_import(self.csv("again.csv", rows=rows))  # a second export of the same two rows
        self.assertEqual(len(self.ledger.state.txns), 2)

    def test_a_later_export_only_adds_what_is_new(self):
        self.run_import()
        extra = ALIPAY_ROWS + ["2026-09-20 08:00:00 ,餐饮美食,瑞幸咖啡 ,美式 ,支出,28.00,交易成功,T0100"]
        result = self.run_import(self.csv("later.csv", rows=extra))
        self.assertEqual((result["imported"], result["duplicates_skipped"], result["posted"]), (1, 5, 1))

    def test_since_and_until(self):
        result = self.run_import(since=dt.date(2026, 9, 8), until=dt.date(2026, 9, 9))
        self.assertEqual((result["imported"], result["out_of_range"]), (2, 3))

    def test_dry_run_reports_everything_and_writes_nothing(self):
        before = self.event_files()
        result = self.run_import(dry_run=True)
        self.assertEqual((result["imported"], result["posted"], result["pending"], result["written"], result["dry_run"]), (5, 2, 3, False, True))
        self.assertEqual(self.event_files(), before)

    def test_a_refused_row_aborts_the_batch_and_names_its_line(self):
        self.run_import()
        self.ledger.assert_balance("Assets:支付宝", "2026-09-30", "162", "CNY")  # -38 spent + 200 received: September is reconciled and locked
        before = self.total_lines()
        later = ["2026-09-20 08:00:00 ,餐饮美食,瑞幸咖啡 ,美式 ,支出,28.00,交易成功,T0100",
                 "2026-10-02 08:00:00 ,餐饮美食,瑞幸咖啡 ,美式 ,支出,29.00,交易成功,T0101"]
        e = err(self.run_import, self.csv("later.csv", rows=later))
        self.assertEqual(e.code, "period_locked")
        self.assertEqual((e.details["row_line"], e.details["row_date"], e.details["row_amount"]), (5, "2026-09-20", "-28.00"))
        self.assertIn("statement line 5", e.message)
        self.assertIn("--since", e.message)
        self.assertEqual(self.total_lines(), before)  # the October row was not written either
        ok = self.run_import(self.csv("later.csv", rows=later), since=dt.date(2026, 10, 1))
        self.assertEqual(ok["imported"], 1)

    def test_the_statement_account_must_be_a_real_asset_or_liability(self):
        self.assertEqual(err(self.run_import, account="不存在").code, "unresolved_account")
        self.assertEqual(err(self.run_import, account="咖啡").code, "unresolved_account")  # an expense is not a statement account

    def test_currency_comes_from_the_mapping_or_the_account(self):
        self.ledger.open_account("Assets:美元户", "2026-01-01", currencies=["USD"])
        usd_mapping = self.mapping(ALIPAY_TOML.replace('ccy = "CNY"\n', ""), "usd")
        result = self.run_import(account="美元户", mapping=usd_mapping)
        self.assertEqual(result["ccy"], "USD")
        self.assertTrue(all(p.ccy == "USD" for r in self.ledger.state.txns.values() for p in r.postings))

    def test_a_rule_pointing_at_a_missing_account_leaves_the_row_pending_with_the_reason(self):
        toml = ALIPAY_TOML.replace('account = "咖啡"', 'account = "不存在的类别"')
        result = self.run_import(mapping=self.mapping(toml, "broken"))
        coffee = next(u for u in result["unresolved_payees"] if u["payee"] == "瑞幸咖啡")
        self.assertIn("不存在的类别", coffee["why"])

    def test_skip_rules_drop_rows_and_say_so(self):
        toml = ALIPAY_TOML + '\n[[rules]]\nmatch = "美团"\nskip = true\n'
        result = self.run_import(mapping=self.mapping(toml, "skippy"))
        self.assertEqual(result["ignored"]["skipped by a rule"], 2)
        self.assertEqual(result["imported"], 3)

    def test_rule_order_and_fields(self):
        toml = ALIPAY_TOML.replace('[[rules]]\nmatch = "瑞幸"\naccount = "咖啡"', '[[rules]]\nmatch = "生椰"\nfield = "description"\naccount = "外卖"\n\n[[rules]]\nmatch = "瑞幸"\nfield = "payee"\naccount = "咖啡"')
        self.run_import(mapping=self.mapping(toml, "ordered"))
        ev = next(r for r in self.ledger.state.txns.values() if r.event.payee == "瑞幸咖啡")
        self.assertEqual(ev.postings[1].account, "Expenses:餐饮:外卖")  # the first matching rule wins

    def test_regex_rules(self):
        toml = ALIPAY_TOML + '\n[[rules]]\nmatch = "美\\\\S"\nregex = true\naccount = "外卖"\n'
        self.run_import(mapping=self.mapping(toml, "rx"))
        self.assertEqual(self.bal("Expenses:餐饮:外卖"), D("105.00"))


class AgentImports(FileCase):
    def test_an_agent_must_say_how_sure_it_is(self):
        e = err(self.run_import, actor=AGENT)
        self.assertEqual(e.code, "confidence_required")

    def test_even_a_dry_run_asks_for_it_so_the_real_run_cannot_surprise(self):
        e = err(self.run_import, actor=AGENT, dry_run=True)
        self.assertEqual(e.code, "confidence_required")
        self.assertEqual(self.total_lines(), 47)  # nothing but the template's accounts

    def test_a_confident_agent_posts_rule_matched_rows(self):
        result = self.run_import(actor=AGENT, confidence=0.95)
        self.assertEqual((result["posted"], result["pending"], result["held_for_confidence"]), (2, 3, False))

    def test_a_hesitant_agent_holds_everything_for_the_user(self):
        result = self.run_import(actor=AGENT, confidence=0.6)
        self.assertEqual((result["posted"], result["pending"], result["held_for_confidence"]), (0, 5, True))
        coffee = next(r for r in self.ledger.state.txns.values() if r.event.payee == "瑞幸咖啡")
        self.assertEqual(coffee.postings[1].account, "Expenses:餐饮:咖啡")  # resolved, so a plain confirm accepts it
        self.ledger.confirm(coffee.id)
        self.assertEqual(self.bal("Expenses:餐饮:咖啡"), D(38))


class Rules(FileCase):
    def test_add_rule_appends_and_keeps_the_mapping_valid(self):
        self.mapping()
        label = add_rule(self.ledger, "alipay-demo", match="美团", account="外卖")
        self.assertEqual(label, "rule #3")
        m = load_mapping(self.ledger, "alipay-demo")
        self.assertEqual([(r.text, r.account) for r in m.rules][-1], ("美团", "Expenses:餐饮:外卖"))  # stored as the full name
        add_rule(self.ledger, "alipay-demo", match="海底.*", account=None, regex=True, field_name="payee")
        last = load_mapping(self.ledger, "alipay-demo").rules[-1]
        self.assertEqual((last.regex, last.field, last.account), (True, "payee", None))

    def test_a_bad_rule_changes_nothing(self):
        self.mapping()
        path = mapping_path(self.ledger, "alipay-demo")
        before = path.read_text(encoding="utf-8")
        for kwargs, code in (
            (dict(match="x", account="不存在"), "unresolved_account"),
            (dict(match="", account="外卖"), "invalid_mapping"),
            (dict(match="(", account="外卖", regex=True), "invalid_mapping"),
            (dict(match="x", account="外卖", field_name="colour"), "invalid_mapping"),
        ):
            self.assertEqual(err(add_rule, self.ledger, "alipay-demo", **kwargs).code, code, kwargs)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(err(add_rule, self.ledger, "nope", match="x", account="外卖").code, "unknown_mapping")

    def test_saving_validates_and_names_are_safe(self):
        e = err(save_mapping, self.ledger, "bad", "[columns")
        self.assertEqual(e.code, "invalid_mapping")
        self.assertEqual(list_mappings(self.ledger), [])
        for name in ("../escape", "a/b", "", "x y"):
            self.assertEqual(err(save_mapping, self.ledger, name, ALIPAY_TOML).code, "unknown_mapping", name)
        save_mapping(self.ledger, "good", ALIPAY_TOML)
        save_mapping(self.ledger, "also-good_1.v2", ALIPAY_TOML)
        self.assertEqual(list_mappings(self.ledger), ["also-good_1.v2", "good"])
        self.assertEqual(err(load_mapping, self.ledger, "missing").code, "unknown_mapping")
        self.assertIn("good", err(load_mapping, self.ledger, "missing").message)

    def test_a_mapping_can_also_be_given_as_a_path(self):
        path = self.dir / "elsewhere.toml"
        path.write_text(ALIPAY_TOML, encoding="utf-8")
        self.assertEqual(load_mapping(self.ledger, str(path)).name, "elsewhere")  # the file name is the name


class Rechecking(FileCase):
    def test_new_rules_settle_the_waiting_rows(self):
        self.run_import()
        add_rule(self.ledger, "alipay-demo", match="美团", account="外卖")
        add_rule(self.ledger, "alipay-demo", match="某某商贸", account="日用")
        result = recheck(self.ledger, "alipay-demo")
        self.assertEqual((result["resolved"], result["still_pending"], result["written"]), (3, [], True))
        self.assertEqual(self.ledger.state.pending(), [])
        self.assertEqual(self.bal("Expenses:餐饮:外卖"), D("105.00"))
        self.assertEqual(self.bal("Expenses:购物:日用"), D("1299.00"))
        self.assertEqual(self.ledger.check(), [])

    def test_rows_without_a_rule_stay_and_are_listed(self):
        self.run_import()
        add_rule(self.ledger, "alipay-demo", match="美团", account="外卖")
        result = recheck(self.ledger, "alipay-demo")
        self.assertEqual(result["resolved"], 2)
        self.assertEqual([(u["payee"], u["count"]) for u in result["still_pending"]], [("某某商贸有限公司", 1)])

    def test_dry_run_and_other_mappings_are_left_alone(self):
        self.run_import()
        add_rule(self.ledger, "alipay-demo", match="美团", account="外卖")
        before = self.event_files()
        self.assertEqual(recheck(self.ledger, "alipay-demo", dry_run=True)["resolved"], 2)
        self.assertEqual(self.event_files(), before)
        self.mapping(ALIPAY_TOML, "other")
        add_rule(self.ledger, "other", match="美团", account="外卖")
        self.assertEqual(recheck(self.ledger, "other")["resolved"], 0)  # those rows came from another mapping

    def test_amounts_and_directions_survive(self):
        self.run_import()
        add_rule(self.ledger, "alipay-demo", match="美团", account="外卖")
        recheck(self.ledger, "alipay-demo")
        meituan = [r for r in self.ledger.state.txns.values() if r.event.payee == "美团"]
        for rec in meituan:
            self.assertEqual([(p.account, p.amount) for p in rec.postings],
                             [("Assets:支付宝", D("-52.50")), ("Expenses:餐饮:外卖", D("52.50"))])


class Inspecting(FileCase):
    def test_the_gbk_alipay_like_file(self):
        info = inspect_statement(self.csv())
        self.assertEqual((info["encoding"], info["delimiter"], info["header_line"], info["preamble_lines"]), ("gb18030", ",", 4, 3))
        self.assertEqual(info["suggested"]["columns"], {
            "date": "交易时间", "direction": "收/支", "id": "交易订单号", "payee": "交易对方", "description": "商品说明", "amount": "金额",
        })
        self.assertEqual((info["suggested"]["date_format"], info["suggested"]["sign"]), ("%Y-%m-%d %H:%M:%S", "unsigned"))
        self.assertTrue(any("direction column" in n for n in info["notes"]))
        self.assertEqual(len(info["sample_rows"]), 6)
        self.assertEqual(info["sample_rows"][0]["交易对方"], "瑞幸咖啡")

    def table(self, text, name="t.csv"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return inspect_statement(path)

    def test_day_first_or_month_first_dates_are_not_guessed(self):
        info = self.table("Date,Description,Amount\n03/04/2026,Coffee,-3.50\n05/06/2026,Lunch,-12.00\n07/08/2026,Taxi,-9.00\n")
        self.assertIsNone(info["suggested"]["date_format"])
        self.assertTrue(any("day-first or month-first" in n for n in info["notes"]))
        clear = self.table("Date,Description,Amount\n23/04/2026,Coffee,-3.50\n05/06/2026,Lunch,-12.00\n", "u.csv")
        self.assertEqual(clear["suggested"]["date_format"], "%d/%m/%Y")  # 23 can only be a day
        self.assertFalse(any("month-first" in n for n in clear["notes"]))

    def test_sign_conventions_are_flagged_not_decided(self):
        both = self.table("Date,Description,Amount\n2026-04-03,Coffee,-3.50\n2026-04-05,Salary,1000.00\n2026-04-06,Taxi,-9.00\n")
        self.assertIsNone(both["suggested"]["sign"])
        self.assertTrue(any("which sign means money INTO" in n for n in both["notes"]))
        one = self.table("Date,Description,Amount\n2026-04-03,Coffee,3.50\n2026-04-05,Lunch,12.00\n2026-04-06,Taxi,9.00\n", "o.csv")
        self.assertTrue(any("same sign" in n for n in one["notes"]))

    def test_two_amount_columns_and_a_balance(self):
        info = self.table("日期,摘要,支出金额,收入金额,余额\n2026-04-03,咖啡,3.50,,996.50\n2026-04-05,工资,,1000.00,1996.50\n2026-04-06,打车,9.00,,1987.50\n")
        cols = info["suggested"]["columns"]
        self.assertEqual((cols["amount_out"], cols["amount_in"], cols["balance"]), ("支出金额", "收入金额", "余额"))
        self.assertNotIn("amount", cols)
        self.assertEqual(info["suggested"]["sign"], "inflow_positive")

    def test_not_a_table(self):
        path = self.dir / "prose.txt"
        path.write_text("just some words\nand more words\n", encoding="utf-8")
        self.assertEqual(err(inspect_statement, path).code, "invalid_statement")


class Cli(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        self.files = self.dir / "files"
        self.files.mkdir()
        self.statement = self.files / "alipay.csv"
        lines = PREAMBLE + [HEADER] + ALIPAY_ROWS + FOOTER
        self.statement.write_bytes(("\r\n".join(lines) + "\r\n").encode("gb18030"))
        self.toml = self.files / "alipay-demo.toml"
        self.toml.write_text(ALIPAY_TOML, encoding="utf-8")

    def run_import(self, *extra, expect=0):
        return self.js("import", "run", str(self.statement), "--map", "alipay-demo", "--account", "支付宝", *extra, expect=expect)

    def test_the_whole_workflow(self):
        info = self.js("import", "inspect", str(self.statement))["data"]
        self.assertEqual(info["encoding"], "gb18030")
        self.assertEqual(self.js("import", "save-map", "alipay-demo", str(self.toml))["data"]["name"], "alipay-demo")
        self.assertEqual(self.js("import", "maps")["data"], ["alipay-demo"])
        dry = self.run_import("--dry-run")["data"]
        self.assertEqual((dry["written"], dry["imported"]), (False, 5))
        done = self.run_import()["data"]
        self.assertEqual((done["posted"], done["pending"], done["written"]), (2, 3, True))
        self.assertEqual(done["unresolved_payees"][0]["payee"], "美团")
        self.assertEqual(self.run_import()["data"]["duplicates_skipped"], 5)
        self.assertEqual(len(self.js("import", "rule-list", "--map", "alipay-demo")["data"]), 2)
        self.js("import", "rule-add", "--map", "alipay-demo", "--match", "美团", "--account", "外卖")
        self.js("import", "rule-add", "--map", "alipay-demo", "--match", "某某商贸", "--account", "日用")
        rechecked = self.js("import", "recheck", "--map", "alipay-demo")["data"]
        self.assertEqual((rechecked["resolved"], rechecked["still_pending"]), (3, []))
        self.assertEqual(self.js("pending")["data"], [])
        self.assertTrue(self.js("check")["data"]["ok"])

    def test_human_output(self):
        self.js("import", "save-map", "alipay-demo", str(self.toml))
        out, _ = self.run_cli("--ledger", str(self.root), "import", "run", str(self.statement), "--map", "alipay-demo", "--account", "支付宝")
        self.assertIn("Imported 5 of 5 rows from alipay.csv into Assets:支付宝", out)
        self.assertIn("posted 2, pending 3", out)
        self.assertIn("e.g. line 12: 共7笔记录", out)
        self.assertIn("Ask the user a category for these payees", out)
        self.assertIn("import rule-add --map alipay-demo", out)
        out, _ = self.run_cli("--ledger", str(self.root), "import", "inspect", str(self.statement))
        self.assertIn("looks like date: 交易时间", out)
        self.assertIn("This is a suggestion", out)
        out, _ = self.run_cli("--ledger", str(self.root), "import", "rule-list", "--map", "alipay-demo")
        self.assertIn("瑞幸", out)

    def test_errors(self):
        self.assertEqual(self.run_import(expect=1)["error"]["code"], "unknown_mapping")
        self.js("import", "save-map", "alipay-demo", str(self.toml))
        bad = self.js("import", "rule-add", "--map", "alipay-demo", "--match", "x", expect=2)
        self.assertEqual(bad["error"]["code"], "usage_error")
        both = self.js("import", "rule-add", "--map", "alipay-demo", "--match", "x", "--account", "外卖", "--skip", expect=2)
        self.assertEqual(both["error"]["code"], "usage_error")
        self.assertEqual(self.js("import", "save-map", "bad", "-", stdin="[columns", expect=1)["error"]["code"], "invalid_mapping")
        agent = self.run_import("--actor", "agent:t", expect=1)
        self.assertEqual(agent["error"]["code"], "confidence_required")
        self.assertEqual(self.run_import("--actor", "agent:t", "--confidence", "0.95")["data"]["posted"], 2)

    def test_save_map_from_stdin_and_since(self):
        self.assertEqual(self.js("import", "save-map", "from-stdin", "-", stdin=ALIPAY_TOML)["data"]["name"], "from-stdin")
        data = self.js("import", "run", str(self.statement), "--map", "from-stdin", "--account", "支付宝", "--since", "2026-09-08")["data"]
        self.assertEqual((data["imported"], data["out_of_range"]), (2, 3))

    def test_a_forced_encoding_that_is_wrong_is_reported(self):
        self.js("import", "save-map", "alipay-demo", str(self.toml))
        data = self.run_import("--encoding", "ascii", expect=1)
        self.assertEqual(data["error"]["code"], "invalid_statement")


if __name__ == "__main__":
    unittest.main()
