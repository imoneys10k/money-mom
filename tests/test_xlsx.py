"""Reading .xlsx workbooks and the regex form of [[skip]].

The workbooks here are built in memory with the standard library, in the same shape as a real WeChat Pay
export: shared strings, a date style, numbers stored as plain digits, merged title rows above the header.
"""

import datetime as dt
import io
import unittest
import zipfile
from decimal import Decimal
from xml.sax.saxutils import escape

from money_mom import LedgerError
from money_mom.importing import inspect_statement, parse_mapping, read_statement, run_import
from money_mom.templates import apply_template

from helpers import HUMAN, LedgerTestCase

D = Decimal
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
STYLES = (
    f'<styleSheet xmlns="{NS}"><numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/>'
    '<numFmt numFmtId="165" formatCode="¥#,##0.00"/></numFmts><cellXfs count="4">'
    '<xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="0"/><xf numFmtId="165"/></cellXfs></styleSheet>'
)
MAPPING = """
ccy = "CNY"
[columns]
date = "交易时间"
amount = "金额(元)"
direction = "收/支"
payee = "交易对方"
id = "交易单号"
[format]
date = "%Y-%m-%d %H:%M:%S"
sign = "unsigned"
[direction]
in = ["收入"]
out = ["支出"]
ignore = ["/"]
[[skip]]
column = "支付方式"
regex = '\\(\\d{4}\\)$'
"""
HEADER = ["交易时间", "交易类型", "交易对方", "收/支", "金额(元)", "支付方式", "交易单号"]
ID1, ID2 = "4200000000000000000000000001", "4200000000000000000000000002"


def serial(moment: dt.datetime) -> str:
    return repr((moment - dt.datetime(1899, 12, 30)).total_seconds() / 86400)


def workbook(rows, *, extra_part=None, doctype=""):
    """rows: list of lists; a float is stored as a number (dates with style 1, money with style 3), a str as text."""
    strings: list[str] = []

    def shared(text):
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    sheet_rows = []
    for number, row in enumerate(rows, start=1):
        cells = []
        row = row or []
        for column, value in enumerate(row):
            ref = "ABCDEFGHIJ"[column] + str(number)
            if value is None:
                continue
            if isinstance(value, dt.datetime):
                cells.append(f'<c r="{ref}" s="1"><v>{serial(value)}</v></c>')
            elif isinstance(value, Decimal):
                cells.append(f'<c r="{ref}" s="3"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="s"><v>{shared(value)}</v></c>')
        if cells:
            sheet_rows.append(f'<row r="{number}">{"".join(cells)}</row>')
    sst = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("xl/workbook.xml", f'{doctype}<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="a" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{NS}"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}">{sst}</sst>')
        z.writestr("xl/styles.xml", STYLES)
        if extra_part:
            z.writestr(*extra_part)
    return out.getvalue()


ROWS = [
    ["微信支付账单明细"],
    None,
    HEADER,
    [dt.datetime(2026, 9, 3, 12, 34, 56), "商户消费", "瑞幸咖啡", "支出", Decimal("18.5"), "招商银行信用卡(1234)", ID1],
    [dt.datetime(2026, 9, 7, 19, 0, 0), "扫二维码付款", "菜市场", "支出", Decimal("30"), "零钱", ID2],
    [dt.datetime(2026, 9, 8, 0, 0, 0), "微信红包", "小王", "收入", Decimal("200.01"), "/", "1000000000000000000000000003"],
]


