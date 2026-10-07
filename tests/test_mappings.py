"""The draft Alipay and WeChat mappings shipped with the skill.

The fixtures below are SYNTHETIC reconstructions of the export layouts, written from memory of how those
files look. They prove the drafts are internally consistent and fail safe; they do not prove that the drafts
match what Alipay or WeChat really export, which is why the drafts say so at the top.
"""

import datetime as dt
import unittest
from decimal import Decimal
from pathlib import Path

from money_mom import LedgerError
from money_mom.importing import parse_mapping, read_statement, run_import, save_mapping, load_mapping
from money_mom.templates import apply_template

from helpers import HUMAN, LedgerTestCase
from test_cli import CliTestCase

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
MAPPINGS = ROOT / "skills" / "money-mom" / "references" / "mappings"
full_checkout_only = unittest.skipUnless((MAPPINGS / "alipay.toml").is_file(), "needs the whole repository")

ALIPAY_PREAMBLE = [
    "------------------------------------------------------------------------------------",
    "导出信息：",
    "姓名：测试用户",
    "支付宝账户：demo@example.com",
    "起始时间：[2026-09-01 00:00:00]    终止时间：[2026-09-30 23:59:59]",
    "导出交易类型：[全部]",
    "导出时间：[2026-10-01 10:00:00]",
    "",
    "共6笔记录",
    "收入：1笔  200.00元",
    "支出：4笔  1,176.50元",
    "不计收支：1笔  500.00元",
    "",
    "特别提示：本明细为示例。",
    "------------------------支付宝（中国）网络技术有限公司  电子客户回单------------------------",
]
ALIPAY_HEADER = "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注,"
ALIPAY_ROWS = [
    "2026-09-03 08:12:01,餐饮美食,瑞幸咖啡,lux***@example.com,生椰拿铁,支出,18.00,招商银行信用卡(1234),交易成功,2026090322001400000000000001\t,M0001\t,,",
    "2026-09-05 12:30:10,餐饮美食,美团,mei***@example.com,外卖订单,支出,52.50,余额,交易成功,2026090522001400000000000002\t,M0002\t,,",
    "2026-09-08 09:00:00,转账红包,小王,wang***@example.com,还钱,收入,200.00,余额,交易成功,2026090822001400000000000003\t,,,",
    "2026-09-10 10:00:00,充值提现,微信,,充值,不计收支,500.00,余额,交易成功,2026091022001400000000000004\t,,,",
    "2026-09-12 10:00:00,餐饮美食,海底捞,hai***@example.com,聚餐,支出,486.00,余额,交易关闭,2026091222001400000000000005\t,M0005\t,,",
    "2026-09-20 21:00:00,日用百货,某某商贸有限公司,mou***@example.com,日用品,支出,1106.00,余额,交易成功,2026092022001400000000000006\t,M0006\t,,",
]

WECHAT_PREAMBLE = [
    "微信支付账单明细,,,,,,,,,,",
    "微信昵称：[测试用户],,,,,,,,,,",
    "起始时间：[2026-09-01 00:00:00] 终止时间：[2026-09-30 23:59:59],,,,,,,,,,",
    "导出类型：[全部],,,,,,,,,,",
    "导出时间：[2026-10-01 10:00:00],,,,,,,,,,",
    ",,,,,,,,,,",
    "共5笔记录,,,,,,,,,,",
    "收入：1笔，金额合计200.00元,,,,,,,,,,",
    "支出：3笔，金额合计88.00元,,,,,,,,,,",
    "中性交易：1笔，金额合计500.00元,,,,,,,,,,",
    "注：本明细为示例。,,,,,,,,,,",
    ",,,,,,,,,,",
    "----------------------微信支付账单明细列表--------------------,,,,,,,,,,",
]
WECHAT_HEADER = "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注"
WECHAT_ROWS = [
    "2026-09-03 12:34:56,商户消费,瑞幸咖啡,生椰拿铁,支出,¥18.00,招商银行信用卡(1234),支付成功,4200000000000000000000000001\t,M0001\t,/",
    "2026-09-06 18:00:00,微信红包,小王,/,收入,¥200.00,/,已收钱,1000000000000000000000000002\t,/,/",
    "2026-09-07 19:00:00,扫二维码付款,菜市场,蔬菜,支出,¥30.00,零钱,支付成功,4200000000000000000000000003\t,M0003\t,/",
    "2026-09-09 08:00:00,零钱充值,招商银行,/,/,¥500.00,招商银行(1234),充值完成,4200000000000000000000000004\t,/,/",
    "2026-09-15 20:00:00,商户消费,电影院,电影票,支出,¥40.00,零钱,支付成功,4200000000000000000000000005\t,M0005\t,/",
]


