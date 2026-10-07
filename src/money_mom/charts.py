"""Charts: self-contained SVG exhibits and a one-page HTML sheet, drawn from the report figures.

Design brief: the restrained look of an equity-research note. Navy and steel on white, one amber accent, muted red
and green for direction, hairline horizontal gridlines only, tabular figures, a headline that states the finding,
and a source line under every exhibit. No gradients, shadows, 3-D or animation. The page leads with the one number
that matters, then plain key points, then the exhibits; every exhibit is drawn twice, at desktop width and at phone
width, because a drawing shrunk to a phone makes its text unreadable.

Rules that keep the pictures honest:

- Charts only draw figures the report already computed, with the same conversion rules. They add no new maths.
- Missing is blank, never zero: a month with no entries is a gap, an incomplete conversion is marked in the exhibit.
- The output is plain files: no script, no web font, no network request. Every user-supplied text is escaped.
- `hide_amounts` redraws everything without a single amount (shares and shapes only) so a picture can be shared.
"""

from __future__ import annotations

import datetime as _dt
import html
import math
import re
from decimal import Decimal
from typing import Any

from .errors import LedgerError

KINDS = ("sheet", "spending", "change", "trend", "networth", "waterfall")
LANGS = ("zh", "en")
FORMATS = ("html", "svg", "json")

STANDALONE_W, HALF_H = 640, 420

# ---- words -----------------------------------------------------------------------------------

TEXT = {
    "zh": {
        "exhibit": "图", "source": "来源：Money Mom 账本；汇率取自账本中已存的参考价（非实时行情）。",
        "hidden": "金额已隐藏，仅显示比例与形状。", "in": "币种", "none": "本月没有可显示的数据。",
        "need_prev": "没有上月数据，无法比较。", "need_two": "至少需要两个月的数据。", "other": "其他",
        "amount": "金额", "share": "占比", "vs_prev": "较上月", "income": "收入", "spending": "支出", "net": "结余",
        "net_worth": "净资产", "month_to_date": "月初至今", "partial": "不完整：缺少 {ccys} 汇率，已从折算数字中剔除",
        "up": "支出增加", "down": "支出减少", "of_income": "占收入", "data": "数据表", "month": "月份",
        "sheet": "月度财务简报", "period": "统计期间", "generated": "生成于", "kpi_rate": "储蓄率", "notes": "说明与核对",
        "pending_none": "没有待确认的记录。", "sealed": "本月核对过余额：{accounts}",
        "stale": "汇率较旧：{ccys}", "transfer_note": "转账与换汇不算收支。", "fx_note": "折算用月末（当月用今天）的已存汇率。",
        "not_advice": "个人记账汇总，不构成任何投资建议。", "month_fmt": "{m}月", "ccy_note": "折算为 {ccy}",
        "h_spend": "{ccy} {total} 的支出里，{top} 占 {share}%", "h_spend_hidden": "{top} 占支出的 {share}%",
        "h_trend_hidden": "收入、支出与结余的月度走势", "h_worth_hidden": "净资产的月度走势",
        "h_change_hidden": "{cat}较上月{dir}得最多", "h_spend_none": "本月没有支出记录",
        "h_trend": "最近 {n} 个月：本月结余 {net}，月均支出 {avg}", "h_trend_none": "没有可比较的月度数据",
        "h_worth": "净资产 {end}，较 {since} 末 {delta}", "h_worth_one": "净资产 {end}",
        "h_change": "{cat}较上月{dir} {delta}", "h_change_none": "与上月相比没有变化",
        "h_water": "收入 {income} 扣除支出后结余 {net}", "word_up": "多", "word_down": "少",
        "lead": "本月结余", "lead_neg": "本月超支", "split": "收入去向", "points": "要点", "charts": "图表", "vs": "较上月",
        "tk_net": "结余 {net} {ccy}，储蓄率 {rate}%", "tk_net_hidden": "储蓄率 {rate}%",
        "tk_over": "支出比收入多 {amount} {ccy}", "tk_over_hidden": "支出超过了收入",
        "tk_top": "{cat}占支出的 {share}%（{amount} {ccy}）", "tk_top_hidden": "{cat}占支出的 {share}%",
        "tk_more": "增加", "tk_less": "减少",
        "tk_spend": "支出较上月{word} {delta} {ccy}（{pct}%）", "tk_spend_hidden": "支出较上月{word}（{pct}%）",
        "tk_cat_up": "{cat}比上月多了 {amount} {ccy}，是变化最大的分类", "tk_cat_up_hidden": "{cat}比上月多得最多",
        "tk_cat_down": "{cat}比上月少了 {amount} {ccy}，是变化最大的分类", "tk_cat_down_hidden": "{cat}比上月少得最多",
        "tk_worth": "净资产 {end} {ccy}，本月 {delta}",
        "tk_pending": "有 {n} 笔待确认的记录，没有计入这些数字", "tk_partial": "缺少 {ccys} 的汇率，相关金额没有折算进来，数字不完整",
        "tk_mtd": "本月还没结束，数字是月初至今",
        "sub_spend": "支出分类（{ccy}）", "sub_change": "各分类较上月的变化（{ccy}）", "sub_trend": "月度收入、支出与结余（{ccy}）",
        "sub_worth": "月末净资产（{ccy}）", "sub_water": "从收入到结余（{ccy}）",
    },
    "en": {
        "exhibit": "Exhibit", "source": "Source: Money Mom ledger. Exchange rates are stored reference prices, not live quotes.",
        "hidden": "Amounts hidden: shares and shapes only.", "in": "Currency", "none": "Nothing to show for this period.",
        "need_prev": "No data for the previous month to compare with.", "need_two": "Needs at least two months of data.",
        "other": "Other", "amount": "Amount", "share": "Share", "vs_prev": "vs prior", "income": "Income",
        "spending": "Spending", "net": "Net", "net_worth": "Net worth", "month_to_date": "month to date",
        "partial": "Incomplete: no rate for {ccys}, left out of the converted figures",
        "up": "spending up", "down": "spending down", "of_income": "of income", "data": "Data table", "month": "Month",
        "sheet": "Monthly financial summary", "period": "Period", "generated": "Generated", "kpi_rate": "Savings rate",
        "notes": "Notes and checks", "pending_none": "Nothing is waiting for confirmation.",
        "sealed": "Balance checked this month: {accounts}", "stale": "Rates are old: {ccys}",
        "transfer_note": "Transfers and currency exchanges are not income or spending.",
        "fx_note": "Converted at the stored rate of the last day of the month (today for the current month).",
        "not_advice": "A personal bookkeeping summary. Not investment advice.", "month_fmt": "{mon}",
        "ccy_note": "Converted to {ccy}",
        "h_spend": "{top} is {share}% of the {ccy} {total} spent", "h_spend_hidden": "{top} is {share}% of spending",
        "h_trend_hidden": "Income, spending and net by month", "h_worth_hidden": "Net worth by month",
        "h_change_hidden": "{cat} spending moved the most vs the month before", "h_spend_none": "No spending recorded this month",
        "h_trend": "Last {n} months: net {net} this month, average spending {avg}", "h_trend_none": "No monthly data to compare",
        "h_worth": "Net worth {end}, {delta} since the end of {since}", "h_worth_one": "Net worth {end}",
        "h_change": "{cat} spending {dir} {delta} on the month before", "h_change_none": "No change from the month before",
        "h_water": "Income of {income} leaves {net} after spending", "word_up": "up", "word_down": "down",
        "lead": "Net saved", "lead_neg": "Overspent", "split": "Where income went", "points": "Key points", "charts": "Charts",
        "vs": "vs prior month",
        "tk_net": "Saved {net} {ccy}, a savings rate of {rate}%", "tk_net_hidden": "A savings rate of {rate}%",
        "tk_over": "Spending was {amount} {ccy} more than income", "tk_over_hidden": "Spending was more than income",
        "tk_top": "{cat} is {share}% of spending ({amount} {ccy})", "tk_top_hidden": "{cat} is {share}% of spending",
        "tk_more": "up", "tk_less": "down",
        "tk_spend": "Spending is {word} {delta} {ccy} ({pct}%) on the month before",
        "tk_spend_hidden": "Spending is {word} ({pct}%) on the month before",
        "tk_cat_up": "{cat} is up {amount} {ccy} on the month before, the biggest move", "tk_cat_up_hidden": "{cat} rose the most on the month before",
        "tk_cat_down": "{cat} is down {amount} {ccy} on the month before, the biggest move", "tk_cat_down_hidden": "{cat} fell the most on the month before",
        "tk_worth": "Net worth is {end} {ccy}, {delta} this month",
        "tk_pending": "Pending entries not counted: {n}", "tk_partial": "No rate for {ccys}: those amounts are left out, so the figures are incomplete",
        "tk_mtd": "The month is not over: these figures are month to date",
        "sub_spend": "Spending by category ({ccy})", "sub_change": "Change by category vs the month before ({ccy})",
        "sub_trend": "Monthly income, spending and net ({ccy})", "sub_worth": "Net worth at month end ({ccy})",
        "sub_water": "From income to net ({ccy})",
    },
}
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# ---- look ------------------------------------------------------------------------------------

