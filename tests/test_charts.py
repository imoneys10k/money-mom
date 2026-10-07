import datetime as dt
import json
import re
import unittest
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from money_mom.charts import (
    KINDS, axis_label, build_exhibits, chart_data, fit, group, key_points, nice_ticks, render, signed, sparkline, wrap,
)
from money_mom.errors import LedgerError
from money_mom.report import monthly_report, monthly_series

from helpers import HUMAN, LedgerTestCase
from test_cli import CliTestCase

TODAY = dt.date(2026, 10, 7)
D = Decimal


class Formatting(unittest.TestCase):
    def test_group_only_adds_separators(self):
        for raw, want in (("0", "0"), ("12", "12"), ("1234", "1,234"), ("-1234567.80", "-1,234,567.80"),
                          ("999.99", "999.99"), ("1000", "1,000"), ("-100", "-100"), ("0.50", "0.50")):
            self.assertEqual(group(raw), want)

    def test_signed(self):
        self.assertEqual(signed("5.00"), "+5.00")
        self.assertEqual(signed("-5.00"), "-5.00")
        self.assertEqual(signed("0.00"), "0.00")
        self.assertEqual(signed(None), "–")
        self.assertEqual(signed("12000.5"), "+12,000.5")

    def test_axis_labels_share_one_unit(self):
        zh = [axis_label(v, "zh", 25000) for v in (0, 5000, 10000, 25000)]
        self.assertEqual(zh, ["0", "0.5万", "1万", "2.5万"])
        en = [axis_label(v, "en", 25000) for v in (0, 5000, 25000)]
        self.assertEqual(en, ["0", "5k", "25k"])
        self.assertEqual(axis_label(250, "en", 500), "250")
        self.assertEqual(axis_label(-20000, "zh", 20000), "-2万")

    def test_ticks_cover_the_range_and_are_round(self):
        for lo, hi in ((0, 22000), (-500, 1200), (0, 0.9), (3, 3), (0, 1_234_567)):
            ticks = nice_ticks(lo, hi)
            self.assertLessEqual(ticks[0], lo)
            self.assertGreaterEqual(ticks[-1], hi)
            steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:])}
            self.assertEqual(len(steps), 1, (lo, hi, ticks))
            self.assertLessEqual(len(ticks), 12)
        self.assertIn(0, nice_ticks(-500, 1200))

    def test_wrapping_keeps_numbers_and_words_whole(self):
        text = "净资产 128,923.56 CNY, 较 2026-01 末 +115,182.56"
        for width in range(200, 330, 5):  # whatever the width, no number is cut in two
            lines = wrap(text, 15, width)
            self.assertLessEqual(len(lines), 2)
            if not lines[-1].endswith("…"):
                for token in ("128,923.56", "2026-01", "+115,182.56"):
                    self.assertTrue(any(token in line for line in lines), (width, token, lines))
        self.assertEqual(wrap("short", 16, 300), ["short"])
        self.assertTrue(wrap("x" * 200, 16, 100)[-1].endswith("…"))
        self.assertEqual(wrap("", 16, 100), [])

    def test_text_fitting(self):
        self.assertEqual(fit("short", 10, 200), "short")
        self.assertTrue(fit("a very long category name indeed", 10, 60).endswith("…"))
        self.assertTrue(fit("很长很长很长很长的分类名称", 10, 50).endswith("…"))
        lines = wrap("a headline that is much too long to sit on one line of a narrow exhibit", 16, 200)
        self.assertLessEqual(len(lines), 2)


