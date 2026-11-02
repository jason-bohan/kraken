"""
Tests for the backtest engine's mechanics on small hand-built candle series.
These check the arithmetic, not whether any strategy makes money.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import contextlib
import io
import math
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtester as bt  # noqa: E402
import market_data as md  # noqa: E402
import strategies  # noqa: E402


def candle(t, o, h, lo, c, v=1.0):
    return [t, o, h, lo, c, c, v, 1]


FIXED = lambda i, entry, sig: (entry * 0.95, entry * 1.10)  # noqa: E731  stop -5%, target +10%


class SimulateTests(unittest.TestCase):
    def test_enters_next_open_and_hits_target(self):
        cs = [candle(0, 100, 100, 100, 100), candle(60, 101, 112, 100, 110), candle(120, 110, 110, 110, 110)]
        trades, bars = bt.simulate(cs, [True, None, None], FIXED, bt.COST_PRESETS["none"])
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].entry, 101)               # next candle's open, not the signal close
        self.assertAlmostEqual(trades[0].exit, 101 * 1.10)
        self.assertEqual(trades[0].reason, "target")
        self.assertEqual(bars, 1)

    def test_stop_wins_when_candle_touches_both(self):
        cs = [candle(0, 100, 100, 100, 100), candle(60, 100, 111, 94, 100)]
        trades, _ = bt.simulate(cs, [True, None], FIXED, bt.COST_PRESETS["none"])
        self.assertEqual(trades[0].reason, "stop")
        self.assertAlmostEqual(trades[0].net_return, -0.05)

    def test_gap_through_stop_fills_at_open(self):
        cs = [candle(0, 100, 100, 100, 100), candle(60, 100, 100, 100, 100), candle(120, 90, 91, 89, 90)]
        trades, _ = bt.simulate(cs, [True, None, None], FIXED, bt.COST_PRESETS["none"])
        self.assertEqual(trades[0].exit, 90)
        self.assertAlmostEqual(trades[0].net_return, -0.10)

    def test_costs_are_charged_on_both_sides(self):
        cs = [candle(0, 100, 100, 100, 100), candle(60, 100, 111, 100, 110)]
        costs = bt.Costs(fee=0.004, slippage=0.0005)
        trades, _ = bt.simulate(cs, [True, None], FIXED, costs)
        entry_eff = 100 * 1.0005
        target = 100 * 1.10  # exits are computed from the raw open
        exit_eff = target * (1 - 0.0005)
        expected = exit_eff * 0.996 / (entry_eff * 1.004) - 1
        self.assertAlmostEqual(trades[0].net_return, expected)
        self.assertLess(trades[0].net_return, 0.10 - 0.009)

    def test_open_position_is_marked_to_last_close(self):
        cs = [candle(0, 100, 100, 100, 100), candle(60, 100, 103, 99, 102)]
        trades, _ = bt.simulate(cs, [True, None], FIXED, bt.COST_PRESETS["none"])
        self.assertEqual(trades[0].reason, "open_at_end")
        self.assertAlmostEqual(trades[0].net_return, 0.02)


class MetricsTests(unittest.TestCase):
    def test_drawdown_and_profit_factor(self):
        cs = [candle(i * 60, 100, 100, 100, 100) for i in range(10)]
        trades = [bt.Trade(0, 0, 1, 1, "x", r) for r in (0.10, -0.20, 0.05)]
        m = bt.metrics(trades, cs, 3)
        self.assertAlmostEqual(m["total_return"], 1.10 * 0.80 * 1.05 - 1)
        self.assertAlmostEqual(m["max_drawdown"], 0.20)
        self.assertAlmostEqual(m["profit_factor"], 0.15 / 0.20)
        self.assertAlmostEqual(m["win_rate"], 2 / 3)

    def test_breakeven_win_rate(self):
        # 10% target, 8% stop, 0.9% round trip → need (8 + 0.9) / 18 ≈ 49.4% winners
        self.assertAlmostEqual(strategies.breakeven_win_rate(0.10, 0.08, 0.009), 0.089 / 0.18)


class ResampleTests(unittest.TestCase):
    def test_resample_ohlcv_and_drops_unfinished_bucket(self):
        cs = [candle(i * 300, 10 + i, 11 + i, 9 + i, 10.5 + i, v=2) for i in range(14)]  # 70 minutes of 5m
        hourly = md.resample(cs, 60)
        self.assertEqual(len(hourly), 1)  # 1:00-1:10 is unfinished
        h = hourly[0]
        self.assertEqual((h[0], h[1], h[2], h[3], h[4], h[6]), (0, 10, 22, 9, 21.5, 24))

    def test_load_kraken_ohlcvt_csv(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("1700000000,1.0,2.0,0.5,1.5,100,7\n1700000060,1.5,1.6,1.4,1.55,50,3\n")
        try:
            rows = md.load_csv(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(rows[0], [1700000000, 1.0, 2.0, 0.5, 1.5, 1.5, 100.0, 7])
        self.assertEqual(len(rows), 2)

    def test_coinbase_product_names(self):
        self.assertEqual(md.coinbase_product("XBTUSD"), "BTC-USD")
        self.assertEqual(md.coinbase_product("XDGUSD"), "DOGE-USD")
        self.assertEqual(md.coinbase_product("SOLUSD"), "SOL-USD")


def random_walk(n: int, seed: int = 1, start: float = 100.0, step_secs: int = 60) -> list:
    rng = random.Random(seed)
    out, p = [], start
    for i in range(n):
        o = p
        p = max(0.01, p * math.exp(rng.gauss(0, 0.002)))
        hi, lo = max(o, p) * (1 + abs(rng.gauss(0, 0.001))), min(o, p) * (1 - abs(rng.gauss(0, 0.001)))
        out.append([i * step_secs, o, hi, lo, p, p, rng.uniform(1, 10), 1])
    return out


class PipelineTests(unittest.TestCase):
    """End-to-end plumbing on a random walk (where, by construction, there is no edge)."""

    def test_hft_pipeline_runs_and_finds_no_edge_in_noise(self):
        cs = random_walk(6000)
        ad = bt.HftAdapter(cs, strategies.HFT_DEFAULTS)
        params = {k: strategies.HFT_DEFAULTS[k] for k in ad.grid}
        res = bt.run_once(ad, params, bt.COST_PRESETS["taker"])
        self.assertGreater(res["metrics"]["trades"], 0)
        rand = bt.random_baseline(ad, params, res["signals"], bt.COST_PRESETS["taker"], runs=20)
        self.assertEqual(len(rand), 20)
        wf = bt.walk_forward(ad, bt.COST_PRESETS["taker"], folds=3)
        self.assertEqual(len(wf["folds"]), 2)
        self.assertNotEqual(bt.verdict(res["metrics"], 0.5, wf, res["metrics"]),
                            "Passes these checks on this data — worth a dry run, not yet proof.")

    def test_swing_pipeline_runs(self):
        cs = random_walk(5000, seed=3, step_secs=300)
        cfg = {"rsi_oversold": 35, "rsi_overbought": 70, "profit_target": 0.06, "stop_loss": 0.04,
               "volatility_threshold": 0.05, "min_confidence": 0.5}
        with contextlib.redirect_stdout(io.StringIO()):
            ad = bt.SwingAdapter(cs, cfg)
        self.assertLess(ad.warmup, len(cs))
        res = bt.run_once(ad, {"min_confidence": 0.5, "profit_target": 0.06, "atr_mult": 1.5},
                          bt.COST_PRESETS["taker"])
        self.assertIn("total_return", res["metrics"])


if __name__ == "__main__":
    unittest.main()
