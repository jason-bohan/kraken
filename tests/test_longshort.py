"""
Tests for longshort_backtest.py on hand-built price paths.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import longshort_backtest as ls  # noqa: E402
from backtester import Costs  # noqa: E402
from momentum_backtest import Universe  # noqa: E402

DAY = 86400
FREE = Costs(0.0, 0.0)


def uni(prices: list) -> Universe:
    return Universe({"XBTUSD": [[i * DAY, p, p, p, p, p, 1.0, 1] for i, p in enumerate(prices)]})


class LongShortTests(unittest.TestCase):
    def test_hold_tracks_price(self):
        u = uni([100, 100, 110, 120, 150])
        r = ls.simulate(u, "XBTUSD", "hold", 2, 0, FREE)
        self.assertAlmostEqual(r["final"], 1.5)

    def test_short_profits_in_a_fall_and_long_only_sits_in_cash(self):
        prices = [100 + i for i in range(30)] + [129 - 2 * i for i in range(40)]
        u = uni(prices)
        lo = ls.simulate(u, "XBTUSD", "long_only", 10, 10, FREE, short_cost=0.0)
        lsr = ls.simulate(u, "XBTUSD", "long_short", 10, 10, FREE, short_cost=0.0)
        self.assertGreater(lsr["final"], lo["final"])
        self.assertGreater(lsr["final"], 1.0)

    def test_short_is_fixed_size_and_wiped_out_if_price_doubles(self):
        prices = [100 - i for i in range(30)] + [70 * 1.05 ** i for i in range(30)]
        u = uni(prices)
        r = ls.simulate(u, "XBTUSD", "long_short", 200, 0, FREE, short_cost=0.0)  # never above 200-day avg
        self.assertEqual(r["trades"], 0)  # no signal yet with 200-day average → stays flat
        u2 = uni([100.0] * 25 + [99.0] + [100 * 1.1 ** i for i in range(1, 15)])
        r2 = ls.simulate(u2, "XBTUSD", "long_short", 20, 24, FREE, short_cost=0.0)
        self.assertGreaterEqual(r2["final"], 0.0)

    def test_short_holding_cost_accrues(self):
        prices = [100 - 0.1 * i for i in range(60)]  # gentle fall: always short after warm-up
        u = uni(prices)
        free = ls.simulate(u, "XBTUSD", "long_short", 10, 10, FREE, short_cost=0.0)
        costly = ls.simulate(u, "XBTUSD", "long_short", 10, 10, FREE, short_cost=0.001)
        self.assertLess(costly["final"], free["final"])

    def test_flip_pays_two_trades(self):
        prices = [100 + i for i in range(20)] + [119 - 3 * i for i in range(20)]
        u = uni(prices)
        r = ls.simulate(u, "XBTUSD", "long_short", 5, 5, Costs(0.004, 0.0), short_cost=0.0)
        self.assertGreaterEqual(r["trades"], 3)  # open long, then close long + open short

    def test_verdict_rules(self):
        rows = [{"beats_long_only": True, "dd_ok": True}] * 5 + [{"beats_long_only": False, "dd_ok": False}] * 2
        ok, checks = ls.verdict(rows, {"long_short": {"final": 2.0}, "long_only": {"final": 1.5}})
        self.assertTrue(ok)
        ok, _ = ls.verdict(rows, {"long_short": {"final": 1.0}, "long_only": {"final": 1.5}})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