class ChartCase(LedgerTestCase):
    def setUp(self):
        super().setUp()
        for account in ("Assets:CMB", "Assets:USD", "Assets:EUR", "Expenses:Dining", "Expenses:Rent", "Expenses:Travel",
                        "Expenses:Gift", "Expenses:Pets", "Expenses:Fun", "Expenses:Tax", "Expenses:Books", "Expenses:Misc",
                        "Income:Salary"):
            kw = {"currencies": ["USD"]} if account == "Assets:USD" else {"currencies": ["EUR"]} if account == "Assets:EUR" else {}
            self.ledger.open_account(account, "2026-01-01", **kw)

    def money(self, date, account, amount, ccy="CNY", payee=None, source="Assets:CMB"):
        self.ledger.add_txn(date, [(account, amount, ccy), (source, str(-D(amount)), ccy)], payee=payee)

    def salary(self, date, amount="10000"):
        self.ledger.add_txn(date, [("Assets:CMB", amount, "CNY"), ("Income:Salary", f"-{amount}", "CNY")])

    def price(self, base, quote, rate, date):
        self.ledger.append(self.ledger.new_event(
            "price", actor=HUMAN, date=date, base=base, quote=quote, rate=rate, source={"type": "manual", "ref": "t"}
        ), clamp_ts=True)

    def usual(self):
        for month in range(5, 10):
            self.salary(f"2026-{month:02d}-01")
            self.money(f"2026-{month:02d}-05", "Expenses:Rent", "3000")
            self.money(f"2026-{month:02d}-09", "Expenses:Dining", str(300 + month * 10), payee="海底捞")
        self.money("2026-09-20", "Expenses:Travel", "1234.50")

    def figures(self, month="2026-09", months=6):
        report = monthly_report(self.ledger, month, today=TODAY)
        series = monthly_series(self.ledger, months, end=month, today=TODAY)
        return report, series

    def draw(self, kind, month="2026-09", **kw):
        report, series = self.figures(month)
        kw.setdefault("generated", TODAY)
        return render(kind, report, series, **kw)


class WellFormed(ChartCase):
    def test_every_chart_in_every_variant_is_valid_xml_or_html(self):
        self.usual()
        for kind in KINDS:
            for lang in ("zh", "en"):
                for hide in (False, True):
                    if kind == "sheet":
                        out = self.draw(kind, lang=lang, hide=hide)
                        self.assertTrue(out.startswith("<!doctype html>"))
                        self.assertEqual(len(re.findall(r'<svg[^>]*role="img"', out)), 10)  # five, at desktop and phone width
                        continue
                    svg = self.draw(kind, lang=lang, hide=hide, fmt="svg")
                    root = ET.fromstring(svg)
                    self.assertTrue(root.tag.endswith("svg"), (kind, lang, hide))
                    self.assertIn("viewBox", root.attrib)
                    html = self.draw(kind, lang=lang, hide=hide, fmt="html")
                    self.assertIn("<svg", html)

    def test_inline_svgs_in_the_sheet_parse_too(self):
        self.usual()
        out = self.draw("sheet")
        for block in re.findall(r"<svg.*?</svg>", out, flags=re.S):
            ET.fromstring(block.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1))

    def test_no_script_and_nothing_loaded_from_elsewhere(self):
        self.usual()
        for kind in KINDS:
            for fmt in ("html", "svg") if kind != "sheet" else ("html",):
                out = self.draw(kind, fmt=fmt)
                self.assertNotIn("<script", out.lower())
                self.assertNotIn("onclick", out.lower())
                self.assertIsNone(re.search(r"(src|href)=", out), (kind, fmt))
                self.assertNotIn("@import", out)
                self.assertNotIn("url(", out)
                stripped = out.replace('xmlns="http://www.w3.org/2000/svg"', "")
                self.assertNotIn("http", stripped, (kind, fmt))

    def test_output_is_deterministic(self):
        self.usual()
        self.assertEqual(self.draw("sheet"), self.draw("sheet"))

    def test_the_sheet_follows_the_system_colour_scheme_and_prints(self):
        self.usual()
        out = self.draw("sheet")
        self.assertIn("prefers-color-scheme:dark", out)
        self.assertIn("@media print", out)
        self.assertIn('<meta name="viewport"', out)

    def test_user_text_is_escaped(self):
        # account names cannot contain markup, so feed the drawing code hostile text directly
        from money_mom.charts import Exhibit, esc
        hostile = '</text><script>alert(1)</script>&"\''
        self.assertEqual(esc(hostile), "&lt;/text&gt;&lt;script&gt;alert(1)&lt;/script&gt;&amp;&quot;&#x27;")
        ex = Exhibit(1, hostile, "en", hide=False)
        ex.begin(hostile)
        ex.warning = hostile
        ex.add(f'<text>{esc(hostile)}</text>')
        ex.table = ([hostile], [[hostile]])
        for text in (ex.svg(standalone=True), ex.svg(standalone=False), ex.data_table()):
            self.assertNotIn("<script", text)
            self.assertNotIn("</text><", text.replace("</text><text", "").replace("</text><line", "").replace("</text></g>", "")
                             .replace("</text><title", "").replace("</text></svg>", "").replace("</text><rect", ""))
        ET.fromstring(ex.svg(standalone=True))


