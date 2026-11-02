"""
Tests for the cross-coin momentum backtest mechanics on hand-built markets.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import momentum_backtest as mb  # noqa: E402
import strategies  # noqa: E402
from backtester import Costs  # noqa: E402

DAY = 86400


def series(prices: list) -> list:
    """Daily candles where open == close == price."""
    return [[i * DAY, p, p, p, p, p, 1.0, 1] for i, p in enumerate(prices)]


def universe(n=200, btc_step=0.002, **coins) -> mb.Universe:
    data = {"XBTUSD": series([100 * (1 + btc_step) ** i for i in range(n)])}
    for name, step in coins.items():
        data[name] = series([10 * (1 + step) ** i for i in range(n)])
    return mb.Universe(data)


NO_COST = Costs(0.0, 0.0)
P = {"lookback": 14, "top_k": 1, "filter_sma": 20, "rebalance_days": 7}


class PickTests(unittest.TestCase):
    def test_picks_only_positive_scores_in_rank_order(self):
        scores = {"A": 0.3, "B": -0.1, "C": 0.1, "D": None}
        self.assertEqual(strategies.momentum_picks(scores, 2, True), ["A", "C"])
        self.assertEqual(strategies.momentum_picks(scores, 5, True), ["A", "C"])
        self.assertEqual(strategies.momentum_picks(scores, 2, False), [])

    def test_market_filter(self):
        self.assertTrue(strategies.market_filter_ok([1, 2, 3, 4], 0))
        self.assertTrue(strategies.market_filter_ok([1, 2, 3, 4], 3))
        self.assertFalse(strategies.market_filter_ok([4, 3, 2, 1], 3))


class SimulationTests(unittest.TestCase):
    def test_holds_the_strongest_coin_and_compounds_it(self):
        u = universe(UPUSD=0.01, FLATUSD=0.0, DOWNUSD=-0.01)
        sim = mb.simulate(u, P, NO_COST)
        m = mb.stats(sim)
        # Always in UPUSD (+1%/day) after warm-up: equity tracks it exactly
        days_held = len(sim["equity"])
        self.assertAlmostEqual(m["total_return"], 1.01 ** days_held - 1, places=6)
        self.assertEqual(m["trades"], 1)  # bought once, never needed to trade again
        self.assertEqual(sim["held_counts"][0], (1, 0))
        self.assertTrue(all(c == (1, 1) for c in sim["held_counts"][1:]))

    def test_market_filter_keeps_everything_in_cash(self):
        u = universe(btc_step=-0.002, UPUSD=0.01)
        m = mb.stats(mb.simulate(u, P, NO_COST))
        self.assertEqual(m["trades"], 0)
        self.assertEqual(m["total_return"], 0.0)
        self.assertEqual(m["exposure"], 0.0)

    def test_costs_charged_on_each_buy_and_sell(self):
        # Leader switches every week: A and B alternate strength, forcing a sell and a buy each rebalance
        n = 120
        a, b = [10.0], [10.0]
        for i in range(1, n):
            up_a = (i // 7) % 2 == 0
            a.append(a[-1] * (1.03 if up_a else 0.99))
            b.append(b[-1] * (0.99 if up_a else 1.03))
        u = mb.Universe({"XBTUSD": series([100 + i for i in range(n)]), "AUSD": series(a), "BUSD": series(b)})
        p = {**P, "lookback": 3, "filter_sma": 0}
        free = mb.stats(mb.simulate(u, p, NO_COST))
        costly = mb.stats(mb.simulate(u, p, Costs(0.004, 0.0005)))
        self.assertGreater(costly["trades"], 5)
        self.assertLess(costly["total_return"], free["total_return"])

    def test_random_selection_matches_holding_counts(self):
        u = universe(n=150, UPUSD=0.01, FLATUSD=0.0, DOWNUSD=-0.005)
        sim = mb.simulate(u, {**P, "top_k": 2}, NO_COST)
        rand = mb.random_selection(u, {**P, "top_k": 2}, NO_COST, sim["held_counts"], runs=20)
        self.assertEqual(len(rand), 20)
        self.assertGreaterEqual(mb.stats(sim)["total_return"], max(rand) - 1e-9)

    def test_random_runs_trade_about_as_often_as_the_strategy(self):
        """Regression: random picks used to reshuffle every week and lose mostly to fees."""
        u = universe(n=300, AUSD=0.004, BUSD=0.003, CUSD=0.002, DUSD=-0.001, EUSD=0.0)
        p = {**P, "top_k": 2}
        sim = mb.simulate(u, p, NO_COST)
        trades = []
        rng_counts = sim["held_counts"]
        import random as _r
        rng = _r.Random(1)

        def picker(n, picks, eligible, held):
            k, kept = rng_counts[n]
            keep = [c for c in held if c in eligible][:kept]
            fresh = [c for c in eligible if c not in keep]
            return keep + rng.sample(fresh, k - len(keep))
        trades.append(mb.simulate(u, p, NO_COST, picker=picker)["trades"])
        self.assertLessEqual(abs(trades[0] - sim["trades"]), 2)

    def test_walk_forward_and_verdict_run(self):
        u = universe(n=500, UPUSD=0.004, FLATUSD=0.0, DOWNUSD=-0.003)
        wf = mb.walk_forward(u, NO_COST, folds=3)
        self.assertEqual(len(wf["folds"]), 2)
        text = mb.verdict({"trades": 40}, 0.5, wf, {"total_return": 0.1})
        self.assertIn("random picks did as well", text)

    def test_buy_hold_benchmark(self):
        u = universe(n=50, UPUSD=0.01)
        bh = mb.buy_hold(u, ["UPUSD"], 0, 50)
        self.assertAlmostEqual(bh["total_return"], 1.01 ** 49 - 1, places=6)


if __name__ == "__main__":
    unittest.main()