class Xlsx(LedgerTestCase):
    def setUp(self):
        super().setUp()
        apply_template(self.ledger, "cn", "2026-01-01", HUMAN)
        self.dir = self.root.parent / "files"
        self.dir.mkdir()
        self.mapping = parse_mapping(MAPPING, "wx")

    def write(self, data, name="bill.xlsx"):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_dates_numbers_and_long_ids_come_through_exactly(self):
        st = read_statement(self.write(workbook(ROWS)), self.mapping)
        self.assertEqual(st.encoding, "xlsx")
        self.assertEqual(st.header_line, 3)  # the row number in the spreadsheet
        self.assertEqual([(r.date, r.amount, r.payee, r.id) for r in st.rows], [
            (dt.date(2026, 9, 7), D("-30"), "菜市场", ID2),
            (dt.date(2026, 9, 8), D("200.01"), "小王", "1000000000000000000000000003"),
        ])
        self.assertEqual(dict(st.ignored), {"skipped by [[skip]]": 1})

    def test_the_time_of_day_survives_a_float_round_trip(self):
        st = read_statement(self.write(workbook(ROWS)), self.mapping)
        self.assertEqual(st.rows[0].raw["交易时间"], "2026-09-07 19:00:00")

    def test_midnight_keeps_its_time_because_the_cell_is_formatted_with_one(self):
        st = read_statement(self.write(workbook(ROWS)), self.mapping)
        self.assertEqual(st.rows[1].raw["交易时间"], "2026-09-08 00:00:00")

    def test_inspect_reads_a_workbook_directly(self):
        info = inspect_statement(self.write(workbook(ROWS)))
        self.assertEqual((info["encoding"], info["header_line"], info["rows"]), ("xlsx", 3, 3))
        self.assertEqual(info["suggested"]["columns"]["date"], "交易时间")
        self.assertEqual(info["sample_rows"][0]["交易时间"], "2026-09-03 12:34:56")

    def test_importing_a_workbook_is_repeatable(self):
        path = self.write(workbook(ROWS))
        first = run_import(self.ledger, path, self.mapping, account="微信")
        self.assertEqual((first["rows_read"], first["pending"]), (2, 2))
        again = run_import(self.ledger, path, self.mapping, account="微信")
        self.assertEqual(again["duplicates_skipped"], 2)

    def test_a_workbook_that_declares_entities_is_refused(self):
        evil = workbook(ROWS, doctype='<!DOCTYPE x [<!ENTITY a "aaaa">]>')
        with self.assertRaises(LedgerError) as ctx:
            read_statement(self.write(evil), self.mapping)
        self.assertEqual(ctx.exception.code, "invalid_statement")

    def test_a_broken_workbook_is_an_invalid_statement_not_a_crash(self):
        with self.assertRaises(LedgerError) as ctx:
            read_statement(self.write(b"PK\x03\x04 not really a zip"), self.mapping)
        self.assertEqual(ctx.exception.code, "invalid_statement")

    def test_a_csv_with_an_xlsx_name_is_still_read_as_text(self):
        csv_text = "交易时间,收/支,金额(元),交易对方,交易单号,支付方式\n2026-09-07 19:00:00,支出,30,菜市场,A1,零钱\n"
        st = read_statement(self.write(csv_text.encode("utf-8"), "plain.xlsx"), self.mapping)
        self.assertEqual([r.payee for r in st.rows], ["菜市场"])


class RegexSkip(unittest.TestCase):
    BASE = '[columns]\ndate = "d"\namount = "a"\n[format]\ndate = "%Y-%m-%d"\n'

    def test_a_skip_needs_exactly_one_of_values_or_regex(self):
        for body in ('column = "x"\n', 'column = "x"\nvalues = ["a"]\nregex = "b"\n'):
            with self.assertRaises(LedgerError) as ctx:
                parse_mapping(self.BASE + "[[skip]]\n" + body, "m")
            self.assertEqual(ctx.exception.code, "invalid_mapping")

    def test_a_bad_regex_is_an_invalid_mapping(self):
        with self.assertRaises(LedgerError) as ctx:
            parse_mapping(self.BASE + '[[skip]]\ncolumn = "x"\nregex = "("\n', "m")
        self.assertIn("regular expression", str(ctx.exception))

    def test_values_still_match_exactly_and_the_regex_is_a_search_ignoring_case(self):
        mapping = parse_mapping(
            self.BASE + '[[skip]]\ncolumn = "s"\nvalues = ["closed"]\n[[skip]]\ncolumn = "m"\nregex = "card"\n', "m"
        )
        text = "d,a,s,m\n2026-09-01,1,closed,cash\n2026-09-02,2,open,Credit CARD\n2026-09-03,3,closed2,cash\n"
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "x.csv"
            path.write_text(text, encoding="utf-8")
            st = read_statement(path, mapping)
        self.assertEqual([r.amount for r in st.rows], [D("3")])


if __name__ == "__main__":
    unittest.main()