class Honesty(ChartCase):
    def test_hidden_amounts_never_appear(self):
        self.usual()
        report, series = self.figures()
        secrets = set()
        for part in (report["income"]["total"], report["expenses"]["total"], report["net"], report["net_worth"]["end"]["total"]):
            secrets.add(part)
        for row in report["categories"]:
            secrets.update(x for x in (row["converted"], row["change"], row["previous"]) if x)
        for row in series["months"]:
            secrets.update(x for x in (row["income"], row["expenses"], row["net"], row["net_worth"]) if x)
        secrets = {s for s in secrets if len(s.replace("-", "").replace(".", "")) >= 4}
        self.assertTrue(secrets)
        for kind in KINDS:
            for fmt in ("html", "svg") if kind != "sheet" else ("html",):
                out = self.draw(kind, hide=True, fmt=fmt)
                for secret in secrets:
                    for form in (secret, group(secret), group(secret).lstrip("-"), signed(secret)):
                        self.assertNotIn(form, out, (kind, fmt, form))
                self.assertNotIn("<details", out)  # the data table holds exact amounts
        self.assertIn("金额已隐藏", self.draw("spending", hide=True, fmt="svg"))
        self.assertIn("Amounts hidden", self.draw("spending", hide=True, fmt="svg", lang="en"))

    def test_shown_amounts_do_appear(self):
        self.usual()
        out = self.draw("sheet")
        report, _ = self.figures()
        self.assertIn(group(report["expenses"]["total"]), out)
        self.assertIn("<details", out)

    def test_a_missing_rate_is_marked_not_drawn_as_zero(self):
        self.usual()
        self.ledger.add_txn("2026-09-12", [("Expenses:Gift", "50", "EUR"), ("Assets:EUR", "-50", "EUR")])
        report, series = self.figures()
        self.assertEqual(report["missing"], ["EUR"])
        for kind in ("spending", "change", "waterfall", "trend", "networth"):
            svg = render(kind, report, series, fmt="svg", generated=TODAY)
            self.assertIn("EUR", svg, kind)
            self.assertIn("class=\"warn\"", svg, kind)
        spending = render("spending", report, series, fmt="svg", generated=TODAY)
        self.assertNotIn("Gift", spending)  # a category that cannot be converted is not drawn as an empty bar

    def test_a_month_without_entries_is_a_gap_not_a_zero_bar(self):
        self.salary("2026-07-01")
        self.money("2026-07-05", "Expenses:Dining", "100")
        self.salary("2026-09-01")
        self.money("2026-09-05", "Expenses:Dining", "200")
        report, series = self.figures("2026-09", )
        months = {m["month"]: m for m in series["months"]}
        self.assertIsNone(months["2026-08"]["income"])
        svg = render("trend", report, series, fmt="svg", generated=TODAY)
        self.assertEqual(svg.count('class="c1"') + svg.count('class="c2"'), 4 + 2)  # 2 bars x 2 months + 2 legend swatches
        html = render("trend", report, series, fmt="html", generated=TODAY)
        self.assertIn("<td>2026-08</td><td>–</td><td>–</td><td>–</td>", html)

    def test_negative_net_and_net_worth_are_drawn_below_the_axis(self):
        self.money("2026-09-05", "Expenses:Rent", "3000", source="Assets:CMB")
        self.ledger.add_txn("2026-08-05", [("Expenses:Rent", "100", "CNY"), ("Assets:CMB", "-100", "CNY")])
        report, series = self.figures()
        self.assertTrue(report["net"].startswith("-"))
        for kind in ("waterfall", "trend", "networth"):
            ET.fromstring(render(kind, report, series, fmt="svg", generated=TODAY))
        self.assertIn("-", render("waterfall", report, series, fmt="svg", generated=TODAY))

    def test_a_single_month_cannot_draw_a_net_worth_line(self):
        self.salary("2026-09-01")
        report, series = self.figures("2026-09")
        svg = render("networth", report, monthly_series(self.ledger, 1, end="2026-09", today=TODAY), fmt="svg", generated=TODAY)
        self.assertIn("至少需要两个月", svg)
        self.assertNotIn("polyline", svg)

    def test_no_previous_month_means_no_change_chart(self):
        self.money("2026-09-05", "Expenses:Dining", "50")
        svg = self.draw("change", fmt="svg")
        self.assertIn("没有上月数据", svg)
        self.assertNotIn("<rect class=\"neg\"", svg)

    def test_an_empty_ledger_month_draws_placeholders_and_never_fails(self):
        report, series = self.figures("2026-09")
        for kind in KINDS:
            out = render(kind, report, series, fmt="html", generated=TODAY)
            self.assertIn("<svg", out)
        for kind in ("spending", "waterfall", "trend"):
            self.assertIn("没有可显示的数据", render(kind, report, series, fmt="svg", generated=TODAY))


