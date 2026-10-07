import json
import unittest
from decimal import Decimal

from money_mom import ValidationError
from money_mom.events import Assert, Confirm, Open, Txn, Void, parse_event
from money_mom.ids import IdGenerator

ACTOR = {"type": "human", "name": "user"}


def txn(**over):
    base = {
        "v": 1,
        "id": "T1",
        "kind": "txn",
        "ts": "2026-10-07T12:00:00+08:00",
        "actor": ACTOR,
        "date": "2026-10-07",
        "status": "posted",
        "postings": [
            {"account": "Expenses:Dining:Coffee", "amount": "38.00", "ccy": "CNY"},
            {"account": "Assets:Alipay", "amount": "-38.00", "ccy": "CNY"},
        ],
    }
    base.update(over)
    return base


def code_of(obj):
    with unittest.TestCase().assertRaises(ValidationError) as ctx:
        parse_event(obj)
    return ctx.exception.code


class ParseValidEvents(unittest.TestCase):
    def test_documented_examples_parse(self):
        lines = [
            '{"v":1,"id":"01J9XK0001","kind":"open","ts":"2026-10-01T09:00:00+08:00","actor":{"type":"human","name":"init"},"account":"Assets:Alipay","date":"2026-10-01","currencies":["CNY"]}',
            '{"v":1,"id":"01J9XK0002","kind":"txn","ts":"2026-10-07T12:31:05+08:00","actor":{"type":"agent","name":"claude-code"},"source":{"type":"chat","ref":"午饭瑞幸 38，支付宝付的"},"confidence":0.96,"date":"2026-10-07","status":"posted","narration":"午饭","payee":"瑞幸","postings":[{"account":"Expenses:Dining:Coffee","amount":"38.00","ccy":"CNY"},{"account":"Assets:Alipay","amount":"-38.00","ccy":"CNY"}]}',
            '{"v":1,"id":"01J9XK0003","kind":"txn","ts":"2026-10-07T12:40:10+08:00","actor":{"type":"agent","name":"claude-code"},"source":{"type":"chat","ref":"给小王转了 200"},"confidence":0.55,"date":"2026-10-07","status":"pending","payee":"小王","postings":[{"account":null,"amount":"200.00","ccy":"CNY"},{"account":"Assets:Alipay","amount":"-200.00","ccy":"CNY"}]}',
            '{"v":1,"id":"01J9XK0004","kind":"confirm","ts":"2026-10-07T12:41:30+08:00","actor":{"type":"human","name":"user"},"target":"01J9XK0003","postings":[{"account":"Assets:Receivable:小王","amount":"200.00","ccy":"CNY"},{"account":"Assets:Alipay","amount":"-200.00","ccy":"CNY"}]}',
            '{"v":1,"id":"01J9XK0005","kind":"void","ts":"2026-10-08T09:02:00+08:00","actor":{"type":"human","name":"user"},"target":"01J9XK0002","reason":"重复录入"}',
            '{"v":1,"id":"01J9XK0006","kind":"assert","ts":"2026-10-08T09:10:00+08:00","actor":{"type":"human","name":"user"},"date":"2026-09-30","account":"Assets:CMB","amount":"12480.55","ccy":"CNY"}',
        ]
        kinds = [type(parse_event(json.loads(line))) for line in lines]
        self.assertEqual(kinds, [Open, Txn, Txn, Confirm, Void, Assert])

    def test_amounts_are_exact_decimals(self):
        ev = parse_event(txn())
        self.assertEqual(ev.postings[0].amount, Decimal("38.00"))
        self.assertEqual(str(ev.postings[1].amount), "-38.00")

    def test_chinese_account_names_are_allowed(self):
        ev = parse_event(
            txn(postings=[
                {"account": "Assets:招行:储蓄卡", "amount": "-1", "ccy": "CNY"},
                {"account": "Expenses:餐饮", "amount": "1", "ccy": "CNY"},
            ])
        )
        self.assertEqual(ev.postings[0].account, "Assets:招行:储蓄卡")

    def test_pending_allows_null_account(self):
        ev = parse_event(txn(status="pending", postings=[
            {"account": None, "amount": "200.00", "ccy": "CNY"},
            {"account": "Assets:Alipay", "amount": "-200.00", "ccy": "CNY"},
        ]))
        self.assertIsNone(ev.postings[0].account)


