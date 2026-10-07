"""Charts: self-contained SVG exhibits and a one-page HTML sheet, drawn from the report figures.

Design brief: the restrained look of an equity-research note. Navy and steel on white, one amber accent, muted red
and green for direction, hairline horizontal gridlines only, tabular figures, a headline that states the finding,
and a source line under every exhibit. No gradients, shadows, 3-D or animation.

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
from decimal import Decimal
from typing import Any

from .errors import LedgerError

KINDS = ("sheet", "spending", "change", "trend", "networth", "waterfall")
LANGS = ("zh", "en")
FORMATS = ("html", "svg", "json")

HALF_W, HEIGHT = 640, 420
PAD = 24

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
}
DARK = {
    "bg": "#121A24", "page": "#0C121A", "ink": "#E6EBF1", "muted": "#9AA7B6", "faint": "#76828F", "grid": "#233041",
    "axis": "#52606F", "c1": "#8DB4E2", "c2": "#4F86C6", "c3": "#6F849C", "c4": "#4DB3B3", "accent": "#E0A94E",
    "pos": "#4DB584", "neg": "#E0716A", "band": "#0A1D33", "bandink": "#E6EBF1",
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
"""

PAGE_CSS = f"""
*{{box-sizing:border-box}}
html{{-webkit-text-size-adjust:100%}}
body{{margin:0;background:var(--page);color:var(--ink);font-family:{FONT};font-variant-numeric:tabular-nums;line-height:1.45}}
.band{{background:var(--band);color:var(--bandink)}}
.band .in{{max-width:1328px;margin:0 auto;padding:22px 24px 20px;display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap}}
.brand{{font-size:11px;letter-spacing:.18em;text-transform:uppercase;opacity:.75}}
.band h1{{margin:4px 0 0;font-size:22px;font-weight:600}}
.band .meta{{font-size:12px;opacity:.8;text-align:right}}
.wrap{{max-width:1328px;margin:0 auto;padding:20px 24px 40px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:16px}}
.kpi{{background:var(--bg);border:1px solid var(--grid);border-top:3px solid var(--c1);padding:12px 14px}}
.kpi .k{{font-size:11px;color:var(--muted);letter-spacing:.04em}}
.kpi .v{{font-size:22px;font-weight:600;margin-top:2px}}
.kpi .s{{font-size:11px;color:var(--faint);margin-top:2px}}
.kpi.accent{{border-top-color:var(--accent)}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:16px}}
.card{{background:var(--bg);border:1px solid var(--grid);padding:0}}
.card svg{{display:block;width:100%;height:auto}}
details{{border-top:1px solid var(--grid);padding:8px 16px 10px;font-size:12px;color:var(--muted)}}
summary{{cursor:pointer;color:var(--muted)}}
table{{border-collapse:collapse;width:100%;margin-top:8px}}
th,td{{padding:3px 6px;text-align:right;border-bottom:1px solid var(--grid);font-size:11.5px;color:var(--ink)}}
th{{color:var(--muted);font-weight:500}}
th:first-child,td:first-child{{text-align:left}}
.notes{{padding:18px 20px;font-size:13px;align-self:start}}
.notes h2{{margin:0 0 8px;font-size:15px}}
.notes ul{{margin:0;padding-left:18px}}
.notes li{{margin:4px 0;color:var(--ink)}}
.notes .muted{{color:var(--muted)}}
.foot{{margin-top:18px;font-size:11px;color:var(--faint)}}
@media print{{
  @page{{size:A4 landscape;margin:10mm}}
  :root{{{_vars(LIGHT)}}}
  body{{background:#fff}}.band{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  .card,.kpi{{break-inside:avoid}}details{{display:none}}
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


def wrap(text: str, size: float, width: float, lines: int = 2) -> list[str]:
    out, cur = [], ""
    for c in text:
        if cur and text_width(cur + c, size) > width:
            out.append(cur)
            cur = ""
            if len(out) == lines - 1:
                cur = text[len("".join(out)):]
                break
        cur += c
    out.append(fit(cur, size, width))
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
    def __init__(self, number: int, headline: str, subtitle: str, lang: str, *, hide: bool, width: int = HALF_W) -> None:
        self.n, self.headline, self.subtitle, self.lang, self.hide, self.w = number, headline, subtitle, lang, hide, width
        self.parts: list[str] = []
        self.table: tuple[list[str], list[list[str]]] | None = None
        self.warning: str | None = None
        self.summary = headline

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    def svg(self, *, standalone: bool) -> str:
        t = TEXT[self.lang]
        head = wrap(self.headline, 16, self.w - 2 * PAD, 2)
        y = 46
        out = [f'<text class="label" x="{PAD}" y="26">{esc(t["exhibit"].upper() if self.lang == "en" else t["exhibit"])} {self.n}</text>']
        for line in head:
            out.append(f'<text class="headline" x="{PAD}" y="{y}">{esc(line)}</text>')
            y += 21
        out.append(f'<text class="sub" x="{PAD}" y="{y + 1}">{esc(fit(self.subtitle, 11.5, self.w - 2 * PAD))}</text>')
        out += self.parts
        notes = ([self.warning] if self.warning else []) + ([t["hidden"]] if self.hide else [])
        for i, line in enumerate(reversed(notes)):
            cls = "warn" if line is self.warning else "small"
            out.append(f'<text class="{cls}" x="{PAD}" y="{HEIGHT - 30 - 13 * i}">{esc(fit(line, 10.5, self.w - 2 * PAD))}</text>')
        out.append(f'<line class="rule" x1="{PAD}" x2="{self.w - PAD}" y1="{HEIGHT - 24}" y2="{HEIGHT - 24}"/>')
        out.append(f'<text class="small" x="{PAD}" y="{HEIGHT - 10}">{esc(fit(t["source"], 10, self.w - 2 * PAD))}</text>')
        ns = ' xmlns="http://www.w3.org/2000/svg"' if standalone else ""
        style = f"<style>{_root_css()}{SVG_CSS}</style>" if standalone else ""
        bg = f'<rect class="ex-bg" width="{self.w}" height="{HEIGHT}"/>' if standalone else ""
        return (f'<svg{ns} viewBox="0 0 {self.w} {HEIGHT}" width="{self.w}" height="{HEIGHT}" role="img" '
                f'aria-label="{esc(self.summary)}"><title>{esc(self.summary)}</title>{style}{bg}{"".join(out)}</svg>')

    def data_table(self) -> str:
        if not self.table or self.hide:
            return ""
        heads, rows = self.table
        body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows)
        return (f"<details><summary>{esc(TEXT[self.lang]['data'])}</summary><table><thead><tr>"
                + "".join(f"<th>{esc(h)}</th>" for h in heads) + f"</tr></thead><tbody>{body}</tbody></table></details>")


def _placeholder(ex: Exhibit, message: str) -> Exhibit:
    ex.add(f'<text class="tick" x="{ex.w / 2}" y="{(HEIGHT + 90) / 2}" text-anchor="middle">{esc(message)}</text>')
    return ex


def _warn(ex: Exhibit, missing: list[str]) -> None:
    if missing:
        ex.warning = "! " + TEXT[ex.lang]["partial"].format(ccys=", ".join(missing))


PLOT_TOP, PLOT_BOTTOM = 124, 352


def _y_axis(ex: Exhibit, ticks: list[float], y_of, left: float, right: float) -> None:
    biggest = max(abs(tick) for tick in ticks)
    for tick in ticks:
        y = y_of(tick)
        cls = "axis" if tick == 0 else "grid"
        ex.add(f'<line class="{cls}" x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        if not ex.hide:
            ex.add(f'<text class="tick" x="{left - 8}" y="{y + 3.5:.1f}" text-anchor="end">{esc(axis_label(tick, ex.lang, biggest))}</text>')


# ---- 1. spending composition -----------------------------------------------------------------

def spending_exhibit(report: dict[str, Any], lang: str, hide: bool, number: int = 1, top: int = 7) -> Exhibit:
    t, ccy = TEXT[lang], report["in"]
    rows = [c for c in report["categories"] if c["converted"] is not None and Decimal(c["converted"]) > 0]
    total = Decimal(report["expenses"]["total"])
    ex = Exhibit(number, t["h_spend_none"], t["sub_spend"].format(ccy=ccy), lang, hide=hide)
    _warn(ex, report["missing"])
    if not rows or total <= 0:
        return _placeholder(ex, t["none"])
    shown, rest = (rows, []) if len(rows) <= top + 1 else (rows[:top], rows[top:])
    items = [(c["category"], Decimal(c["converted"]), c["share_percent"], c["change"], False) for c in shown]
    if rest:
        value = sum((Decimal(c["converted"]) for c in rest), Decimal(0))
        pct = None if report["expenses"]["missing"] else str((value * 100 / total).quantize(Decimal("0.1")))
        items.append((f'{t["other"]} ({len(rest)})', value, pct, None, True))
    first = items[0]
    ex.headline = (t["h_spend_hidden"] if hide else t["h_spend"]).format(
        ccy=ccy, total=group(report["expenses"]["total"]), top=first[0], share=first[2] or "–"
    )
    ex.summary = ex.headline
    top_y = PLOT_TOP
    label_w, bar_x = 118, PAD + 118 + 8
    amount_x, share_x, change_x = 500, 552, 616
    bar_w = amount_x - 76 - bar_x
    ex.add(f'<text class="colhead" x="{share_x}" y="{top_y - 8}" text-anchor="end">{esc(t["share"])}</text>')
    if not hide:
        ex.add(f'<text class="colhead" x="{amount_x}" y="{top_y - 8}" text-anchor="end">{esc(t["amount"])}</text>')
        ex.add(f'<text class="colhead" x="{change_x}" y="{top_y - 8}" text-anchor="end">{esc(t["vs_prev"])}</text>')
    ex.add(f'<line class="rule" x1="{PAD}" x2="{HALF_W - PAD}" y1="{top_y}" y2="{top_y}"/>')
    step = min(30.0, (PLOT_BOTTOM - top_y) / len(items))
    biggest = max(v for _, v, *_ in items)
    table = []
    for i, (name, value, pct, change, is_other) in enumerate(items):
        y = top_y + i * step
        mid = y + step / 2
        width = float(value / biggest) * bar_w
        tip = f"{name}: {pct or '–'}%" if hide else f"{name}: {group(format(value.quantize(Decimal('0.01')), 'f'))} {ccy} ({pct or '–'}%)"
        ex.add(f'<g class="bar"><title>{esc(tip)}</title>'
               f'<text class="cat" x="{PAD}" y="{mid + 3.5:.1f}">{esc(fit(name, 10.5, label_w))}</text>'
               f'<rect class="{"c3" if is_other else "c1"}" x="{bar_x}" y="{mid - 6:.1f}" width="{max(width, 1.5):.1f}" height="12" rx="1"/>')
        if not hide:
            ex.add(f'<text class="val" x="{amount_x}" y="{mid + 3.5:.1f}" text-anchor="end">{esc(group(format(value.quantize(Decimal("0.01")), "f")))}</text>')
        ex.add(f'<text class="val" x="{share_x}" y="{mid + 3.5:.1f}" text-anchor="end">{esc(pct + "%" if pct else "–")}</text>')
        if not hide:
            if change is None:
                ex.add(f'<text class="val" x="{change_x}" y="{mid + 3.5:.1f}" text-anchor="end" style="fill:var(--faint)">–</text>')
            else:
                cls = "tneg" if Decimal(change) > 0 else "tpos" if Decimal(change) < 0 else ""
                arrow = "▲ " if Decimal(change) > 0 else "▼ " if Decimal(change) < 0 else ""
                ex.add(f'<text class="val {cls}" x="{change_x}" y="{mid + 3.5:.1f}" text-anchor="end">{esc(arrow + signed(change))}</text>')
        ex.add("</g>")
        ex.add(f'<line class="rule" x1="{PAD}" x2="{HALF_W - PAD}" y1="{y + step:.1f}" y2="{y + step:.1f}"/>')
        table.append([name, group(format(value.quantize(Decimal("0.01")), "f")), (pct + "%") if pct else "–", signed(change) if change else "–"])
    ex.table = ([t["spending"], f"{t['amount']} ({ccy})", t["share"], t["vs_prev"]], table)
    return ex


# ---- 2. change against last month ------------------------------------------------------------

def change_exhibit(report: dict[str, Any], lang: str, hide: bool, number: int = 2, top: int = 8) -> Exhibit:
    t, ccy = TEXT[lang], report["in"]
    ex = Exhibit(number, t["h_change_none"], t["sub_change"].format(ccy=ccy), lang, hide=hide)
    _warn(ex, report["missing"])
    if not report["previous"]["has_data"]:
        return _placeholder(ex, t["need_prev"])
    rows = [(c["category"], Decimal(c["change"])) for c in report["categories"] if c["change"] is not None]
    rows = [r for r in rows if r[1] != 0]
    if not rows:
        return _placeholder(ex, t["h_change_none"])
    rows.sort(key=lambda r: (-abs(r[1]), r[0]))
    biggest = rows[0]
    word = t["word_up"] if biggest[1] > 0 else t["word_down"]
    ex.headline = (t["h_change_hidden"] if hide else t["h_change"]).format(
        cat=biggest[0], dir=word, delta=group(format(abs(biggest[1]), "f")))
    ex.summary = ex.headline
    rows = rows[:top]
    left, right = PAD + 118 + 8, HALF_W - PAD
    centre = (left + right) / 2
    room = (right - left) / 2 - (8 if hide else 62)
    scale = room / float(max(abs(v) for _, v in rows))
    ex.add(f'<line class="rule" x1="{PAD}" x2="{HALF_W - PAD}" y1="{PLOT_TOP}" y2="{PLOT_TOP}"/>')
    ex.add(f'<text class="colhead" x="{centre - 6}" y="{PLOT_TOP - 8}" text-anchor="end">◀ {esc(t["down"])}</text>')
    ex.add(f'<text class="colhead" x="{centre + 6}" y="{PLOT_TOP - 8}">{esc(t["up"])} ▶</text>')
    step = min(30.0, (PLOT_BOTTOM - PLOT_TOP) / len(rows))
    table = []
    for i, (name, delta) in enumerate(rows):
        mid = PLOT_TOP + i * step + step / 2
        w = float(abs(delta)) * scale
        x = centre if delta > 0 else centre - w
        tip = name if hide else f"{name}: {signed(format(delta, 'f'))} {ccy}"
        ex.add(f'<g class="bar"><title>{esc(tip)}</title>'
               f'<text class="cat" x="{PAD}" y="{mid + 3.5:.1f}">{esc(fit(name, 10.5, 118))}</text>'
               f'<rect class="{"neg" if delta > 0 else "pos"}" x="{x:.1f}" y="{mid - 6:.1f}" width="{max(w, 1.5):.1f}" height="12" rx="1"/>')
        if not hide:
            label = signed(format(delta, "f"))
            if delta > 0:
                ex.add(f'<text class="val" x="{centre + w + 6:.1f}" y="{mid + 3.5:.1f}">{esc(label)}</text>')
            else:
                ex.add(f'<text class="val" x="{centre - w - 6:.1f}" y="{mid + 3.5:.1f}" text-anchor="end">{esc(label)}</text>')
        ex.add("</g>")
        table.append([name, signed(format(delta, "f"))])
    ex.add(f'<line class="axis" x1="{centre:.1f}" x2="{centre:.1f}" y1="{PLOT_TOP}" y2="{PLOT_TOP + step * len(rows):.1f}"/>')
    ex.table = ([t["spending"], f"{t['vs_prev']} ({ccy})"], table)
    return ex


# ---- 3. monthly trend ------------------------------------------------------------------------

def _x_labels(ex: Exhibit, months: list[str], x_of, y: float) -> None:
    for i, month in enumerate(months):
        x = x_of(i)
        ex.add(f'<text class="tick" x="{x:.1f}" y="{y}" text-anchor="middle">{esc(month_label(month, ex.lang))}</text>')
        if i == 0 or month.endswith("-01"):
            ex.add(f'<text class="small" x="{x:.1f}" y="{y + 12}" text-anchor="middle">{month[:4]}</text>')


def trend_exhibit(series: dict[str, Any], lang: str, hide: bool, number: int = 3) -> Exhibit:
    t, ccy = TEXT[lang], series["in"]
    rows = series["months"]
    ex = Exhibit(number, t["h_trend_none"], t["sub_trend"].format(ccy=ccy), lang, hide=hide)
    have = [r for r in rows if r["income"] is not None]
    _warn(ex, sorted({c for r in rows for c in r["missing"]}))
    if not have:
        return _placeholder(ex, t["none"])
    spend = [Decimal(r["expenses"]) for r in have]
    last = have[-1]
    avg = (sum(spend, Decimal(0)) / len(spend)).quantize(Decimal("0.01"))
    ex.headline = (t["h_trend_hidden"] if hide else t["h_trend"]).format(
        n=len(rows), net=signed(last["net"]), avg=group(format(avg, "f"))
    )
    ex.summary = ex.headline
    values = [v for r in have for v in (_num(r["income"]), _num(r["expenses"]), _num(r["net"]))]
    ticks = nice_ticks(min(min(values), 0), max(values))
    lo, hi = ticks[0], ticks[-1]
    left, right, top, bottom = 64.0, HALF_W - PAD, PLOT_TOP + 8, PLOT_BOTTOM - 8
    y_of = lambda v: bottom - (v - lo) / (hi - lo) * (bottom - top)
    n = len(rows)
    band = (right - left) / n
    x_of = lambda i: left + band * (i + 0.5)
    _y_axis(ex, ticks, y_of, left, right)
    # legend
    lx = PAD
    for cls, name in (("c1", t["income"]), ("c2", t["spending"]), ("line", t["net"])):
        if cls == "line":
            ex.add(f'<line x1="{lx}" x2="{lx + 14}" y1="{PLOT_TOP - 12}" y2="{PLOT_TOP - 12}" style="stroke:var(--accent);stroke-width:2"/>')
        else:
            ex.add(f'<rect class="{cls}" x="{lx}" y="{PLOT_TOP - 17}" width="10" height="10" rx="1"/>')
        ex.add(f'<text class="tick" x="{lx + 18}" y="{PLOT_TOP - 8}">{esc(name)}</text>')
        lx += 18 + text_width(name, 10.5) + 18
    bar_w = min(band * 0.3, 22)
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
    _x_labels(ex, [r["month"] for r in rows], x_of, PLOT_BOTTOM + 14)
    ex.table = ([t["month"], t["income"], t["spending"], t["net"]], table)
    return ex


# ---- 4. net worth ----------------------------------------------------------------------------

def networth_exhibit(series: dict[str, Any], lang: str, hide: bool, number: int = 4) -> Exhibit:
    t, ccy = TEXT[lang], series["in"]
    rows = series["months"]
    ex = Exhibit(number, t["h_worth_one"].format(end=""), t["sub_worth"].format(ccy=ccy), lang, hide=hide)
    usable = [r for r in rows if not r["net_worth_partial"]]
    _warn(ex, sorted({c for r in rows for c in r["net_worth_missing"]}))
    if len(rows) < 2 or len(usable) < 1:
        ex.headline = t["none"] if not usable else t["h_worth_hidden"] if hide else t["h_worth_one"].format(end=f"{group(usable[-1]['net_worth'])} {ccy}")
        ex.summary = ex.headline
        return _placeholder(ex, t["need_two"] if usable else t["none"])
    end, first = usable[-1], usable[0]
    now = f"{group(end['net_worth'])} {ccy}"
    if hide:
        ex.headline = t["h_worth_hidden"]
    elif end is first:
        ex.headline = t["h_worth_one"].format(end=now)
    else:
        delta = format(Decimal(end["net_worth"]) - Decimal(first["net_worth"]), "f")
        ex.headline = t["h_worth"].format(end=now, since=first["month"], delta=signed(delta))
    ex.summary = ex.headline
    values = [_num(r["net_worth"]) for r in usable]
    ticks = nice_ticks(min(min(values), 0), max(max(values), 0))
    lo, hi = ticks[0], ticks[-1]
    left, right, top, bottom = 64.0, HALF_W - PAD - 10, PLOT_TOP + 8, PLOT_BOTTOM - 8
    y_of = lambda v: bottom - (v - lo) / (hi - lo) * (bottom - top)
    n = len(rows)
    step = (right - left) / max(n - 1, 1)
    x_of = lambda i: left + step * i
    _y_axis(ex, ticks, y_of, left, HALF_W - PAD)
    pts = [(x_of(i), y_of(_num(r["net_worth"])), r) for i, r in enumerate(rows) if not r["net_worth_partial"]]
    if len(pts) > 1:
        zero = y_of(0)
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
        ex.add(f'<polygon class="area-worth" points="{pts[0][0]:.1f},{zero:.1f} {poly} {pts[-1][0]:.1f},{zero:.1f}"/>')
        ex.add(f'<polyline class="line-worth" points="{poly}"/>')
    table = []
    for i, r in enumerate(rows):
        x = x_of(i)
        if r["net_worth_partial"]:
            table.append([r["month"], "–"])
            continue
        y = y_of(_num(r["net_worth"]))
        tip = r["month"] + ("" if hide else f': {group(r["net_worth"])} {ccy}')
        ex.add(f'<g class="bar"><title>{esc(tip)}</title><circle class="dot-worth" cx="{x:.1f}" cy="{y:.1f}" r="3.25"/></g>')
        table.append([r["month"], group(r["net_worth"])])
    if not hide:
        x, y, r = pts[-1]
        ex.add(f'<text class="val" x="{x:.1f}" y="{y - 10:.1f}" text-anchor="end" style="font-weight:600">{esc(group(r["net_worth"]))}</text>')
    _x_labels(ex, [r["month"] for r in rows], x_of, PLOT_BOTTOM + 14)
    ex.table = ([t["month"], f"{t['net_worth']} ({ccy})"], table)
    return ex


# ---- 5. waterfall ----------------------------------------------------------------------------

def waterfall_exhibit(report: dict[str, Any], lang: str, hide: bool, number: int = 5, top: int = 5) -> Exhibit:
    t, ccy = TEXT[lang], report["in"]
    ex = Exhibit(number, t["h_spend_none"], t["sub_water"].format(ccy=ccy), lang, hide=hide)
    _warn(ex, report["missing"])
    income = Decimal(report["income"]["total"])
    cats = [(c["category"], Decimal(c["converted"])) for c in report["categories"] if c["converted"] is not None and Decimal(c["converted"]) > 0]
    if report["entries"] == 0 or (income == 0 and not cats):
        return _placeholder(ex, t["none"])
    shown, rest = (cats, []) if len(cats) <= top + 1 else (cats[:top], cats[top:])
    steps: list[tuple[str, Decimal, str]] = [(t["income"], income, "total")]
    steps += [(name, -v, "down") for name, v in shown]
    if rest:
        steps.append((f'{t["other"]} ({len(rest)})', -sum((v for _, v in rest), Decimal(0)), "down"))
    net = Decimal(report["net"])
    steps.append((t["net"], net, "total"))
    pct = None if income <= 0 else str((net * 100 / income).quantize(Decimal("0.1")))
    if hide:
        ex.headline = f'{t["net"]} {pct}% {t["of_income"]}' if pct is not None else t["sub_water"].format(ccy=ccy)
    else:
        ex.headline = t["h_water"].format(income=group(report["income"]["total"]), net=signed(report["net"]))
    ex.summary = ex.headline
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
    left, right, top_y, bottom = 64.0, HALF_W - PAD, PLOT_TOP + 14, PLOT_BOTTOM - 8
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
        tip = name + (f": {(value * 100 / income).quantize(Decimal('0.1'))}%" if hide and income > 0 else "" if hide else f": {signed(format(value.quantize(Decimal('0.01')), 'f'))} {ccy}")
        ex.add(f'<g class="bar"><title>{esc(tip)}</title><rect class="{cls}" x="{x:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1 - y0, 1.5):.1f}" rx="1"/></g>')
        label = (f"{(value * 100 / income).quantize(Decimal('0.1'))}%" if income > 0 else "") if hide else signed(format(value.quantize(Decimal("0.01")), "f")) if kind == "down" else group(format(value.quantize(Decimal("0.01")), "f"))
        ly = (y0 - 5) if kind == "total" and value >= 0 else (y1 + 12)
        ex.add(f'<text class="val" x="{x + bw / 2:.1f}" y="{ly:.1f}" text-anchor="middle">{esc(label)}</text>')
        ex.add(f'<text class="cat" x="{x + bw / 2:.1f}" y="{PLOT_BOTTOM + 14}" text-anchor="middle">{esc(fit(name, 10.5, band - 4))}</text>')
        prev_end = yb
        table.append([name, signed(format(value.quantize(Decimal("0.01")), "f"))])
    ex.table = (["", ccy], table)
    return ex


# ---- the sheet and the public entry points ---------------------------------------------------

def build_exhibits(report: dict[str, Any], series: dict[str, Any], lang: str, hide: bool) -> dict[str, Exhibit]:
    return {
        "spending": spending_exhibit(report, lang, hide, 1),
        "change": change_exhibit(report, lang, hide, 2),
        "trend": trend_exhibit(series, lang, hide, 3),
        "networth": networth_exhibit(series, lang, hide, 4),
        "waterfall": waterfall_exhibit(report, lang, hide, 5),
    }


def _kpi(label: str, value: str, sub: str = "", accent: bool = False) -> str:
    return (f'<div class="kpi{" accent" if accent else ""}"><div class="k">{esc(label)}</div>'
            f'<div class="v">{esc(value)}</div><div class="s">{esc(sub) if sub else "&nbsp;"}</div></div>')


def sheet_html(report: dict[str, Any], series: dict[str, Any], lang: str, hide: bool, *, generated: _dt.date) -> str:
    t, ccy = TEXT[lang], report["in"]
    ex = build_exhibits(report, series, lang, hide)
    mask = "••••"
    period = report["month"] + (f" · {t['month_to_date']}" if report["in_progress"] else "")
    prev = report["previous"]
    spend_sub = ""
    if not hide and prev["has_data"] and prev.get("expenses_change") is not None:
        pct = f" ({signed(prev['expenses_change_percent'])}%)" if prev["expenses_change_percent"] is not None else ""
        spend_sub = f"{t['vs_prev']} {signed(prev['expenses_change'])}{pct}"
    rate = report["savings_rate_percent"]
    worth = report["net_worth"]
    kpis = "".join([
        _kpi(t["income"], mask if hide else group(report["income"]["total"]), ccy),
        _kpi(t["spending"], mask if hide else group(report["expenses"]["total"]), spend_sub or ccy),
        _kpi(t["net"], mask if hide else signed(report["net"]), ccy, accent=True),
        _kpi(t["kpi_rate"], f"{rate}%" if rate is not None else "–", t["of_income"]),
        _kpi(t["net_worth"], mask if hide else group(worth["end"]["total"]), worth["end"]["as_of"] + (" *" if worth["end"]["partial"] else "")),
    ])
    cards = "".join(f'<div class="card">{e.svg(standalone=False)}{e.data_table()}</div>' for e in ex.values())
    checks = report["checks"]
    items = list(report["notes"])
    items.append(t["transfer_note"])
    items.append(t["fx_note"])
    if checks["pending_total"] == 0:
        items.append(t["pending_none"])
    if checks["accounts_with_assertion"]:
        items.append(t["sealed"].format(accounts=", ".join(checks["accounts_with_assertion"])))
    if checks["stale_rates"]:
        items.append(t["stale"].format(ccys=", ".join(checks["stale_rates"])))
    if hide:
        items.append(t["hidden"])
    notes = (f'<div class="card notes"><h2>{esc(t["notes"])}</h2><ul>'
             + "".join(f"<li>{esc(i)}</li>" for i in items) + f'</ul><p class="muted">{esc(t["not_advice"])}</p></div>')
    return (
        f'<!doctype html><html lang="{"zh-CN" if lang == "zh" else "en"}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light dark"><title>{esc(t["sheet"])} {esc(report["month"])}</title>'
        f'<style>{_root_css()}{SVG_CSS}{PAGE_CSS}</style></head><body>'
        f'<div class="band"><div class="in"><div><div class="brand">Money Mom</div><h1>{esc(t["sheet"])}</h1></div>'
        f'<div class="meta">{esc(t["period"])} {esc(period)}<br>{esc(t["ccy_note"].format(ccy=ccy))} · '
        f'{esc(t["generated"])} {generated.isoformat()}</div></div></div>'
        f'<div class="wrap"><div class="kpis">{kpis}</div><div class="grid2">{cards}{notes}</div>'
        f'<div class="foot">{esc(t["source"])} {esc(t["not_advice"])}</div></div></body></html>'
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
    exhibits = build_exhibits(report, series, lang, hide)
    exhibit = exhibits[kind]
    exhibit.n = 1
    if fmt == "svg":
        return exhibit.svg(standalone=True)
    t = TEXT[lang]
    return (
        f'<!doctype html><html lang="{"zh-CN" if lang == "zh" else "en"}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light dark"><title>{esc(exhibit.summary)}</title>'
        f'<style>{_root_css()}{SVG_CSS}{PAGE_CSS}</style></head><body><div class="wrap" style="max-width:720px">'
        f'<div class="card">{exhibit.svg(standalone=False)}{exhibit.data_table()}</div>'
        f'<div class="foot">{esc(t["not_advice"])}</div></div></body></html>'
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