class Sheet(ChartCase):
    def test_the_page_leads_with_the_result_then_key_points_then_the_charts(self):
        self.usual()
        out = self.draw("sheet", lang="en")
        lead, points, charts, notes = (out.index(x) for x in ('class="lead"', 'class="points"', 'class="stack"', 'class="notes"'))
        self.assertLess(lead, points)
        self.assertLess(points, charts)
        self.assertLess(charts, notes)
        report, _ = self.figures()
        self.assertIn("+" + group(report["net"]), out)
        self.assertIn("Net saved", out)

    def test_every_chart_is_drawn_at_desktop_and_at_phone_width_and_only_one_is_read_aloud(self):
        self.usual()
        out = self.draw("sheet")
        self.assertIn('viewBox="0 0 1120 380"', out)   # the wide trend chart
        self.assertIn('viewBox="0 0 548 400"', out)    # a half-width chart
        self.assertIn('viewBox="0 0 360 380"', out)    # the phone version of the trend chart
        self.assertEqual(out.count('role="img" aria-hidden="true"'), 5)  # the phone drawings are hidden from screen readers
        self.assertIn("@media (max-width:820px)", out)
        self.assertIn(".mob{display:block}", out)

    def test_a_loss_is_shown_as_overspending(self):
        self.money("2026-09-05", "Expenses:Rent", "300")
        out = self.draw("sheet", lang="en")
        self.assertIn("Overspent", out)
        self.assertIn('class="big neg"', out)

    def test_hidden_mode_masks_the_lead_and_the_kpis(self):
        self.usual()
        out = self.draw("sheet", hide=True)
        self.assertIn("••••", out)
        self.assertNotIn('class="chip', out)  # a chip states an amount
        report, _ = self.figures()
        self.assertNotIn(group(report["net"]), out)

    def test_spending_up_is_bad_and_income_up_is_good_in_the_chips(self):
        self.salary("2026-08-01", "10000")
        self.salary("2026-09-01", "12000")
        self.money("2026-08-05", "Expenses:Rent", "1000")
        self.money("2026-09-05", "Expenses:Rent", "2000")
        out = self.draw("sheet", lang="en")
        kpis = out[out.index('class="kpis"'):out.index('class="sec"')]
        income, spending = (kpis[kpis.index(f">{name}<"):] for name in ("Income", "Spending"))
        self.assertRegex(income[:400], r'chip good">▲')
        self.assertRegex(spending[:400], r'chip bad">▲')

    def test_the_sparkline_draws_gaps_and_marks_the_last_point(self):
        svg = sparkline([1.0, 2.0, None, 3.0, 4.0, 2.0])
        self.assertEqual(svg.count("<polyline"), 2)
        self.assertEqual(svg.count("<circle"), 1)
        self.assertEqual(sparkline([1.0]), "")
        self.assertEqual(sparkline([None, None, 3.0]), "")
        flat = sparkline([5.0, 5.0, 5.0])
        self.assertIn("polyline", flat)  # a flat line is still a line, not a division by zero

    def test_key_points_say_what_happened_and_what_to_doubt(self):
        self.usual()
        self.ledger.add_txn("2026-09-25", [("Expenses:Dining", "9", "CNY"), (None, "-9", "CNY")], status="pending")
        report, _ = self.figures()
        points = key_points(report, "en", False)
        kinds = [k for k, _ in points]
        text = " ".join(t for _, t in points)
        self.assertIn("up", kinds)
        self.assertIn("Saved", text)
        self.assertIn("Rent", text)
        self.assertIn("Pending entries not counted: 1", text)
        hidden = " ".join(t for _, t in key_points(report, "en", True))
        self.assertNotIn(group(report["net"]), hidden)
        self.assertNotIn("CNY", hidden)

    def test_more_spending_is_a_down_point_and_less_spending_is_an_up_point(self):
        self.money("2026-08-05", "Expenses:Dining", "100")
        self.money("2026-08-06", "Expenses:Rent", "500")
        self.money("2026-09-05", "Expenses:Dining", "300")   # +200
        self.money("2026-09-06", "Expenses:Rent", "450")     # -50
        report, _ = self.figures()
        by_text = {text: kind for kind, text in key_points(report, "en", False)}
        spending = next(k for text, k in by_text.items() if text.startswith("Spending is up"))
        category = next(k for text, k in by_text.items() if text.startswith("Dining is up"))
        self.assertEqual((spending, category), ("down", "down"))
        # and the other way round
        self.ledger.void(next(r.id for r in self.ledger.state.txns.values() if r.date == dt.date(2026, 9, 5)), "undo")
        self.money("2026-09-07", "Expenses:Dining", "20")
        report, _ = self.figures()
        by_text = {text: kind for kind, text in key_points(report, "en", False)}
        self.assertEqual(next(k for text, k in by_text.items() if text.startswith("Spending is down")), "up")
        self.assertEqual(next(k for text, k in by_text.items() if text.startswith("Dining is down")), "up")

    def test_key_points_flag_an_unfinished_month_and_missing_rates(self):
        self.usual()
        self.ledger.add_txn("2026-10-02", [("Expenses:Gift", "5", "EUR"), ("Assets:EUR", "-5", "EUR")])
        report = monthly_report(self.ledger, "2026-10", today=TODAY)
        text = " ".join(t for _, t in key_points(report, "en", False))
        self.assertIn("month to date", text)
        self.assertIn("EUR", text)

    def test_no_key_points_are_invented_for_an_empty_month(self):
        report, _ = self.figures()
        self.assertEqual(key_points(report, "en", False), [])
        out = self.draw("sheet")
        self.assertNotIn('class="points"', out)


