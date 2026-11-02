"""
Tests for the forward paper trader (RESEARCH.md #7) with Kraken mocked.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import paper_momentum as pm  # noqa: E402
import terminal_data as td  # noqa: E402

DAY = 86400
NOW = 1_800_000_000 // DAY * DAY + 3600  # an hour after a daily close


class FakeKraken:
    """Daily candles where each coin grows at its own daily rate; BTC trend switchable."""

    def __init__(self, rates: dict, days: int = 150):
        self.rates = rates
        self.days = days
        self.now = NOW
        self.origin = NOW - days * DAY  # fixed, so prices move as time passes

    def price(self, pair: str, t: int) -> float:
        k = (t - self.origin) / DAY
        return 100 * (1 + self.rates.get(pair, 0.0)) ** k

    def get_ohlc(self, pair, interval=1440):
        start = self.now // DAY * DAY - self.days * DAY
        out = []
        for i in range(self.days + 1):  # includes the still-forming candle
            t = start + i * DAY
            p = self.price(pair, t)
            out.append([t, p, p, p, p, p, 1.0, 1])
        return out

    def get_ticker(self, pair):
        return {"c": [str(self.price(pair, self.now)), "1"]}


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmp.name) / "momentum_ledger.json"
        self.fake = FakeKraken({"XBTUSD": 0.003, "SOLUSD": 0.01, "ETHUSD": 0.006, "ADAUSD": 0.004,
                                "XDGUSD": -0.01})
        self.patches = [
            mock.patch.object(pm, "LEDGER", self.ledger_path),
            mock.patch.object(pm.kc, "get_ohlc", side_effect=self.fake.get_ohlc),
            mock.patch.object(pm.kc, "get_ticker", side_effect=self.fake.get_ticker),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_at(self, t):
        self.fake.now = t
        with contextlib.redirect_stdout(io.StringIO()):
            return pm.run(now=t)

    def test_first_run_buys_top_three_and_pays_costs(self):
        ledger = self.run_at(NOW)
        self.assertEqual(sorted(ledger["holdings"]), ["ADAUSD", "ETHUSD", "SOLUSD"])
        self.assertEqual(len(ledger["trades"]), 3)
        s = pm.summary(ledger)
        self.assertLess(s["value"], 1000)          # fees + slippage on the way in
        self.assertGreater(s["value"], 1000 * 0.99)
        self.assertTrue(ledger["weeks"][0]["decision"]["market_ok"])

    def test_completed_candles_only(self):
        closes = pm.completed_closes("SOLUSD", NOW)
        self.assertEqual(len(closes), self.fake.days)  # the forming candle is dropped

    def test_second_run_same_week_only_marks(self):
        self.run_at(NOW)
        ledger = self.run_at(NOW + 2 * DAY)
        self.assertEqual(len(ledger["trades"]), 3)
        self.assertEqual(len(ledger["weeks"]), 1)
        self.assertEqual(len(ledger["marks"]), 1)
        self.assertGreater(pm.summary(ledger)["value"], 1000)  # holdings grew

    def test_btc_below_average_moves_to_cash(self):
        self.run_at(NOW)
        self.fake.rates["XBTUSD"] = -0.02  # BTC now in a steep downtrend over the whole history
        ledger = self.run_at(NOW + 7 * DAY)
        self.assertEqual(ledger["holdings"], {})
        self.assertFalse(ledger["weeks"][-1]["decision"]["market_ok"])
        self.assertEqual(len(ledger["trades"]), 6)  # 3 buys, then 3 sells

    def test_ledger_persists_and_status_prints(self):
        self.run_at(NOW)
        ledger = pm.load()
        self.assertEqual(ledger["start_value"], 1000.0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pm.print_status(ledger)
        self.assertIn("PAPER MOMENTUM", out.getvalue())

    def test_terminal_screen(self):
        self.run_at(NOW)
        with mock.patch.object(td, "short_name", side_effect=lambda p: p.replace("USD", "")):
            v = td.paper(ledger_path=self.ledger_path)
        rows = dict((r[0], r[1]) for r in v.rows)
        self.assertEqual(rows["Holding"], "ADA, ETH, SOL")
        self.assertEqual(rows["BTC filter"], ("ON", "up"))
        self.assertTrue(v.series)


if __name__ == "__main__":
    unittest.main()
