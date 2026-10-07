"""Starter account trees. Each template is an opening set of accounts plus friendly aliases."""

from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING

from .errors import LedgerError

if TYPE_CHECKING:
    from .ledger import Ledger

_CN_ACCOUNTS = """
Assets:现金 Assets:支付宝 Assets:微信 Assets:银行卡 Assets:应收
Liabilities:信用卡 Liabilities:花呗
Equity:期初余额 Equity:调整 Equity:汇兑
Income:工资 Income:奖金 Income:利息 Income:其他收入
Expenses:餐饮 Expenses:餐饮:早餐 Expenses:餐饮:午餐 Expenses:餐饮:晚餐
Expenses:餐饮:咖啡 Expenses:餐饮:外卖 Expenses:餐饮:聚餐
Expenses:交通 Expenses:交通:地铁公交 Expenses:交通:打车 Expenses:交通:加油 Expenses:交通:停车
Expenses:购物 Expenses:购物:服饰 Expenses:购物:数码 Expenses:购物:日用
Expenses:居住 Expenses:居住:房租 Expenses:居住:水电燃气 Expenses:居住:物业 Expenses:居住:网络
Expenses:通讯 Expenses:订阅
Expenses:娱乐 Expenses:娱乐:电影 Expenses:娱乐:游戏 Expenses:娱乐:旅行
Expenses:医疗 Expenses:教育
Expenses:人情 Expenses:人情:礼金 Expenses:人情:请客
Expenses:其他支出
""".split()

_EN_ACCOUNTS = """
Assets:Cash Assets:Checking Assets:Savings Assets:Receivable
Liabilities:CreditCard
Equity:Opening-Balances Equity:Adjustments Equity:Conversions
Income:Salary Income:Bonus Income:Interest Income:Other
Expenses:Food Expenses:Food:Groceries Expenses:Food:Restaurants Expenses:Food:Coffee
Expenses:Transport Expenses:Housing Expenses:Housing:Rent Expenses:Housing:Utilities
Expenses:Shopping Expenses:Subscriptions Expenses:Health Expenses:Education
Expenses:Entertainment Expenses:Gifts Expenses:Other
""".split()

TEMPLATES: dict[str, dict] = {
    "cn": {
        "description": "中文账户树：现金、支付宝、微信、银行卡、信用卡，以及常见收支分类",
        "accounts": _CN_ACCOUNTS,
        "aliases": {
            "alipay": "Assets:支付宝",
            "微信支付": "Assets:微信",
            "wechat": "Assets:微信",
            "cash": "Assets:现金",
            "opening": "Equity:期初余额",
            "期初": "Equity:期初余额",
        },
    },
    "en": {
        "description": "English account tree: cash, checking, savings, credit card, common categories",
        "accounts": _EN_ACCOUNTS,
        "aliases": {
            "cash": "Assets:Cash",
            "checking": "Assets:Checking",
            "savings": "Assets:Savings",
            "card": "Liabilities:CreditCard",
            "opening": "Equity:Opening-Balances",
        },
    },
}


def apply_template(ledger: "Ledger", name: str, date: _dt.date | str, actor: tuple[str, str]) -> int:
    """Open every account of the template in one atomic write and install its aliases."""
    template = TEMPLATES.get(name)
    if template is None:
        raise LedgerError(
            f"unknown template {name!r}; available: {', '.join(sorted(TEMPLATES))}",
            code="unknown_template",
        )
    events = [
        ledger.new_event("open", actor=actor, account=account, date=str(date))
        for account in template["accounts"]
    ]
    ledger.append_many(events, clamp_ts=True)
    for alias, account in template["aliases"].items():
        ledger.set_alias(alias, account)
    return len(events)