class Content(ChartCase):
    def test_headlines_state_the_figures(self):
        self.usual()
        report, series = self.figures()
        ex = build_exhibits(report, series, "en", False)
        self.assertIn("Rent", ex["spending"].headline)
        self.assertIn(group(report["expenses"]["total"]), ex["spending"].headline)
        self.assertIn(signed(report["net"]), ex["waterfall"].headline)
        self.assertIn(group(report["net_worth"]["end"]["total"]), ex["networth"].headline)
        self.assertIn("2026-05", ex["networth"].headline)

    def test_bars_are_ordered_largest_first_and_scaled_to_the_largest(self):
        self.usual()
        svg = self.draw("spending", fmt="svg", lang="en")
        titles = re.findall(r"<title>([^<]*?): ", svg)
        self.assertEqual(titles[0], "Rent")
        widths = [float(w) for w in re.findall(r'<rect class="c[13]" x="[\d.]+" y="[\d.]+" width="([\d.]+)"', svg)]
        self.assertEqual(widths, sorted(widths, reverse=True))
        self.assertAlmostEqual(widths[0], 240.0, delta=60)

    def test_a_lone_leftover_category_is_not_hidden_in_other(self):
        for name in ("Pets", "Fun", "Tax", "Books", "Misc"):
            self.money("2026-09-05", f"Expenses:{name}", "10")
        for name, amount in (("Dining", "500"), ("Rent", "400"), ("Travel", "300")):
            self.money("2026-09-06", f"Expenses:{name}", amount)
        svg = self.draw("spending", fmt="svg", lang="en")  # eight categories: all fit
        self.assertNotIn("Other (1)", svg)
        self.money("2026-09-07", "Expenses:Gift", "5")  # nine: the smallest two are grouped
        self.assertIn("Other (2)", self.draw("spending", fmt="svg", lang="en"))

    def test_increases_and_decreases_use_opposite_colours_and_arrows(self):
        self.money("2026-08-05", "Expenses:Dining", "100")
        self.money("2026-08-06", "Expenses:Rent", "100")
        self.money("2026-09-05", "Expenses:Dining", "300")
        self.money("2026-09-06", "Expenses:Rent", "40")
        svg = self.draw("change", fmt="svg", lang="en")
        self.assertIn('class="neg"', svg)
        self.assertIn('class="pos"', svg)
        spending = self.draw("spending", fmt="svg", lang="en")
        self.assertIn("▲ +200.00", spending)
        self.assertIn("▼ -60.00", spending)

    def test_the_change_chart_is_ordered_by_size_of_change_and_coloured_by_direction(self):
        self.money("2026-08-05", "Expenses:Dining", "100")
        self.money("2026-08-06", "Expenses:Rent", "500")
        self.money("2026-09-05", "Expenses:Dining", "40")   # -60: a small saving
        self.money("2026-09-06", "Expenses:Rent", "800")    # +300: the big increase
        svg = self.draw("change", fmt="svg", lang="en")
        self.assertLess(svg.index("<title>Rent: +300.00"), svg.index("<title>Dining: -60.00"))
        self.assertRegex(svg, r'<title>Rent: \+300.00 CNY</title>.*?<rect class="neg"')
        self.assertRegex(svg, r'<title>Dining: -60.00 CNY</title>.*?<rect class="pos"')

    def test_value_axes_always_include_zero(self):
        self.usual()
        for kind in ("trend", "networth", "waterfall"):
            svg = self.draw(kind, fmt="svg")
            self.assertIn('text-anchor="end">0</text>', svg, kind)

    def test_months_whose_net_worth_cannot_be_converted_are_not_drawn(self):
        self.salary("2026-07-01")
        self.ledger.add_txn("2026-08-05", [("Assets:EUR", "10", "EUR"), ("Income:Salary", "-10", "EUR")])
        report, series = self.figures("2026-09")
        svg = render("networth", report, series, fmt="svg", generated=TODAY)
        self.assertNotIn("polyline", svg)
        self.assertEqual(svg.count('class="dot-worth"'), 1)
        html = render("networth", report, series, fmt="html", generated=TODAY)
        self.assertIn("<td>2026-08</td><td>–</td>", html)
        self.assertIn("EUR", svg)

    def test_chart_data_has_the_figures_behind_each_chart(self):
        self.usual()
        report, series = self.figures()
        data = chart_data("sheet", report, series)
        self.assertEqual(set(data) & {"spending", "change", "trend", "networth", "waterfall"},
                         {"spending", "change", "trend", "networth", "waterfall"})
        self.assertEqual(list(chart_data("trend", report, series)), ["month", "in", "partial", "missing", "trend"])

    def test_the_sheet_lists_the_checks(self):
        self.usual()
        self.ledger.add_txn("2026-09-25", [("Expenses:Dining", "9", "CNY"), (None, "-9", "CNY")], status="pending")
        out = self.draw("sheet", lang="en")
        self.assertIn("1 pending entry in this month is not counted", out)
        self.assertNotIn("Nothing is waiting", out)
        self.assertIn("Transfers and currency exchanges are not income or spending", out)

    def test_bad_requests_are_usage_errors(self):
        self.usual()
        report, series = self.figures()
        for kwargs in ({"kind": "pie"}, {"kind": "spending", "lang": "fr"}, {"kind": "spending", "fmt": "png"},
                       {"kind": "sheet", "fmt": "svg"}):
            kwargs.setdefault("fmt", "html")
            with self.assertRaises(LedgerError) as ctx:
                render(report=report, series=series, **kwargs)
            self.assertEqual(ctx.exception.code, "usage_error", kwargs)


