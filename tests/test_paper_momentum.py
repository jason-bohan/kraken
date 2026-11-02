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

    def test_first_scheduled_run_rebalances_even_if_started_late_in_the_day(self):
        """Started Monday 23:23, schedule Mondays 01:17: the next run is only ~6.1 days later."""
        self.run_at(NOW)
        ledger = self.run_at(NOW + 6 * DAY + 2 * 3600)
        self.assertEqual(len(ledger["weeks"]), 2)
        ledger = self.run_at(NOW + 8 * DAY)  # an extra run two days later does not rebalance
        self.assertEqual(len(ledger["weeks"]), 2)

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


class DemoTests(unittest.TestCase):
    """Prices rise for 200 days, then everything falls for the last 40: the demo should find the last buy."""

    def setUp(self):
        self.days = 240
        self.turn = 200

    def price(self, pair, i):
        rate = {"XBTUSD": 0.004, "SOLUSD": 0.012, "ETHUSD": 0.008, "ADAUSD": 0.006}.get(pair, -0.002)
        if i <= self.turn:
            return 100 * (1 + rate) ** i
        return 100 * (1 + rate) ** self.turn * 0.99 ** (i - self.turn)

    def get_ohlc(self, pair, interval=1440):
        start = NOW // DAY * DAY - self.days * DAY
        return [[start + i * DAY, *[self.price(pair, i)] * 5, 1.0, 1] for i in range(self.days + 1)]

    def get_ticker(self, pair):
        return {"c": [str(self.price(pair, self.days)), "1"]}

    def test_demo_finds_last_buy_and_never_touches_the_ledger(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(pm, "LEDGER", Path(d) / "momentum_ledger.json"), \
             mock.patch.object(pm.kc, "get_ohlc", side_effect=self.get_ohlc), \
             mock.patch.object(pm.kc, "get_ticker", side_effect=self.get_ticker):
            result = pm.demo(now=NOW)
            self.assertFalse((Path(d) / "momentum_ledger.json").exists())
        # Last buy day is a few days into the fall, when only the strongest coin still had a
        # positive 28-day return.
        self.assertEqual(result["picks"], ["SOLUSD"])
        self.assertEqual([(t["side"], t["pair"]) for t in result["trades"]], [("buy", "SOLUSD")])
        self.assertGreater(result["days_ago"], 1)          # recent days: everything falling, no buy
        self.assertLess(result["value_today"], result["value_after_buy"])  # bought before the fall
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pm.print_demo(result)
        self.assertIn("NOT recorded", out.getvalue())


if __name__ == "__main__":
    unittest.main()