LIGHT = {
    "bg": "#FFFFFF", "page": "#F3F5F8", "ink": "#16202C", "muted": "#5B6877", "faint": "#8A95A3", "grid": "#E4E8EE",
    "axis": "#9AA4B1", "c1": "#0B2A4A", "c2": "#4F86C6", "c3": "#8FA3BA", "c4": "#2E8B8B", "accent": "#C4872B",
    "pos": "#1F7A4F", "neg": "#B23B33", "band": "#0B2A4A", "bandink": "#FFFFFF",
    "posbg": "#E6F2EC", "negbg": "#F7E8E6", "accentbg": "#FBF1E0", "soft": "#F7F9FB",
}
DARK = {
    "bg": "#121A24", "page": "#0C121A", "ink": "#E6EBF1", "muted": "#9AA7B6", "faint": "#76828F", "grid": "#233041",
    "axis": "#52606F", "c1": "#8DB4E2", "c2": "#4F86C6", "c3": "#6F849C", "c4": "#4DB3B3", "accent": "#E0A94E",
    "pos": "#4DB584", "neg": "#E0716A", "band": "#0A1D33", "bandink": "#E6EBF1",
    "posbg": "#12301F", "negbg": "#3A1B19", "accentbg": "#3A2C12", "soft": "#16202C",
}
FONT = ('-apple-system, "Segoe UI", "Helvetica Neue", Arial, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", '
        "sans-serif")


def _vars(palette: dict[str, str]) -> str:
    return "".join(f"--{k}:{v};" for k, v in palette.items())


SVG_CSS = f"""
.ex-bg{{fill:var(--bg)}}
text{{font-family:{FONT};font-variant-numeric:tabular-nums;fill:var(--ink)}}
.label{{font-size:10px;letter-spacing:.12em;fill:var(--accent);font-weight:600}}
.headline{{font-size:16px;font-weight:600}}
.sub{{font-size:11.5px;fill:var(--muted)}}
.tick,.cat{{font-size:10.5px;fill:var(--muted)}}
.colhead{{font-size:10px;fill:var(--faint)}}
.val{{font-size:10.5px}}
.small{{font-size:10px;fill:var(--faint)}}
.warn{{font-size:10.5px;fill:var(--accent);font-weight:600}}
.grid{{stroke:var(--grid);stroke-width:1}}
.axis{{stroke:var(--axis);stroke-width:1}}
.rule{{stroke:var(--grid);stroke-width:1}}
.conn{{stroke:var(--axis);stroke-width:.75;stroke-dasharray:2 2}}
.c1{{fill:var(--c1)}}.c2{{fill:var(--c2)}}.c3{{fill:var(--c3)}}.c4{{fill:var(--c4)}}
.pos{{fill:var(--pos)}}.neg{{fill:var(--neg)}}
.tpos{{fill:var(--pos)}}.tneg{{fill:var(--neg)}}
.line-net{{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round}}
.line-worth{{fill:none;stroke:var(--c1);stroke-width:2;stroke-linejoin:round}}
.area-worth{{fill:var(--c1);opacity:.08}}
.dot-net{{fill:var(--bg);stroke:var(--accent);stroke-width:1.75}}
.dot-worth{{fill:var(--c1);stroke:var(--bg);stroke-width:1.5}}
.dot-open{{fill:var(--bg);stroke:var(--c1);stroke-width:1.5}}
.bar:hover{{opacity:.82}}
.faint{{fill:var(--faint)}}
.strong{{font-weight:600}}
.cat.strong{{fill:var(--ink)}}
.spark-line{{fill:none;stroke:var(--c1);stroke-width:1.75;stroke-linejoin:round;stroke-linecap:round}}
.spark-dot{{fill:var(--accent)}}
"""

PAGE_CSS = f"""
*{{box-sizing:border-box}}
html{{-webkit-text-size-adjust:100%}}
body{{margin:0;background:var(--page);color:var(--ink);font-family:{FONT};font-variant-numeric:tabular-nums;line-height:1.5;font-size:14px}}
.band{{background:var(--band);color:var(--bandink);border-bottom:3px solid var(--accent)}}
.band .in{{max-width:1180px;margin:0 auto;padding:20px 30px;display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}}
.band .id{{display:flex;align-items:center;gap:14px}}
.mark{{width:30px;height:30px;border:2px solid var(--bandink);position:relative;opacity:.9;flex:none}}
.mark::after{{content:"";position:absolute;left:5px;right:5px;bottom:5px;height:10px;border-left:3px solid var(--accent);border-right:3px solid var(--accent);border-top:3px solid var(--accent)}}
.brand{{font-size:11px;letter-spacing:.2em;text-transform:uppercase;opacity:.7}}
.band h1{{margin:2px 0 0;font-size:22px;font-weight:600;letter-spacing:.01em}}
.band .meta{{font-size:12px;opacity:.85;text-align:right;line-height:1.7}}
.sheet{{max-width:1180px;margin:0 auto;padding:26px 30px 56px}}
.summary{{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:20px}}
.lead{{background:var(--bg);border:1px solid var(--grid);border-left:4px solid var(--accent);padding:24px 28px;display:flex;flex-direction:column;justify-content:center}}
.lead .k{{font-size:12px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase}}
.lead .big{{font-size:50px;font-weight:600;line-height:1.1;margin:8px 0 18px;letter-spacing:-.01em}}
.lead .big small{{font-size:16px;font-weight:500;color:var(--muted);margin-left:10px;letter-spacing:.04em}}
.big.pos{{color:var(--pos)}}.big.neg{{color:var(--neg)}}
.split{{display:flex;height:10px;background:var(--grid);overflow:hidden}}
.split .s-spent{{background:var(--c2)}}.split .s-kept{{background:var(--pos)}}
.split.over .s-spent{{background:var(--neg)}}
.split-legend{{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:12px;color:var(--ink)}}
.split-legend .muted{{color:var(--muted)}}
.dot{{display:inline-block;width:8px;height:8px;margin-right:6px;border-radius:50%}}
.dot.spent{{background:var(--c2)}}.dot.kept{{background:var(--pos)}}
.kpis{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
.kpi{{background:var(--bg);border:1px solid var(--grid);border-top:3px solid var(--c1);padding:14px 18px 12px;min-width:0}}
.kpi .k{{font-size:12px;color:var(--muted);letter-spacing:.04em}}
.kpi .v{{font-size:26px;font-weight:600;margin-top:2px;line-height:1.25}}
.kpi .row{{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:6px 8px;min-height:32px;margin-top:4px}}
.spark{{flex:none;overflow:visible;margin-left:auto}}
.chip{{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:10px;white-space:nowrap}}
.chip.good{{background:var(--posbg);color:var(--pos)}}.chip.bad{{background:var(--negbg);color:var(--neg)}}
.chip.flat{{background:var(--soft);color:var(--muted)}}
.sec{{display:flex;align-items:center;gap:14px;margin:34px 0 14px;font-size:12px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}}
.sec::after{{content:"";flex:1;height:1px;background:var(--grid)}}
.points{{list-style:none;margin:0;padding:0;background:var(--bg);border:1px solid var(--grid)}}
.points li{{display:flex;gap:14px;align-items:baseline;padding:11px 20px;border-bottom:1px solid var(--grid);font-size:14.5px}}
.points li:last-child{{border-bottom:0}}
.points .ico{{flex:none;width:16px;text-align:center;font-size:11px;font-weight:700}}
.points .info .ico{{color:var(--c1)}}.points .up .ico{{color:var(--pos)}}.points .down .ico{{color:var(--neg)}}.points .warn .ico{{color:var(--accent)}}
.points .warn{{background:var(--accentbg)}}
.stack{{display:grid;gap:20px}}
.pair{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}}
.card{{background:var(--bg);border:1px solid var(--grid);min-width:0}}
.card svg{{display:block;width:100%;height:auto}}
.desk{{display:block}}.mob{{display:none}}
details{{border-top:1px solid var(--grid);padding:8px 18px 10px;font-size:12px;color:var(--muted)}}
summary{{cursor:pointer;color:var(--muted)}}
table{{border-collapse:collapse;width:100%;margin-top:8px}}
th,td{{padding:3px 6px;text-align:right;border-bottom:1px solid var(--grid);font-size:11.5px;color:var(--ink)}}
th{{color:var(--muted);font-weight:500}}
th:first-child,td:first-child{{text-align:left}}
.notes{{margin:0;padding:16px 24px 16px 40px;background:var(--bg);border:1px solid var(--grid);font-size:13px;color:var(--muted)}}
.notes li{{margin:3px 0}}
.foot{{margin-top:22px;font-size:11px;color:var(--faint)}}
@media (max-width:820px){{
  .sheet{{padding:16px 14px 40px}}.band .in{{padding:16px 14px}}.band .meta{{text-align:left}}
  .summary,.pair{{grid-template-columns:1fr}}
  .desk{{display:none}}.mob{{display:block}}
  .lead{{padding:18px 18px}}.lead .big{{font-size:40px}}
  .kpi .v{{font-size:22px}}
  .kpi .row{{display:block}}.chip{{white-space:normal}}.spark{{display:block;margin-top:8px}}
}}
@media print{{
  @page{{size:A4 landscape;margin:10mm}}
  :root{{{_vars(LIGHT)}}}
  body{{background:#fff}}.band,.split,.chip{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  .card,.kpi,.lead{{break-inside:avoid}}details{{display:none}}
  .desk{{display:block!important}}.mob{{display:none!important}}
}}
"""