class DraftCase(LedgerTestCase):
    def setUp(self):
        super().setUp()
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)
        self.dir = self.root.parent / "files"
        self.dir.mkdir()

    def alipay_file(self, rows=ALIPAY_ROWS, name="alipay.csv"):
        path = self.dir / name
        lines = ALIPAY_PREAMBLE + [ALIPAY_HEADER] + list(rows)
        path.write_bytes(("\r\n".join(lines) + "\r\n").encode("gb18030"))
        return path

    def wechat_file(self, rows=WECHAT_ROWS, name="wechat.csv"):
        path = self.dir / name
        lines = WECHAT_PREAMBLE + [WECHAT_HEADER] + list(rows)
        path.write_bytes(("﻿" + "\r\n".join(lines) + "\r\n").encode("utf-8"))
        return path

    def draft(self, name):
        text = (MAPPINGS / f"{name}.toml").read_text(encoding="utf-8")
        save_mapping(self.ledger, name, text)
        return load_mapping(self.ledger, name)


@full_checkout_only
class Shipped(unittest.TestCase):
    def test_both_drafts_are_valid_mappings(self):
        for name in ("alipay", "wechat"):
            mapping = parse_mapping((MAPPINGS / f"{name}.toml").read_text(encoding="utf-8"), name)
            self.assertEqual((mapping.ccy, mapping.sign), ("CNY", "unsigned"))
            self.assertEqual(mapping.rules, ())  # no categories are guessed

    def test_the_alipay_draft_says_at_the_very_top_that_it_is_unverified(self):
        first = (MAPPINGS / "alipay.toml").read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("NOT VERIFIED", first)
        self.assertIn("未经真实导出文件验证", first)

    def test_the_wechat_mapping_says_what_it_was_checked_against_and_that_it_is_one_sample(self):
        text = (MAPPINGS / "wechat.toml").read_text(encoding="utf-8")
        self.assertNotIn("NOT VERIFIED", text.splitlines()[0])
        self.assertIn("one real WeChat Pay export", text.splitlines()[0])
        self.assertIn("only one sample", text)

    def test_the_skill_calls_them_drafts(self):
        text = (ROOT / "skills/money-mom/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/mappings/alipay.toml", text)
        self.assertIn("references/mappings/wechat.toml", text)
        self.assertIn("not verified", text.lower())