class RejectInvalidEvents(unittest.TestCase):
    def test_unknown_kind_and_fields(self):
        self.assertEqual(code_of(txn(kind="refund")), "unknown_kind")
        self.assertEqual(code_of(txn(colour="red")), "unknown_field")

    def test_version(self):
        self.assertEqual(code_of(txn(v=2)), "unsupported_version")
        self.assertEqual(code_of(txn(v=True)), "unsupported_version")
        self.assertEqual(code_of(txn(v="1")), "unsupported_version")

    def test_missing_required(self):
        obj = txn()
        del obj["date"]
        self.assertEqual(code_of(obj), "missing_field")

    def test_amounts_must_be_plain_decimal_strings(self):
        for bad in ("1e3", "38.0.0", " 38", "+38", "NaN", "٣٨", "", "1,000.00"):
            post = [{"account": "Assets:A", "amount": bad, "ccy": "CNY"},
                    {"account": "Assets:B", "amount": "1", "ccy": "CNY"}]
            self.assertEqual(code_of(txn(postings=post)), "invalid_amount", bad)
        post = [{"account": "Assets:A", "amount": 38.5, "ccy": "CNY"},
                {"account": "Assets:B", "amount": "1", "ccy": "CNY"}]
        self.assertEqual(code_of(txn(postings=post)), "invalid_amount")

    def test_timestamps_need_an_offset(self):
        self.assertEqual(code_of(txn(ts="2026-10-07T12:00:00")), "invalid_ts")
        self.assertEqual(code_of(txn(ts="2026-10-07")), "invalid_ts")
        self.assertEqual(code_of(txn(ts=1700000000)), "invalid_ts")
        parse_event(txn(ts="2026-10-07T04:00:00Z"))

    def test_dates(self):
        self.assertEqual(code_of(txn(date="2026-02-30")), "invalid_date")
        self.assertEqual(code_of(txn(date="20261007")), "invalid_date")

    def test_account_names(self):
        for bad in ("Cash", "Assets", "Assets:", "Assets::X", "Bank:Cash", "Assets:-x", "Assets:a b",
                    "Assets:e\u0301"):  # last one is not NFC
            post = [{"account": bad, "amount": "1", "ccy": "CNY"},
                    {"account": "Assets:B", "amount": "-1", "ccy": "CNY"}]
            self.assertEqual(code_of(txn(postings=post)), "invalid_account", bad)

    def test_currency(self):
        for bad in ("cny", "", "C NY", "X" * 25):
            post = [{"account": "Assets:A", "amount": "1", "ccy": bad},
                    {"account": "Assets:B", "amount": "-1", "ccy": "CNY"}]
            self.assertEqual(code_of(txn(postings=post)), "invalid_currency", repr(bad))

    def test_postings_shape(self):
        self.assertEqual(code_of(txn(postings=[])), "invalid_postings")
        one = [{"account": "Assets:A", "amount": "1", "ccy": "CNY"}]
        self.assertEqual(code_of(txn(postings=one)), "invalid_postings")
        extra = [{"account": "Assets:A", "amount": "1", "ccy": "CNY", "note": "x"},
                 {"account": "Assets:B", "amount": "-1", "ccy": "CNY"}]
        self.assertEqual(code_of(txn(postings=extra)), "unknown_field")
        priced = [{"account": "Assets:A", "amount": "1", "ccy": "USD", "price": {"amount": "7", "ccy": "CNY"}},
                  {"account": "Assets:B", "amount": "-7", "ccy": "CNY"}]
        self.assertEqual(code_of(txn(postings=priced)), "unsupported_field")
        no_account = [{"amount": "1", "ccy": "CNY"}, {"account": "Assets:B", "amount": "-1", "ccy": "CNY"}]
        self.assertEqual(code_of(txn(postings=no_account)), "missing_field")

    def test_status(self):
        self.assertEqual(code_of(txn(status="draft")), "invalid_status")

    def test_actor(self):
        self.assertEqual(code_of(txn(actor={"type": "robot", "name": "x"})), "invalid_actor")
        self.assertEqual(code_of(txn(actor={"type": "human"})), "invalid_actor")
        self.assertEqual(code_of(txn(actor={"type": "human", "name": " "})), "invalid_actor")

    def test_confidence(self):
        self.assertEqual(code_of(txn(confidence=1.5)), "invalid_confidence")
        self.assertEqual(code_of(txn(confidence=True)), "invalid_confidence")
        self.assertEqual(code_of(txn(confidence="high")), "invalid_confidence")
        parse_event(txn(confidence=None))
        parse_event(txn(confidence=0))

    def test_source_must_not_leak_absolute_paths(self):
        for ref in ("/Users/me/bank.csv", "~/bank.csv", "C:\\Users\\me\\bank.csv", "C:/x.csv", "\\\\server\\x"):
            self.assertEqual(code_of(txn(source={"type": "file", "ref": ref})), "invalid_source", ref)
        parse_event(txn(source={"type": "file", "ref": "alipay_2026-09.csv"}))
        parse_event(txn(source={"type": "chat", "ref": "/help 午饭"}))  # chat text is free-form
        self.assertEqual(code_of(txn(source={"type": "email"})), "invalid_source")
        self.assertEqual(code_of(txn(source={"type": "file", "sha256": "ABC"})), "invalid_source")

    def test_void_requires_a_reason(self):
        base = {"v": 1, "id": "V1", "kind": "void", "ts": "2026-10-07T12:00:00+08:00",
                "actor": ACTOR, "target": "T1"}
        self.assertEqual(code_of(base), "missing_field")
        self.assertEqual(code_of({**base, "reason": "  "}), "invalid_field")

    def test_error_carries_location(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_event(txn(status="draft"), where="2026-10.jsonl:7")
        self.assertEqual(ctx.exception.where, "2026-10.jsonl:7")
        self.assertIn("2026-10.jsonl:7", str(ctx.exception))

    def test_ids(self):
        self.assertEqual(code_of(txn(id="has space")), "invalid_id")
        self.assertEqual(code_of(txn(id="")), "invalid_id")
        self.assertEqual(code_of(txn(id="x" * 65)), "invalid_id")


class IdTests(unittest.TestCase):
    def test_format_and_monotonic(self):
        ms = [1_700_000_000_000]
        gen = IdGenerator(clock_ms=lambda: ms[0], randbytes=lambda n: b"\x00" * n)
        first, second = gen.new(), gen.new()  # same millisecond
        self.assertEqual(len(first), 26)
        self.assertLess(first, second)
        ms[0] -= 5000  # clock steps backwards
        third = gen.new()
        self.assertLess(second, third)
        ms[0] += 10_000
        self.assertLess(third, gen.new())

    def test_ids_are_valid_event_ids(self):
        gen = IdGenerator()
        ids = {gen.new() for _ in range(1000)}
        self.assertEqual(len(ids), 1000)
        parse_event(txn(id=next(iter(ids))))


if __name__ == "__main__":
    unittest.main()