def _root_css() -> str:
    return (f":root{{{_vars(LIGHT)}}}"
            f"@media (prefers-color-scheme:dark){{:root{{{_vars(DARK)}}}}}")


# ---- small helpers ---------------------------------------------------------------------------

def esc(text: Any) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def text_width(text: str, size: float) -> float:
    """Rough width in pixels: CJK characters are one em wide, everything else about 0.56 em."""
    return sum(size if ord(c) > 0x2E80 else size * 0.56 for c in text)


def fit(text: str, size: float, width: float) -> str:
    if text_width(text, size) <= width:
        return text
    out = ""
    for c in text:
        if text_width(out + c + "…", size) > width:
            break
        out += c
    return out + "…"


_TOKEN = re.compile(r"[A-Za-z0-9.,+\-−%$¥()/:*]+|\s+|.", re.S)


def wrap(text: str, size: float, width: float, lines: int = 2) -> list[str]:
    """Break text into at most `lines` lines without splitting a number or a word in the middle.

    Words and numbers are kept whole, each CJK character may start a line, and what does not fit in the last
    line is cut with an ellipsis."""
    out: list[str] = []
    cur = ""
    pieces = _TOKEN.findall(text)
    k = 0
    while k < len(pieces):
        piece = pieces[k]
        if cur and text_width(cur + piece, size) > width and not piece.isspace():
            out.append(cur.rstrip())
            cur = ""
            if len(out) == lines - 1:
                cur = "".join(pieces[k:])
                break
        if not cur and piece.isspace():
            k += 1
            continue
        cur += piece
        k += 1
    out.append(fit(cur.rstrip(), size, width))
    return [line for line in out if line]


def group(amount: str) -> str:
    """'-12345.60' -> '-12,345.60' (the digits are never changed, only grouped)."""
    whole, dot, frac = amount.lstrip("-").partition(".")
    out = ""
    while len(whole) > 3:
        out = "," + whole[-3:] + out
        whole = whole[:-3]
    return ("-" if amount.startswith("-") else "") + whole + out + dot + frac


def signed(amount: str | None) -> str:
    if amount is None:
        return "–"
    d = Decimal(amount)
    return group(amount) if d < 0 else ("+" if d > 0 else "") + group(amount)


def axis_label(value: float, lang: str, scale: float = 0.0) -> str:
    """A tick label. `scale` is the largest absolute tick on the axis, so every label shares one unit."""
    a = abs(scale or value)
    units = ((1e8, "亿"), (1e4, "万")) if lang == "zh" else ((1e9, "B"), (1e6, "M"), (1e3, "k"))
    for limit, unit in units:
        if a >= limit:
            return "0" if value == 0 else _trim(value / limit) + unit
    return _trim(value)


def _trim(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """Round tick values that cover [lo, hi] (and always include 0 when the range touches it)."""
    if hi == lo:
        hi, lo = hi + 1, lo - 1 if lo != 0 else 0
    span = hi - lo
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step + 1e-9) * step
    ticks, v = [], start
    while v <= hi + step * 0.999:
        ticks.append(round(v, 10))
        v += step
        if len(ticks) > 12:
            break
    return ticks


def _num(text: str | None) -> float | None:
    return None if text is None else float(Decimal(text))


def month_label(month: str, lang: str) -> str:
    m = int(month[5:7])
    return TEXT["zh"]["month_fmt"].format(m=m) if lang == "zh" else MONTHS_EN[m - 1]


# ---- exhibit frame ---------------------------------------------------------------------------

class Exhibit:
    """One chart. `w` is the drawing width in pixels: the page shows an exhibit at 1:1, so a compact drawing
    (about a phone's width) keeps its text readable where a shrunken desktop drawing would not."""

    def __init__(self, number: int, subtitle: str, lang: str, *, hide: bool, w: int = STANDALONE_W, h: int = HALF_H,
                 compact: bool = False) -> None:
        self.n, self.subtitle, self.lang, self.hide, self.w, self.h, self.compact = number, subtitle, lang, hide, w, h, compact
        self.pad = 16 if compact else 24
        self.headline = ""
        self.summary = ""
        self.head: list[str] = []
        self.top = 0
        self.parts: list[str] = []
        self.table: tuple[list[str], list[list[str]]] | None = None
        self.warning: str | None = None

    @property
    def bottom(self) -> int:
        """The lowest line of the plot area; the axis labels, warnings and source line sit below it."""
        return self.h - 78

    def begin(self, headline: str) -> None:
        """Set the headline (the finding) and work out where the plot area starts below it."""
        self.headline = self.summary = headline
        self.head = wrap(headline, 15 if self.compact else 16, self.w - 2 * self.pad, 2)
        self.sub_y = 46 + 21 * (len(self.head) - 1) + 22
        self.top = self.sub_y + 30

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    def svg(self, *, standalone: bool) -> str:
        t = TEXT[self.lang]
        pad, w, h = self.pad, self.w, self.h
        size = 15 if self.compact else 16
        out = [f'<text class="label" x="{pad}" y="26">{esc(t["exhibit"].upper() if self.lang == "en" else t["exhibit"])} {self.n}</text>']
        for i, line in enumerate(self.head):
            out.append(f'<text class="headline" x="{pad}" y="{46 + 21 * i}" style="font-size:{size}px">{esc(line)}</text>')
        out.append(f'<text class="sub" x="{pad}" y="{self.sub_y}">{esc(fit(self.subtitle, 11.5, w - 2 * pad))}</text>')
        out += self.parts
        notes = ([self.warning] if self.warning else []) + ([t["hidden"]] if self.hide else [])
        for i, line in enumerate(reversed(notes)):
            cls = "warn" if line is self.warning else "small"
            out.append(f'<text class="{cls}" x="{pad}" y="{h - 30 - 13 * i}">{esc(fit(line, 10.5, w - 2 * pad))}</text>')
        out.append(f'<line class="rule" x1="{pad}" x2="{w - pad}" y1="{h - 24}" y2="{h - 24}"/>')
        out.append(f'<text class="small" x="{pad}" y="{h - 10}">{esc(fit(t["source"], 10, w - 2 * pad))}</text>')
        ns = ' xmlns="http://www.w3.org/2000/svg"' if standalone else ""
        style = f"<style>{_root_css()}{SVG_CSS}</style>" if standalone else ""
        bg = f'<rect class="ex-bg" width="{w}" height="{h}"/>' if standalone else ""
        hidden = "" if standalone or not self.compact else ' aria-hidden="true"'
        return (f'<svg{ns} viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img"{hidden} '
                f'aria-label="{esc(self.summary)}"><title>{esc(self.summary)}</title>{style}{bg}{"".join(out)}</svg>')

    def data_table(self) -> str:
        if not self.table or self.hide:
            return ""
        heads, rows = self.table
        body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows)
        return (f"<details><summary>{esc(TEXT[self.lang]['data'])}</summary><table><thead><tr>"
                + "".join(f"<th>{esc(h)}</th>" for h in heads) + f"</tr></thead><tbody>{body}</tbody></table></details>")