@full_checkout_only
class Alipay(DraftCase):
    def test_a_file_shaped_like_the_export_is_read(self):
        st = read_statement(self.alipay_file(), self.draft("alipay"))
        self.assertEqual(st.encoding, "gb18030")
        self.assertEqual(st.header_line, len(ALIPAY_PREAMBLE) + 1)
        self.assertEqual([(r.date, r.amount, r.payee) for r in st.rows], [
            (dt.date(2026, 9, 3), D("-18.00"), "瑞幸咖啡"),
            (dt.date(2026, 9, 5), D("-52.50"), "美团"),
            (dt.date(2026, 9, 8), D("200.00"), "小王"),
            (dt.date(2026, 9, 20), D("-1106.00"), "某某商贸有限公司"),
        ])
        self.assertEqual(st.rows[0].id, "2026090322001400000000000001")  # the trailing tab is gone
        self.assertEqual(st.rows[0].description, "生椰拿铁")
        self.assertEqual(dict(st.ignored), {"direction '不计收支'": 1, "skipped by [[skip]]": 1})

    def test_a_direction_it_does_not_know_stops_everything_and_names_the_line(self):
        odd = ALIPAY_ROWS[0].replace(",支出,", ",冻结,")
        with self.assertRaises(LedgerError) as ctx:
            read_statement(self.alipay_file([odd]), self.draft("alipay"))
        self.assertEqual(ctx.exception.code, "invalid_statement")
        self.assertIn(f"line {len(ALIPAY_PREAMBLE) + 2}", str(ctx.exception))
        self.assertIn("冻结", str(ctx.exception))

    def test_a_different_header_is_refused_not_misread(self):
        path = self.dir / "other.csv"
        path.write_bytes("日期,金额,对方\r\n2026-09-01,10,某人\r\n".encode("gb18030"))
        with self.assertRaises(LedgerError) as ctx:
            read_statement(path, self.draft("alipay"))
        self.assertEqual(ctx.exception.code, "invalid_statement")

    def test_importing_is_exact_and_repeatable(self):
        mapping = self.draft("alipay")
        first = run_import(self.ledger, self.alipay_file(), mapping, account="支付宝")
        self.assertEqual((first["rows_read"], first["posted"], first["pending"]), (4, 0, 4))  # no rules: all wait
        self.assertEqual(self.ledger.state.balance_of("Assets:支付宝").get("CNY", D(0)), D(0))  # pending rows move nothing
        again = run_import(self.ledger, self.alipay_file(name="again.csv"), mapping, account="支付宝")
        self.assertEqual((again["posted"], again["pending"], again["duplicates_skipped"]), (0, 0, 4))

    def test_with_rules_the_balance_follows_the_statement(self):
        mapping = self.draft("alipay")
        text = (MAPPINGS / "alipay.toml").read_text(encoding="utf-8") + (
            '\n[[rules]]\nmatch = "瑞幸"\naccount = "咖啡"\n'
            '\n[[rules]]\nmatch = "美团"\naccount = "餐饮"\n'
            '\n[[rules]]\nmatch = "小王"\naccount = "应收"\n'
            '\n[[rules]]\nmatch = "商贸"\naccount = "购物"\n')
        save_mapping(self.ledger, "alipay", text)
        mapping = load_mapping(self.ledger, "alipay")
        result = run_import(self.ledger, self.alipay_file(), mapping, account="支付宝")
        self.assertEqual((result["posted"], result["pending"]), (4, 0))
        self.assertEqual(self.ledger.state.balance_of("Assets:支付宝").get("CNY", D(0)), D("-976.50"))


