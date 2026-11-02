"""
Tests for dca_backtest.py on hand-built price paths.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import dca_backtest as dca  # noqa: E402
from backtester import Costs  # noqa: E402
from momentum_backtest import Universe  # noqa: E402

DAY = 86400
FREE = Costs(0.0, 0.0)


def uni(prices: list) -> Universe:
    return Universe({"XBTUSD": [[i * DAY, p, p, p, p, p, 1.0, 1] for i, p in enumerate(prices)]})


class DcaTests(unittest.TestCase):
    def test_plain_dca_flat_price_keeps_money(self):
        u = uni([100.0] * 120)
        r = dca.simulate(u, {"XBTUSD": 1.0}, "plain", 10, 20, FREE)
        self.assertEqual(r["contributed"], 25.0 * 15)  # weeks starting days 20,27,...,118
        self.assertAlmostEqual(r["final"], r["contributed"])
        self.assertAlmostEqual(r["max_drawdown"], 0.0)

    def test_costs_are_charged(self):
        u = uni([100.0] * 60)
        r = dca.simulate(u, {"XBTUSD": 1.0}, "plain", 10, 20, Costs(0.004, 0.0005))
        self.assertLess(r["final"], r["contributed"])
        self.assertGreater(r["fees"], 0)

    def test_switch_sells_in_a_crash_and_cuts_drawdown(self):
        prices = [100 + i for i in range(150)] + [250 * 0.98 ** i for i in range(150)]
        u = uni(prices)
        plain = dca.simulate(u, {"XBTUSD": 1.0}, "plain", 50, 60, FREE)
        switch = dca.simulate(u, {"XBTUSD": 1.0}, "switch", 50, 60, FREE)
        pause = dca.simulate(u, {"XBTUSD": 1.0}, "pause", 50, 60, FREE)
        self.assertLess(switch["max_drawdown"], plain["max_drawdown"] / 2)
        self.assertGreater(switch["final"], plain["final"])
        self.assertGreater(pause["final"], plain["final"])        # stopped buying the fall
        self.assertLess(switch["invested"], plain["invested"])

    def test_pause_never_sells(self):
        prices = [100 + i for i in range(100)] + [200 - i for i in range(100)]
        u = uni(prices)
        r = dca.simulate(u, {"XBTUSD": 1.0}, "pause", 20, 30, FREE)
        plain = dca.simulate(u, {"XBTUSD": 1.0}, "plain", 20, 30, FREE)
        self.assertLessEqual(r["trades"], plain["trades"])

    def test_no_market_yet_keeps_cash(self):
        u = Universe({"XBTUSD": [[i * DAY, 100.0, 100.0, 100.0, 100.0, 100.0, 1.0, 1] for i in range(60)],
                      "ETHUSD": [[i * DAY, 10.0, 10.0, 10.0, 10.0, 10.0, 1.0, 1] for i in range(40, 60)]})
        r = dca.simulate(u, {"XBTUSD": 0.5, "ETHUSD": 0.5}, "plain", 10, 10, FREE)
        self.assertAlmostEqual(r["final"], r["contributed"])  # ETH's half waited in cash, then bought

    def test_year_windows_and_pass_rule(self):
        start = 1483228800 - 200 * DAY  # 200 days before 2017-01-01
        n = 365 * 8
        candles = [[start + i * DAY, 100 * 1.001 ** i] * 1 for i in range(n)]
        u = Universe({"XBTUSD": [[t, p, p, p, p, p, 1.0, 1] for t, p in candles]})
        self.assertEqual(u.days[dca.year_index(u, 2017)], 1483228800)
        rows = dca.evaluate(u, {"XBTUSD": 1.0}, 100, FREE)
        self.assertEqual([r["year"] for r in rows], list(range(2017, 2024)))
        self.assertTrue(all(not r["pass"] for r in rows))  # smooth uptrend: nothing to protect from


if __name__ == "__main__":
    unittest.main()
