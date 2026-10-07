import datetime as dt
import tempfile
import unittest
from pathlib import Path

from money_mom import Ledger

TZ = dt.timezone(dt.timedelta(hours=8))
HUMAN = ("human", "user")
AGENT = ("agent", "test-agent")


class StepClock:
    """Deterministic clock that moves forward one second per call."""

    def __init__(self, start: dt.datetime | None = None) -> None:
        self.now = start or dt.datetime(2026, 10, 7, 12, 0, 0, tzinfo=TZ)

    def __call__(self) -> dt.datetime:
        self.now += dt.timedelta(seconds=1)
        return self.now


class LedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "mm"
        self.clock = StepClock()
        self.ledger = Ledger.init(self.root, clock=self.clock)

    def open_accounts(self) -> None:
        led = self.ledger
        led.open_account("Assets:Alipay", "2026-10-01", currencies=["CNY"])
        led.open_account("Assets:CMB", "2026-10-01")
        led.open_account("Assets:Receivable:小王", "2026-10-01")
        led.open_account("Expenses:Dining:Coffee", "2026-10-01")
        led.open_account("Expenses:Gift", "2026-10-01")
        led.open_account("Income:Salary", "2026-10-01")

    def fund(self, account: str = "Assets:CMB", amount: str = "1000.00", date: str = "2026-10-01"):
        return self.ledger.add_txn(
            date, [(account, amount, "CNY"), ("Income:Salary", f"-{amount}", "CNY")], narration="fund"
        )

    def event_files(self) -> dict[str, str]:
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(self.ledger.ledger_dir.glob("*.jsonl"))}

    def total_lines(self) -> int:
        return sum(len([l for l in text.split("\n") if l]) for text in self.event_files().values())