class SeriesCase(ChartCase):
    def test_months_before_the_first_entry_are_left_out_and_order_is_oldest_first(self):
        self.salary("2026-07-01")
        series = monthly_series(self.ledger, 12, end="2026-09", today=TODAY)
        self.assertEqual([m["month"] for m in series["months"]], ["2026-07", "2026-08", "2026-09"])

    def test_each_month_uses_its_own_last_day_for_rates(self):
        self.price("USD", "CNY", "7", "2026-08-31")
        self.price("USD", "CNY", "8", "2026-09-30")
        self.ledger.add_txn("2026-08-05", [("Assets:USD", "100", "USD"), ("Income:Salary", "-100", "USD")])
        months = {m["month"]: m for m in monthly_series(self.ledger, 3, end="2026-09", today=TODAY)["months"]}
        self.assertEqual(months["2026-08"]["net_worth"], "700.00")
        self.assertEqual(months["2026-09"]["net_worth"], "800.00")
        self.assertEqual(months["2026-08"]["income"], "700.00")

    def test_limits(self):
        for months in (0, 61, -1):
            with self.assertRaises(LedgerError):
                monthly_series(self.ledger, months, today=TODAY)
        with self.assertRaises(LedgerError):
            monthly_series(self.ledger, 3, end="2026-11", today=TODAY)

    def test_an_empty_ledger_has_no_months(self):
        self.assertEqual(monthly_series(self.ledger, 6, end="2026-09", today=TODAY)["months"], [])