@full_checkout_only
class WeChat(DraftCase):
    def test_a_file_shaped_like_the_export_is_read(self):
        st = read_statement(self.wechat_file(), self.draft("wechat"))
        self.assertEqual(st.encoding, "utf-8-sig")
        self.assertEqual(st.header_line, len(WECHAT_PREAMBLE) + 1)
        # the card payment (招商银行信用卡) and the card top-up are left to the card's own statement
        self.assertEqual([(r.date, r.amount, r.payee) for r in st.rows], [
            (dt.date(2026, 9, 6), D("200.00"), "小王"),
            (dt.date(2026, 9, 7), D("-30.00"), "菜市场"),
            (dt.date(2026, 9, 15), D("-40.00"), "电影院"),
        ])
        self.assertEqual(st.rows[0].id, "1000000000000000000000000002")
        self.assertEqual(dict(st.ignored), {"skipped by [[skip]]": 2})

    def test_rows_paid_by_any_card_are_skipped_and_wallet_rows_are_kept(self):
        rows = [
            "2026-09-03 12:00:00,商户消费,甲,a,支出,¥1.00,中国银行储蓄卡(3564),支付成功,4200000000000000000000000021\t,M1\t,/",
            "2026-09-03 12:01:00,商户消费,乙,b,支出,¥2.00,MASTERCARD(2061),支付成功,4200000000000000000000000022\t,M2\t,/",
            "2026-09-03 12:02:00,商户消费,丙,c,支出,¥3.00,民生银行信用卡(5554),支付成功,4200000000000000000000000023\t,M3\t,/",
            "2026-09-03 12:03:00,商户消费,丁,d,支出,¥4.00,零钱,支付成功,4200000000000000000000000024\t,M4\t,/",
            "2026-09-03 12:04:00,商户消费,戊,e,支出,¥5.00,零钱通,支付成功,4200000000000000000000000025\t,M5\t,/",
            "2026-09-03 12:05:00,微信红包,己,/,收入,¥6.00,/,已存入零钱,1000000000000000000000000026\t,/,/",
        ]
        st = read_statement(self.wechat_file(rows), self.draft("wechat"))
        self.assertEqual([r.payee for r in st.rows], ["丁", "戊", "己"])
        self.assertEqual(dict(st.ignored), {"skipped by [[skip]]": 3})

    def test_a_direction_it_does_not_know_stops_everything(self):
        odd = WECHAT_ROWS[2].replace(",支出,", ",待定,")
        with self.assertRaises(LedgerError) as ctx:
            read_statement(self.wechat_file([odd]), self.draft("wechat"))
        self.assertIn("待定", str(ctx.exception))

    def test_importing_is_exact_and_repeatable(self):
        mapping = self.draft("wechat")
        first = run_import(self.ledger, self.wechat_file(), mapping, account="微信")
        self.assertEqual((first["rows_read"], first["pending"]), (3, 3))
        again = run_import(self.ledger, self.wechat_file(name="again.csv"), mapping, account="微信")
        self.assertEqual(again["duplicates_skipped"], 3)

    def test_a_refund_and_its_order_are_both_kept_so_they_net_to_zero(self):
        rows = [
            "2026-09-04 09:00:00,商户消费,电商,手机壳,支出,¥59.00,零钱,已全额退款,4200000000000000000000000010\t,M0010\t,/",
            "2026-09-05 09:00:00,退款,电商,手机壳-退款,收入,¥59.00,零钱,已退款,5000000000000000000000000011\t,M0010\t,/",
        ]
        st = read_statement(self.wechat_file(rows), self.draft("wechat"))
        self.assertEqual((len(st.rows), sum(r.amount for r in st.rows)), (2, D("0.00")))

    def test_a_card_refund_is_dropped_together_with_its_card_order(self):
        rows = [
            "2026-09-04 09:00:00,商户消费,电商,手机壳,支出,¥59.00,中国银行储蓄卡(3564),已全额退款,4200000000000000000000000012\t,M0012\t,/",
            "2026-09-05 09:00:00,退款,电商,手机壳-退款,收入,¥59.00,中国银行储蓄卡(3564),已退款,5000000000000000000000000013\t,M0012\t,/",
        ]
        st = read_statement(self.wechat_file(rows), self.draft("wechat"))
        self.assertEqual(st.rows, [])

    def test_the_card_skip_is_one_regex_rule_not_a_list_of_personal_card_numbers(self):
        mapping = self.draft("wechat")
        self.assertEqual([(c, v) for c, v, _ in mapping.skip_when], [("支付方式", ())])
        self.assertIsNotNone(mapping.skip_when[0][2])


@full_checkout_only
class Cli(CliTestCase):
    def test_the_save_command_in_the_draft_works_as_written(self):
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        for name in ("alipay", "wechat"):
            text = (MAPPINGS / f"{name}.toml").read_text(encoding="utf-8")
            self.assertIn(f"import save-map {name} -", text)
            self.js("import", "save-map", name, "-", stdin=text)
        self.assertEqual(self.js("import", "maps")["data"], ["alipay", "wechat"])

    def test_inspect_finds_the_header_in_both_layouts(self):
        files = Path(self.dir) / "f"
        files.mkdir()
        alipay, wechat = files / "a.csv", files / "w.csv"
        alipay.write_bytes(("\r\n".join(ALIPAY_PREAMBLE + [ALIPAY_HEADER] + ALIPAY_ROWS) + "\r\n").encode("gb18030"))
        wechat.write_bytes(("﻿" + "\r\n".join(WECHAT_PREAMBLE + [WECHAT_HEADER] + WECHAT_ROWS) + "\r\n").encode("utf-8"))
        self.js("init", "--template", "cn", "--date", "2026-01-01")
        for path, want in ((alipay, len(ALIPAY_ROWS)), (wechat, len(WECHAT_ROWS))):
            data = self.js("import", "inspect", str(path))["data"]
            self.assertIn("交易时间", data["columns_in_file"])
            self.assertEqual(data["rows"], want)
            self.assertEqual(data["suggested"]["columns"]["date"], "交易时间")
            self.assertEqual(data["suggested"]["sign"], "unsigned")
            self.assertTrue(any("direction" in n for n in data["notes"]))


if __name__ == "__main__":
    unittest.main()