def _placeholder(ex: Exhibit, headline: str, message: str) -> Exhibit:
    ex.begin(headline)
    ex.add(f'<text class="tick" x="{ex.w / 2}" y="{(ex.top + ex.bottom) / 2}" text-anchor="middle">{esc(fit(message, 10.5, ex.w - 2 * ex.pad))}</text>')
    return ex


def _warn(ex: Exhibit, missing: list[str]) -> None:
    if missing:
        ex.warning = "! " + TEXT[ex.lang]["partial"].format(ccys=", ".join(missing))


def _y_axis(ex: Exhibit, ticks: list[float], y_of, left: float, right: float) -> None:
    biggest = max(abs(tick) for tick in ticks)
    for tick in ticks:
        y = y_of(tick)
        cls = "axis" if tick == 0 else "grid"
        ex.add(f'<line class="{cls}" x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        if not ex.hide:
            ex.add(f'<text class="tick" x="{left - 8}" y="{y + 3.5:.1f}" text-anchor="end">{esc(axis_label(tick, ex.lang, biggest))}</text>')


def _x_labels(ex: Exhibit, months: list[str], x_of, band: float) -> None:
    stride = 1 if band >= 34 else 2 if band >= 20 else 3
    y = ex.bottom + 14
    for i, month in enumerate(months):
        if i % stride and not (i == len(months) - 1 and stride > 1 and (len(months) - 1) % stride == 0):
            continue
        x = x_of(i)
        ex.add(f'<text class="tick" x="{x:.1f}" y="{y}" text-anchor="middle">{esc(month_label(month, ex.lang))}</text>')
        if i == 0 or month.endswith("-01"):
            ex.add(f'<text class="small" x="{x:.1f}" y="{y + 12}" text-anchor="middle">{month[:4]}</text>')


def _money2(value: Decimal) -> str:
    return group(format(value.quantize(Decimal("0.01")), "f"))


# ---- 1. spending composition -----------------------------------------------------------------

def spending_exhibit(report: dict[str, Any], lang: str, hide: bool, number: int = 1, top: int = 7, *,
                     w: int = STANDALONE_W, h: int = HALF_H, compact: bool = False) -> Exhibit:
    t, ccy = TEXT[lang], report["in"]
    ex = Exhibit(number, t["sub_spend"].format(ccy=ccy), lang, hide=hide, w=w, h=h, compact=compact)
    _warn(ex, report["missing"])
    rows = [c for c in report["categories"] if c["converted"] is not None and Decimal(c["converted"]) > 0]
    total = Decimal(report["expenses"]["total"])
    if not rows or total <= 0:
        return _placeholder(ex, t["h_spend_none"], t["none"])
    shown, rest = (rows, []) if len(rows) <= top + 1 else (rows[:top], rows[top:])
    items = [(c["category"], Decimal(c["converted"]), c["share_percent"], c["change"], False) for c in shown]
    if rest:
        value = sum((Decimal(c["converted"]) for c in rest), Decimal(0))
        pct = None if report["expenses"]["missing"] else str((value * 100 / total).quantize(Decimal("0.1")))
        items.append((f'{t["other"]} ({len(rest)})', value, pct, None, True))
    first = items[0]
    ex.begin((t["h_spend_hidden"] if hide else t["h_spend"]).format(
        ccy=ccy, total=group(report["expenses"]["total"]), top=first[0], share=first[2] or "–"))
    pad, right = ex.pad, ex.w - ex.pad
    top_y = ex.top
    step = min(34.0 if compact else 32.0, (ex.bottom + 14 - top_y) / len(items))
    biggest = max(v for _, v, *_ in items)
    table = []
    if not compact:
        change_x = right
        share_x = change_x - 64
        amount_x = share_x - 52
        label_w = 118
        bar_x = pad + label_w + 8
        bar_w = max(amount_x - 76 - bar_x, 40)
        ex.add(f'<text class="colhead" x="{share_x}" y="{top_y - 8}" text-anchor="end">{esc(t["share"])}</text>')
        if not hide:
            ex.add(f'<text class="colhead" x="{amount_x}" y="{top_y - 8}" text-anchor="end">{esc(t["amount"])}</text>')
            ex.add(f'<text class="colhead" x="{change_x}" y="{top_y - 8}" text-anchor="end">{esc(t["vs_prev"])}</text>')
    else:
        share_x = right
        bar_x = pad
        bar_w = right - pad - 62
    ex.add(f'<line class="rule" x1="{pad}" x2="{right}" y1="{top_y}" y2="{top_y}"/>')
    for i, (name, value, pct, change, is_other) in enumerate(items):
        y = top_y + i * step
        mid = y + step / 2
        width = float(value / biggest) * bar_w
        cls = "c3" if is_other else "c1"
        shown_amount = _money2(value)
        tip = f"{name}: {pct or '–'}%" if hide else f"{name}: {shown_amount} {ccy} ({pct or '–'}%)"
        pct_text = (pct + "%") if pct else "–"
        ex.add(f'<g class="bar"><title>{esc(tip)}</title>')
        if not compact:
            ex.add(f'<text class="cat" x="{pad}" y="{mid + 3.5:.1f}">{esc(fit(name, 10.5, 118))}</text>'
                   f'<rect class="{cls}" x="{bar_x}" y="{mid - 6:.1f}" width="{max(width, 1.5):.1f}" height="12" rx="1"/>')
            if not hide:
                ex.add(f'<text class="val" x="{amount_x}" y="{mid + 3.5:.1f}" text-anchor="end">{esc(shown_amount)}</text>')
            ex.add(f'<text class="val" x="{share_x}" y="{mid + 3.5:.1f}" text-anchor="end">{esc(pct_text)}</text>')
            if not hide:
                if change is None:
                    ex.add(f'<text class="val faint" x="{change_x}" y="{mid + 3.5:.1f}" text-anchor="end">–</text>')
                else:
                    c = Decimal(change)
                    ex.add(f'<text class="val {"tneg" if c > 0 else "tpos" if c < 0 else ""}" x="{change_x}" y="{mid + 3.5:.1f}" '
                           f'text-anchor="end">{esc(("▲ " if c > 0 else "▼ " if c < 0 else "") + signed(change))}</text>')
        else:  # two lines: the name and amount, then the bar with its share
            ex.add(f'<text class="cat strong" x="{pad}" y="{y + 12:.1f}">{esc(fit(name, 10.5, right - pad - (0 if hide else 96)))}</text>')
            if not hide:
                ex.add(f'<text class="val" x="{right}" y="{y + 12:.1f}" text-anchor="end">{esc(shown_amount)}</text>')
            ex.add(f'<rect class="{cls}" x="{bar_x}" y="{y + 18:.1f}" width="{max(width, 1.5):.1f}" height="8" rx="1"/>'
                   f'<text class="val" x="{share_x}" y="{y + 26:.1f}" text-anchor="end">{esc(pct_text)}</text>')
        ex.add("</g>")
        ex.add(f'<line class="rule" x1="{pad}" x2="{right}" y1="{y + step:.1f}" y2="{y + step:.1f}"/>')
        table.append([name, shown_amount, pct_text, signed(change) if change else "–"])
    ex.table = ([t["spending"], f"{t['amount']} ({ccy})", t["share"], t["vs_prev"]], table)
    return ex


# ---- 2. change against last month ------------------------------------------------------------

def change_exhibit(report: dict[str, Any], lang: str, hide: bool, number: int = 2, top: int = 8, *,
                   w: int = STANDALONE_W, h: int = HALF_H, compact: bool = False) -> Exhibit:
    t, ccy = TEXT[lang], report["in"]
    ex = Exhibit(number, t["sub_change"].format(ccy=ccy), lang, hide=hide, w=w, h=h, compact=compact)
    _warn(ex, report["missing"])
    if not report["previous"]["has_data"]:
        return _placeholder(ex, t["h_change_none"], t["need_prev"])
    rows = [(c["category"], Decimal(c["change"])) for c in report["categories"] if c["change"] is not None]
    rows = [r for r in rows if r[1] != 0]
    if not rows:
        return _placeholder(ex, t["h_change_none"], t["h_change_none"])
    rows.sort(key=lambda r: (-abs(r[1]), r[0]))
    biggest = rows[0]
    ex.begin((t["h_change_hidden"] if hide else t["h_change"]).format(
        cat=biggest[0], dir=t["word_up"] if biggest[1] > 0 else t["word_down"], delta=group(format(abs(biggest[1]), "f"))))
    rows = rows[:top]
    pad, right = ex.pad, ex.w - ex.pad
    label_w = 88 if compact else 118
    left = pad + label_w + 8
    centre = (left + right) / 2
    room = (right - left) / 2 - (8 if hide else 62)
    scale = room / float(max(abs(v) for _, v in rows))
    ex.add(f'<line class="rule" x1="{pad}" x2="{right}" y1="{ex.top}" y2="{ex.top}"/>')
    ex.add(f'<text class="colhead" x="{centre - 6}" y="{ex.top - 8}" text-anchor="end">◀ {esc(t["down"])}</text>')
    ex.add(f'<text class="colhead" x="{centre + 6}" y="{ex.top - 8}">{esc(t["up"])} ▶</text>')
    step = min(32.0, (ex.bottom + 14 - ex.top) / len(rows))
    table = []
    for i, (name, delta) in enumerate(rows):
        mid = ex.top + i * step + step / 2
        width = float(abs(delta)) * scale
        x = centre if delta > 0 else centre - width
        label = signed(format(delta, "f"))
        tip = name if hide else f"{name}: {label} {ccy}"
        ex.add(f'<g class="bar"><title>{esc(tip)}</title>'
               f'<text class="cat" x="{pad}" y="{mid + 3.5:.1f}">{esc(fit(name, 10.5, label_w))}</text>'
               f'<rect class="{"neg" if delta > 0 else "pos"}" x="{x:.1f}" y="{mid - 6:.1f}" width="{max(width, 1.5):.1f}" height="12" rx="1"/>')
        if not hide:
            if delta > 0:
                ex.add(f'<text class="val" x="{centre + width + 6:.1f}" y="{mid + 3.5:.1f}">{esc(label)}</text>')
            else:
                ex.add(f'<text class="val" x="{centre - width - 6:.1f}" y="{mid + 3.5:.1f}" text-anchor="end">{esc(label)}</text>')
        ex.add("</g>")
        table.append([name, label])
    ex.add(f'<line class="axis" x1="{centre:.1f}" x2="{centre:.1f}" y1="{ex.top}" y2="{ex.top + step * len(rows):.1f}"/>')
    ex.table = ([t["spending"], f"{t['vs_prev']} ({ccy})"], table)
    return ex


# ---- 3. monthly trend ------------------------------------------------------------------------

def trend_exhibit(series: dict[str, Any], lang: str, hide: bool, number: int = 3, *,
                  w: int = STANDALONE_W, h: int = HALF_H, compact: bool = False) -> Exhibit:
    t, ccy = TEXT[lang], series["in"]
    rows = series["months"]
    ex = Exhibit(number, t["sub_trend"].format(ccy=ccy), lang, hide=hide, w=w, h=h, compact=compact)
    have = [r for r in rows if r["income"] is not None]
    _warn(ex, sorted({c for r in rows for c in r["missing"]}))
    if not have:
        return _placeholder(ex, t["h_trend_none"], t["none"])
    spend = [Decimal(r["expenses"]) for r in have]
    avg = (sum(spend, Decimal(0)) / len(spend)).quantize(Decimal("0.01"))
    ex.begin((t["h_trend_hidden"] if hide else t["h_trend"]).format(
        n=len(rows), net=signed(have[-1]["net"]), avg=group(format(avg, "f"))))
    values = [v for r in have for v in (_num(r["income"]), _num(r["expenses"]), _num(r["net"]))]
    ticks = nice_ticks(min(min(values), 0), max(values))
    lo, hi = ticks[0], ticks[-1]
    pad = ex.pad
    left, right, top, bottom = (48.0 if compact else 64.0), ex.w - pad, ex.top + 8, ex.bottom - 8
    y_of = lambda v: bottom - (v - lo) / (hi - lo) * (bottom - top)
    n = len(rows)
    band = (right - left) / n
    x_of = lambda i: left + band * (i + 0.5)
    _y_axis(ex, ticks, y_of, left, right)
    lx = pad
    for cls, name in (("c1", t["income"]), ("c2", t["spending"]), ("line", t["net"])):
        if cls == "line":
            ex.add(f'<line x1="{lx}" x2="{lx + 14}" y1="{ex.top - 12}" y2="{ex.top - 12}" style="stroke:var(--accent);stroke-width:2"/>')
        else:
            ex.add(f'<rect class="{cls}" x="{lx}" y="{ex.top - 17}" width="10" height="10" rx="1"/>')
        ex.add(f'<text class="tick" x="{lx + 18}" y="{ex.top - 8}">{esc(name)}</text>')
        lx += 18 + text_width(name, 10.5) + 18
    bar_w = min(band * 0.3, 24)
    table, points = [], []
    for i, r in enumerate(rows):
        x = x_of(i)
        if r["income"] is None:
            points.append(None)
            table.append([r["month"], "–", "–", "–"])
            continue
        for key, cls, dx in (("income", "c1", -bar_w - 1), ("expenses", "c2", 1)):
            v = _num(r[key])
            y0, y1 = y_of(max(v, 0)), y_of(min(v, 0))
            tip = f'{r["month"]} {t[{"income": "income", "expenses": "spending"}[key]]}' + ("" if hide else f': {group(r[key])} {ccy}')
            ex.add(f'<g class="bar"><title>{esc(tip)}</title><rect class="{cls}" x="{x + dx:.1f}" y="{y0:.1f}" width="{bar_w:.1f}" height="{max(y1 - y0, 1):.1f}" rx="1"/></g>')
        points.append((x, y_of(_num(r["net"])), r))
        table.append([r["month"], group(r["income"]), group(r["expenses"]), signed(r["net"])])
    segment: list[tuple[float, float, dict]] = []
    for point in points + [None]:
        if point is None:
            if len(segment) > 1:
                ex.add('<polyline class="line-net" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in segment) + '"/>')
            for x, y, r in segment:
                tip = f'{r["month"]} {t["net"]}' + ("" if hide else f": {signed(r['net'])} {ccy}")
                ex.add(f'<g class="bar"><title>{esc(tip)}</title><circle class="dot-net" cx="{x:.1f}" cy="{y:.1f}" r="3.25"/></g>')
            segment = []
        else:
            segment.append(point)
    _x_labels(ex, [r["month"] for r in rows], x_of, band)
    ex.table = ([t["month"], t["income"], t["spending"], t["net"]], table)
    return ex


# ---- 4. net worth ----------------------------------------------------------------------------

def networth_exhibit(series: dict[str, Any], lang: str, hide: bool, number: int = 4, *,
                     w: int = STANDALONE_W, h: int = HALF_H, compact: bool = False) -> Exhibit:
    t, ccy = TEXT[lang], series["in"]
    rows = series["months"]
    ex = Exhibit(number, t["sub_worth"].format(ccy=ccy), lang, hide=hide, w=w, h=h, compact=compact)
    usable = [r for r in rows if not r["net_worth_partial"]]
    _warn(ex, sorted({c for r in rows for c in r["net_worth_missing"]}))
    if len(rows) < 2 or len(usable) < 1:
        if not usable:
            return _placeholder(ex, t["none"], t["none"])
        headline = t["h_worth_hidden"] if hide else t["h_worth_one"].format(end=f"{group(usable[-1]['net_worth'])} {ccy}")
        return _placeholder(ex, headline, t["need_two"])
    end, first = usable[-1], usable[0]
    now = f"{group(end['net_worth'])} {ccy}"
    if hide:
        headline = t["h_worth_hidden"]
    elif end is first:
        headline = t["h_worth_one"].format(end=now)
    else:
        delta = format(Decimal(end["net_worth"]) - Decimal(first["net_worth"]), "f")
        headline = t["h_worth"].format(end=now, since=first["month"], delta=signed(delta))
    ex.begin(headline)
    values = [_num(r["net_worth"]) for r in usable]
    ticks = nice_ticks(min(min(values), 0), max(max(values), 0))
    lo, hi = ticks[0], ticks[-1]
    pad = ex.pad
    left, right, top, bottom = (48.0 if compact else 64.0), ex.w - pad - 10, ex.top + 8, ex.bottom - 8
    y_of = lambda v: bottom - (v - lo) / (hi - lo) * (bottom - top)
    n = len(rows)
    step = (right - left) / max(n - 1, 1)
    x_of = lambda i: left + step * i
    _y_axis(ex, ticks, y_of, left, ex.w - pad)
    pts = [(x_of(i), y_of(_num(r["net_worth"])), r) for i, r in enumerate(rows) if not r["net_worth_partial"]]
    if len(pts) > 1:
        zero = y_of(0)
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
        ex.add(f'<polygon class="area-worth" points="{pts[0][0]:.1f},{zero:.1f} {poly} {pts[-1][0]:.1f},{zero:.1f}"/>')
        ex.add(f'<polyline class="line-worth" points="{poly}"/>')
    table = []
    for i, r in enumerate(rows):
        if r["net_worth_partial"]:
            table.append([r["month"], "–"])
            continue
        x, y = x_of(i), y_of(_num(r["net_worth"]))
        tip = r["month"] + ("" if hide else f': {group(r["net_worth"])} {ccy}')
        ex.add(f'<g class="bar"><title>{esc(tip)}</title><circle class="dot-worth" cx="{x:.1f}" cy="{y:.1f}" r="3.25"/></g>')
        table.append([r["month"], group(r["net_worth"])])
    if not hide:
        x, y, r = pts[-1]
        ex.add(f'<text class="val strong" x="{x:.1f}" y="{y - 10:.1f}" text-anchor="end">{esc(group(r["net_worth"]))}</text>')
    _x_labels(ex, [r["month"] for r in rows], x_of, step)
    ex.table = ([t["month"], f"{t['net_worth']} ({ccy})"], table)
    return ex


# ---- 5. waterfall ----------------------------------------------------------------------------

def waterfall_exhibit(report: dict[str, Any], lang: str, hide: bool, number: int = 5, top: int = 5, *,
                      w: int = STANDALONE_W, h: int = HALF_H, compact: bool = False) -> Exhibit:
    t, ccy = TEXT[lang], report["in"]
    ex = Exhibit(number, t["sub_water"].format(ccy=ccy), lang, hide=hide, w=w, h=h, compact=compact)
    _warn(ex, report["missing"])
    income = Decimal(report["income"]["total"])
    cats = [(c["category"], Decimal(c["converted"])) for c in report["categories"] if c["converted"] is not None and Decimal(c["converted"]) > 0]
    if report["entries"] == 0 or (income == 0 and not cats):
        return _placeholder(ex, t["h_spend_none"], t["none"])
    shown, rest = (cats, []) if len(cats) <= top + 1 else (cats[:top], cats[top:])
    steps: list[tuple[str, Decimal, str]] = [(t["income"], income, "total")]
    steps += [(name, -v, "down") for name, v in shown]
    if rest:
        steps.append((f'{t["other"]} ({len(rest)})', -sum((v for _, v in rest), Decimal(0)), "down"))
    net = Decimal(report["net"])
    steps.append((t["net"], net, "total"))
    pct = None if income <= 0 else str((net * 100 / income).quantize(Decimal("0.1")))
    if hide:
        ex.begin(f'{t["net"]} {pct}% {t["of_income"]}' if pct is not None else t["sub_water"].format(ccy=ccy))
    else:
        ex.begin(t["h_water"].format(income=group(report["income"]["total"]), net=signed(report["net"])))
    levels, running = [], Decimal(0)
    for name, v, kind in steps:
        if kind == "total":
            levels.append((name, Decimal(0), v, kind, v))
            running = v
        else:
            levels.append((name, running, running + v, kind, v))
            running += v
    pts = [float(x) for _, a, b, _, _ in levels for x in (a, b)]
    scale_div = float(income) if hide and income > 0 else 1.0
    ticks = nice_ticks(min(min(pts), 0) / scale_div, max(pts) / scale_div)
    lo, hi = ticks[0], ticks[-1]
    pad = ex.pad
    left, right, top_y, bottom = (48.0 if compact else 64.0), ex.w - pad, ex.top + 14, ex.bottom - 8
    y_of = lambda v: bottom - (v / scale_div - lo) / (hi - lo) * (bottom - top_y)
    if hide:
        for tick in ticks:
            y = bottom - (tick - lo) / (hi - lo) * (bottom - top_y)
            ex.add(f'<line class="{"axis" if tick == 0 else "grid"}" x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}"/>')
            ex.add(f'<text class="tick" x="{left - 8}" y="{y + 3.5:.1f}" text-anchor="end">{esc(_trim(tick * 100) + "%")}</text>')
    else:
        _y_axis(ex, ticks, y_of, left, right)
    band = (right - left) / len(levels)
    bw = min(band * 0.56, 44)
    table = []
    prev_end = None
    for i, (name, a, b, kind, value) in enumerate(levels):
        x = left + band * i + (band - bw) / 2
        ya, yb = y_of(float(a)), y_of(float(b))
        cls = "c1" if name == t["income"] else ("pos" if value >= 0 else "neg") if kind == "total" else "c3"
        y0, y1 = min(ya, yb), max(ya, yb)
        if prev_end is not None:
            ex.add(f'<line class="conn" x1="{x - (band - bw):.1f}" x2="{x:.1f}" y1="{prev_end:.1f}" y2="{prev_end:.1f}"/>')
        share = f"{(value * 100 / income).quantize(Decimal('0.1'))}%" if income > 0 else ""
        tip = name + (f": {share}" if hide else f": {signed(format(value.quantize(Decimal('0.01')), 'f'))} {ccy}")
        ex.add(f'<g class="bar"><title>{esc(tip)}</title><rect class="{cls}" x="{x:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1 - y0, 1.5):.1f}" rx="1"/></g>')
        label = share if hide else signed(format(value.quantize(Decimal("0.01")), "f")) if kind == "down" else _money2(value)
        ly = (y0 - 5) if kind == "total" and value >= 0 else (y1 + 12)
        ex.add(f'<text class="val" x="{x + bw / 2:.1f}" y="{ly:.1f}" text-anchor="middle">{esc(label)}</text>')
        ex.add(f'<text class="cat" x="{x + bw / 2:.1f}" y="{ex.bottom + 14}" text-anchor="middle">{esc(fit(name, 10.5, band - 4))}</text>')
        prev_end = yb
        table.append([name, signed(format(value.quantize(Decimal("0.01")), "f"))])
    ex.table = (["", ccy], table)
    return ex


# ---- small multiples and key points ----------------------------------------------------------

def sparkline(values: list[float | None], width: int = 96, height: int = 30) -> str:
    """A tiny line of the last months. Gaps (None) break the line; the last point is marked."""
    known = [v for v in values if v is not None]
    if len(known) < 2:
        return ""
    lo, hi = min(known), max(known)
    span = (hi - lo) or 1.0
    n = len(values)
    xs = [3 + (width - 6) * i / max(n - 1, 1) for i in range(n)]
    ys = [None if v is None else height - 4 - (v - lo) / span * (height - 8) for v in values]
    runs, run = [], []
    for x, y in zip(xs, ys):
        if y is None:
            if len(run) > 1:
                runs.append(run)
            run = []
        else:
            run.append((x, y))
    if len(run) > 1:
        runs.append(run)
    lines = "".join('<polyline class="spark-line" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in r) + '"/>' for r in runs)
    last = next((x, y) for x, y in reversed(list(zip(xs, ys))) if y is not None)
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" aria-hidden="true">'
            f'{lines}<circle class="spark-dot" cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="2.6"/></svg>')


def _pct(part: Decimal, whole: Decimal) -> str | None:
    return None if whole == 0 else str((part * 100 / whole).quantize(Decimal("0.1")))


def key_points(report: dict[str, Any], lang: str, hide: bool) -> list[tuple[str, str]]:
    """Plain statements read straight off the report: (kind, text) with kind info, up, down or warn."""
    t, ccy = TEXT[lang], report["in"]
    out: list[tuple[str, str]] = []
    net, income = Decimal(report["net"]), Decimal(report["income"]["total"])
    rate = report["savings_rate_percent"]
    if net < 0:
        out.append(("down", (t["tk_over_hidden"] if hide else t["tk_over"]).format(amount=group(format(abs(net), "f")), ccy=ccy)))
    elif rate is not None:
        out.append(("up", (t["tk_net_hidden"] if hide else t["tk_net"]).format(net=signed(report["net"]), ccy=ccy, rate=rate)))
    cats = [c for c in report["categories"] if c["converted"] is not None and Decimal(c["converted"]) > 0]
    if cats and not report["expenses"]["missing"]:
        c = cats[0]
        out.append(("info", (t["tk_top_hidden"] if hide else t["tk_top"]).format(
            cat=c["category"], share=c["share_percent"] or "–", amount=group(c["converted"]), ccy=ccy)))
    prev = report["previous"]
    if prev["has_data"] and prev.get("expenses_change") is not None:
        delta = Decimal(prev["expenses_change"])
        pct = prev["expenses_change_percent"]
        if delta != 0:
            word = t["tk_more"] if delta > 0 else t["tk_less"]
            text = (t["tk_spend_hidden"].format(word=word, pct=signed(pct) if pct is not None else "–") if hide else
                    t["tk_spend"].format(word=word, delta=group(format(abs(delta), "f")), ccy=ccy,
                                         pct=signed(pct) if pct is not None else "–"))
            out.append(("down" if delta > 0 else "up", text))
        moved = [c for c in report["categories"] if c["change"] is not None and Decimal(c["change"]) != 0]
        if moved:
            big = max(moved, key=lambda c: (abs(Decimal(c["change"])), c["category"]))
            d = Decimal(big["change"])
            key = ("tk_cat_up" if d > 0 else "tk_cat_down") + ("_hidden" if hide else "")
            out.append(("down" if d > 0 else "up", t[key].format(cat=big["category"], amount=group(format(abs(d), "f")), ccy=ccy)))
    worth = report["net_worth"]
    if worth["change"] is not None and not hide and (report["entries"] or Decimal(worth["change"]) != 0):
        out.append(("info", t["tk_worth"].format(end=group(worth["end"]["total"]), ccy=ccy, delta=signed(worth["change"]))))
    checks = report["checks"]
    if checks["pending_in_month"]:
        out.append(("warn", t["tk_pending"].format(n=checks["pending_in_month"])))
    if report["missing"]:
        out.append(("warn", t["tk_partial"].format(ccys=", ".join(report["missing"]))))
    if report["in_progress"]:
        out.append(("warn", t["tk_mtd"]))
    return out


def _delta_chip(cur: Decimal | None, prev: Decimal | None, *, good_when_up: bool, lang: str) -> str:
    if cur is None or prev is None:
        return ""
    diff = cur - prev
    if diff == 0:
        return f'<span class="chip flat">= {esc(TEXT[lang]["vs"])}</span>'
    pct = f" ({'+' if diff > 0 else '−'}{abs(diff * 100 / prev).quantize(Decimal('0.1'))}%)" if prev != 0 else ""
    good = (diff > 0) == good_when_up
    arrow = "▲" if diff > 0 else "▼"
    return (f'<span class="chip {"good" if good else "bad"}">{arrow} {esc(TEXT[lang]["vs"])} '
            f'{esc(("+" if diff > 0 else "−") + group(format(abs(diff).quantize(Decimal("0.01")), "f")))}{esc(pct)}</span>')


# ---- the sheet and the public entry points ---------------------------------------------------

FULL_W, PAIR_W, COMPACT_W = 1120, 548, 360
FULL_H, PAIR_H = 380, 400
COMPACT_H = {"trend": 380, "spending": 440, "change": 380, "networth": 380, "waterfall": 400}


def build_exhibits(report: dict[str, Any], series: dict[str, Any], lang: str, hide: bool, *,
                   layout: str = "standalone") -> dict[str, Exhibit]:
    """The five exhibits. `layout`: standalone (640 wide), desktop (full and half columns) or compact (a phone)."""
    if layout == "standalone":
        sizes = {k: (STANDALONE_W, HALF_H) for k in KINDS if k != "sheet"}
    elif layout == "desktop":
        sizes = {"trend": (FULL_W, FULL_H)} | {k: (PAIR_W, PAIR_H) for k in ("spending", "change", "networth", "waterfall")}
    else:
        sizes = {k: (COMPACT_W, COMPACT_H[k]) for k in KINDS if k != "sheet"}
    compact = layout == "compact"

    def size(kind: str) -> dict[str, Any]:
        return {"w": sizes[kind][0], "h": sizes[kind][1], "compact": compact}

    return {
        "trend": trend_exhibit(series, lang, hide, 1, **size("trend")),
        "spending": spending_exhibit(report, lang, hide, 2, **size("spending")),
        "change": change_exhibit(report, lang, hide, 3, **size("change")),
        "networth": networth_exhibit(series, lang, hide, 4, **size("networth")),
        "waterfall": waterfall_exhibit(report, lang, hide, 5, **size("waterfall")),
    }


def sheet_html(report: dict[str, Any], series: dict[str, Any], lang: str, hide: bool, *, generated: _dt.date) -> str:
    t, ccy = TEXT[lang], report["in"]
    desktop = build_exhibits(report, series, lang, hide, layout="desktop")
    compact = build_exhibits(report, series, lang, hide, layout="compact")
    mask = "••••"
    period = report["month"] + (f" · {t['month_to_date']}" if report["in_progress"] else "")
    prev = report["previous"]
    months = series["months"][-6:]
    spark = lambda key, fn=lambda v: v: sparkline([None if m[key] is None else float(fn(Decimal(m[key]))) for m in months])
    cur = {k: Decimal(report[k]["total"]) for k in ("income", "expenses")}
    cur["net"] = Decimal(report["net"])
    before = {k: Decimal(prev[k]) for k in ("income", "expenses", "net")} if prev["has_data"] and not prev.get("partial") and not report["partial"] else {}
    rate = report["savings_rate_percent"]
    worth = report["net_worth"]
    worth_end = Decimal(worth["end"]["total"])
    worth_prev = Decimal(worth["start"]["total"]) if not worth["start"]["partial"] and not worth["end"]["partial"] else None

    # the lead: what was left over, and where the income went
    net = cur["net"]
    lead_k = t["lead"] if net >= 0 else t["lead_neg"]
    if hide:
        lead_val = mask
    elif net >= 0:
        lead_val = ("+" if net > 0 else "") + group(report["net"])
    else:
        lead_val = group(report["net"])
    lead_cls = "pos" if net >= 0 else "neg"
    split = ""
    if cur["income"] > 0 and not report["partial"]:
        spent = min(cur["expenses"] * 100 / cur["income"], Decimal(100))
        kept = max(Decimal(100) - spent, Decimal(0))
        over = " over" if cur["expenses"] > cur["income"] else ""
        split = (f'<div class="split{over}" role="img" aria-label="{esc(t["split"])}">'
                 f'<span class="s-spent" style="width:{spent:.1f}%"></span><span class="s-kept" style="width:{kept:.1f}%"></span></div>'
                 f'<div class="split-legend"><span><i class="dot spent"></i>{esc(t["spending"])} {spent.quantize(Decimal("0.1"))}%</span>'
                 f'<span><i class="dot kept"></i>{esc(t["net"])} {(cur["net"] * 100 / cur["income"]).quantize(Decimal("0.1"))}%</span>'
                 f'<span class="muted">{esc(t["of_income"])}</span></div>')
    lead = (f'<div class="lead"><div class="k">{esc(lead_k)} · {esc(period)}</div>'
            f'<div class="big {lead_cls}">{esc(lead_val)}<small>{esc(ccy)}</small></div>{split}</div>')

    def kpi(label: str, value: str, chip: str, spark_svg: str) -> str:
        return (f'<div class="kpi"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div>'
                f'<div class="row"><div>{chip or "&nbsp;"}</div>{spark_svg}</div></div>')

    rates = [None if m["income"] is None or Decimal(m["income"]) <= 0 else float(Decimal(m["net"]) * 100 / Decimal(m["income"])) for m in months]
    kpis = "".join([
        kpi(t["income"], mask if hide else group(report["income"]["total"]),
            "" if hide or not before else _delta_chip(cur["income"], before["income"], good_when_up=True, lang=lang), spark("income")),
        kpi(t["spending"], mask if hide else group(report["expenses"]["total"]),
            "" if hide or not before else _delta_chip(cur["expenses"], before["expenses"], good_when_up=False, lang=lang), spark("expenses")),
        kpi(t["kpi_rate"], f"{rate}%" if rate is not None else "–", "", sparkline(rates)),
        kpi(t["net_worth"] + (" *" if worth["end"]["partial"] else ""), mask if hide else group(worth["end"]["total"]),
            "" if hide or worth_prev is None else _delta_chip(worth_end, worth_prev, good_when_up=True, lang=lang),
            spark("net_worth")),
    ])

    points = key_points(report, lang, hide)
    icons = {"info": "●", "up": "▲", "down": "▼", "warn": "!"}
    point_html = "".join(f'<li class="{kind}"><span class="ico">{icons[kind]}</span><span>{esc(text)}</span></li>' for kind, text in points)

    def card(name: str, wide: bool = False) -> str:
        d, c = desktop[name], compact[name]
        return (f'<div class="card{" wide" if wide else ""}"><div class="desk">{d.svg(standalone=False)}</div>'
                f'<div class="mob">{c.svg(standalone=False)}</div>{d.data_table()}</div>')

    checks = report["checks"]
    items = list(report["notes"]) + [t["transfer_note"], t["fx_note"]]
    if checks["pending_total"] == 0:
        items.append(t["pending_none"])
    if checks["accounts_with_assertion"]:
        items.append(t["sealed"].format(accounts=", ".join(checks["accounts_with_assertion"])))
    if checks["stale_rates"]:
        items.append(t["stale"].format(ccys=", ".join(checks["stale_rates"])))
    if hide:
        items.append(t["hidden"])
    notes = "".join(f"<li>{esc(i)}</li>" for i in items)

    def section(title: str) -> str:
        return f'<h2 class="sec"><span>{esc(title)}</span></h2>'

    return (
        f'<!doctype html><html lang="{"zh-CN" if lang == "zh" else "en"}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light dark"><title>{esc(t["sheet"])} {esc(report["month"])}</title>'
        f'<style>{_root_css()}{SVG_CSS}{PAGE_CSS}</style></head><body>'
        f'<header class="band"><div class="in"><div class="id"><span class="mark" aria-hidden="true"></span>'
        f'<div><div class="brand">Money Mom</div><h1>{esc(t["sheet"])}</h1></div></div>'
        f'<div class="meta"><div>{esc(t["period"])} <b>{esc(period)}</b></div>'
        f'<div>{esc(t["ccy_note"].format(ccy=ccy))} · {esc(t["generated"])} {generated.isoformat()}</div></div></div></header>'
        f'<main class="sheet"><section class="summary">{lead}<div class="kpis">{kpis}</div></section>'
        + (f'{section(t["points"])}<ul class="points">{point_html}</ul>' if points else "")
        + f'{section(t["charts"])}<div class="stack">{card("trend", True)}'
        f'<div class="pair">{card("spending")}{card("change")}</div>'
        f'<div class="pair">{card("networth")}{card("waterfall")}</div></div>'
        f'{section(t["notes"])}<ul class="notes">{notes}</ul>'
        f'<footer class="foot">{esc(t["source"])} {esc(t["not_advice"])}</footer></main></body></html>'
    )


def render(
    kind: str, report: dict[str, Any], series: dict[str, Any], *, lang: str = "zh", hide: bool = False,
    fmt: str = "html", generated: _dt.date | None = None,
) -> str:
    """The text of one chart: an HTML sheet, or a single exhibit as SVG."""
    if kind not in KINDS or lang not in LANGS or fmt not in ("html", "svg"):
        raise LedgerError(f"cannot draw kind={kind!r} lang={lang!r} format={fmt!r}", code="usage_error")
    if kind == "sheet":
        if fmt != "html":
            raise LedgerError("the sheet is several charts on one page: use --format html, or pick one chart for svg", code="usage_error")
        return sheet_html(report, series, lang, hide, generated=generated or _dt.date.today())
    exhibit = build_exhibits(report, series, lang, hide)[kind]
    exhibit.n = 1
    if fmt == "svg":
        return exhibit.svg(standalone=True)
    t = TEXT[lang]
    return (
        f'<!doctype html><html lang="{"zh-CN" if lang == "zh" else "en"}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light dark"><title>{esc(exhibit.summary)}</title>'
        f'<style>{_root_css()}{SVG_CSS}{PAGE_CSS}</style></head><body><main class="sheet" style="max-width:720px">'
        f'<div class="card">{exhibit.svg(standalone=False)}{exhibit.data_table()}</div>'
        f'<footer class="foot">{esc(t["not_advice"])}</footer></main></body></html>'
    )


def chart_data(kind: str, report: dict[str, Any], series: dict[str, Any]) -> dict[str, Any]:
    """The figures behind a chart, for an agent that wants to draw its own."""
    parts = {
        "spending": {"categories": report["categories"], "expenses": report["expenses"]},
        "change": {"categories": report["categories"], "previous": report["previous"]},
        "trend": {"months": series["months"]},
        "networth": {"months": series["months"]},
        "waterfall": {"income": report["income"], "categories": report["categories"], "net": report["net"]},
    }
    body = parts if kind == "sheet" else {kind: parts[kind]}
    return {"month": report["month"], "in": report["in"], "partial": report["partial"], "missing": report["missing"], **body}