class Cli(CliTestCase):
    def setUp(self):
        super().setUp()
        self.setup_ledger()
        self.js("open", "Expenses:Rent", "--date", "2026-10-01")
        self.js("add", "--date", "2026-10-03", "--posting", "Expenses:Rent 300.00 CNY", "--posting", "Assets:CMB -300.00 CNY")
        self.args = ("chart", "--month", "2026-10")

    def test_writes_into_the_ledger_charts_folder(self):
        data = self.js(*self.args)["data"]
        path = self.root / "charts" / "sheet-2026-10.html"
        self.assertEqual(data["path"], str(path))
        self.assertTrue(path.read_text(encoding="utf-8").startswith("<!doctype html>"))
        self.assertFalse(list((self.root / "charts").glob("*.tmp")))
        again = self.js(*self.args)["data"]  # the default file is ours to refresh
        self.assertEqual(again["path"], data["path"])

    def test_private_charts_get_their_own_name(self):
        data = self.js(*self.args, "--hide-amounts")["data"]
        self.assertTrue(data["path"].endswith("sheet-2026-10-private.html"))
        self.assertTrue(data["amounts_hidden"])
        self.assertNotIn("300.00", Path(data["path"]).read_text(encoding="utf-8"))

    def test_one_chart_as_svg(self):
        data = self.js(*self.args, "spending", "--format", "svg", "--lang", "en")["data"]
        text = Path(data["path"]).read_text(encoding="utf-8")
        self.assertTrue(data["path"].endswith("spending-2026-10.svg"))
        ET.fromstring(text)

    def test_stdout(self):
        out, _ = self.run_cli("--ledger", str(self.root), *self.args, "trend", "--format", "svg", "--out", "-")
        ET.fromstring(out)
        self.assertFalse((self.root / "charts").exists())
        data = self.js(*self.args, "trend", "--format", "svg", "--out", "-")["data"]
        self.assertIn("<svg", data["content"])

    def test_json_format_writes_nothing(self):
        data = self.js(*self.args, "waterfall", "--format", "json")["data"]
        self.assertEqual(data["month"], "2026-10")
        self.assertIn("waterfall", data)
        self.assertFalse((self.root / "charts").exists())

    def test_json_format_prints_the_figures_as_text_too(self):
        out, _ = self.run_cli("--ledger", str(self.root), *self.args, "spending", "--format", "json")
        self.assertEqual(json.loads(out)["month"], "2026-10")

    def test_an_explicit_out_is_not_overwritten_without_force(self):
        target = self.dir / "mine.html"
        target.write_text("precious", encoding="utf-8")
        self.assertEqual(self.js(*self.args, "--out", str(target), expect=2)["error"]["code"], "usage_error")
        self.assertEqual(target.read_text(encoding="utf-8"), "precious")
        self.js(*self.args, "--out", str(target), "--force")
        self.assertTrue(target.read_text(encoding="utf-8").startswith("<!doctype html>"))

    def test_bad_input(self):
        for extra in (("--months", "0"), ("--months", "61"), ("--month", "oct")):
            self.assertEqual(self.js("chart", *extra, expect=2)["error"]["code"], "usage_error", extra)
        self.assertEqual(self.js(*self.args, "sheet", "--format", "svg", expect=2)["error"]["code"], "usage_error")
        self.run_cli("--ledger", str(self.root), "chart", "pie", expect=2)

    def test_it_never_touches_the_ledger_events(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*.jsonl")}
        self.js(*self.args)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*.jsonl")}, before)

    def test_doctor_still_passes_with_charts_in_the_folder(self):
        self.js(*self.args)
        self.assertTrue(self.js("doctor")["data"]["ok"])
        self.assertTrue(self.js("check")["data"]["ok"])


if __name__ == "__main__":
    unittest.main()
