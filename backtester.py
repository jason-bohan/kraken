#!/usr/bin/env python3
"""
backtester.py — test the bots' strategies on history, after costs, and check
whether a result is an edge or luck.

It replays the exact signal code the bots run (strategies.py) and adds:
  - Costs: Kraken fees on both sides plus slippage (taker = market orders).
  - Realistic fills: enter at the NEXT candle's open; a candle that touches
    both stop and target counts as a stop.
  - Baselines: buy-and-hold over the same period, and random entries with the
    same exit rules (if random entries do as well, the signal adds nothing).
  - Walk-forward: settings are picked on earlier data and scored on the next,
    unseen period, so results aren't flattered by hindsight.
  - Cost stress: the same run with costs doubled.

Examples:
    python3 backtester.py hft --pairs SOLUSD,XDGUSD --days 30
    python3 backtester.py hft --pairs SOLUSD --days 30 --costs maker
    python3 backtester.py swing --pairs XBTUSD,ETHUSD,SOLUSD,ADAUSD --days 180
    python3 backtester.py hft --csv SOLUSD=data/SOLUSD_1.csv --days 60

Reports are printed and saved to data/backtests/*.json (the terminal's BT screen reads them).
"""

import argparse
import itertools
import json
import random
import statistics
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import market_data
import strategies

REPORT_DIR = Path(__file__).resolve().parent / "data" / "backtests"


# ─────────────────────────────────────────────
# COSTS
# ─────────────────────────────────────────────

@dataclass
class Costs:
    fee: float = 0.0040        # per side. Kraken Pro, <$10k 30-day volume: taker 0.40%, maker 0.25%
    slippage: float = 0.0005   # per side, price moved against us

    def scaled(self, k: float) -> "Costs":
        return Costs(self.fee * k, self.slippage * k)

    @property
    def round_trip(self) -> float:
        return 2 * (self.fee + self.slippage)


COST_PRESETS = {
    "taker": Costs(0.0040, 0.0005),  # market orders (what the bots place today)
    "maker": Costs(0.0025, 0.0000),  # resting limit orders
    "none": Costs(0.0, 0.0),
}


# ─────────────────────────────────────────────
# SIMULATION
# ─────────────────────────────────────────────

@dataclass
class Trade:
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    reason: str
    net_return: float
    bars: int = 0          # candles held


def simulate(candles: list, signals: list, exits, costs: Costs, start: int = 0, end: int | None = None,
             exit_signals: list | None = None) -> tuple[list, int]:
    """
    Long-only, one position at a time.

    signals[i] is truthy when the strategy would buy at the close of candle i;
    the order fills at the open of candle i+1. exits(i, entry_price, signal)
    returns (stop, target) or (stop, target, max_bars); stop/target may be None.
    exit_signals[i] truthy = sell at the open of candle i+1. A position still
    open after max_bars candles is sold at that candle's close.
    Returns (trades, bars_in_market).
    """
    end = len(candles) if end is None else end
    trades, bars_in = [], 0
    pos = None  # [entry_time, entry_eff, stop, target, max_bars, bars_held]

    def close(t, price, reason):
        exit_eff = price * (1 - costs.slippage)
        net = (exit_eff * (1 - costs.fee)) / (pos[1] * (1 + costs.fee)) - 1
        trades.append(Trade(pos[0], t, pos[1], exit_eff, reason, net, pos[5]))

    for i in range(max(start, 1), end):
        t, o, h, lo, c = candles[i][0], candles[i][1], candles[i][2], candles[i][3], candles[i][4]
        if pos is not None and exit_signals and exit_signals[i - 1]:
            close(t, o, "signal")
            pos = None
        if pos is None and signals[i - 1]:
            ex = exits(i - 1, o, signals[i - 1])
            stop, target = ex[0], ex[1]
            max_bars = ex[2] if len(ex) > 2 else None
            pos = [t, o * (1 + costs.slippage), stop, target, max_bars, 0]
        if pos is None:
            continue
        bars_in += 1
        pos[5] += 1
        stop, target = pos[2], pos[3]
        exit_price = reason = None
        if stop is not None and o <= stop:
            exit_price, reason = o, "stop"            # gapped through the stop
        elif target is not None and o >= target:
            exit_price, reason = o, "target"
        elif stop is not None and lo <= stop:
            exit_price, reason = stop, "stop"         # stop first if both were touched
        elif target is not None and h >= target:
            exit_price, reason = target, "target"
        elif pos[4] and pos[5] >= pos[4]:
            exit_price, reason = c, "time"
        if exit_price is not None:
            close(t, exit_price, reason)
            pos = None
    if pos is not None:  # mark open position to the last close
        close(candles[end - 1][0], candles[end - 1][4], "open_at_end")
    return trades, bars_in


def metrics(trades: list, candles: list, bars_in: int, start: int = 0, end: int | None = None) -> dict:
    end = len(candles) if end is None else end
    rets = [tr.net_return for tr in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in rets:
        equity *= 1 + r
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
    first_open, last_close = candles[start][1], candles[end - 1][4]
    peak_px, bh_dd = first_open, 0.0
    for c in candles[start:end]:
        peak_px = max(peak_px, c[2])
        bh_dd = max(bh_dd, 1 - c[3] / peak_px)
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "trades": len(rets),
        "win_rate": len(wins) / len(rets) if rets else 0.0,
        "avg_win": statistics.mean(wins) if wins else 0.0,
        "avg_loss": statistics.mean(losses) if losses else 0.0,
        "expectancy": statistics.mean(rets) if rets else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win else 0.0),
        "total_return": equity - 1,
        "max_drawdown": max_dd,
        "exposure": bars_in / max(1, end - start),
        "buy_hold": last_close / first_open - 1 if first_open else 0.0,
        "buy_hold_max_drawdown": bh_dd,
    }


def random_baseline(adapter, trades: list, costs: Costs, runs: int, seed: int = 7) -> list:
    """
    Random-timing test: in each run, place the same number of trades with the
    same holding times as the strategy, at random non-overlapping moments
    after the indicator warm-up, and pay the same costs. Returns each run's
    total return. If the strategy doesn't beat most of these, its timing adds
    nothing beyond being in the market that often.
    """
    candles = adapter.candles
    start, n = adapter.warmup, len(candles)
    durations = [max(1, t.bars) for t in trades]
    free = (n - start) - sum(durations)
    if not durations or free < 0:
        return []
    rng = random.Random(seed)
    totals = []
    for _ in range(runs):
        d = durations[:]
        rng.shuffle(d)
        cuts = sorted(rng.randint(0, free) for _ in d)  # where each trade starts, as gaps
        pos, prev, equity = start, 0, 1.0
        for cut, dur in zip(cuts, d):
            pos += cut - prev
            prev = cut
            entry = candles[pos][1] * (1 + costs.slippage)
            exit_ = candles[pos + dur - 1][4] * (1 - costs.slippage)
            equity *= (exit_ * (1 - costs.fee)) / (entry * (1 + costs.fee))
            pos += dur
        totals.append(equity - 1)
    return totals


def percentile_of(value: float, dist: list) -> float:
    return sum(1 for d in dist if d < value) / len(dist) if dist else 0.0


# ─────────────────────────────────────────────
# STRATEGY ADAPTERS
# ─────────────────────────────────────────────
# Each adapter precomputes what doesn't depend on the tunable settings, then
# builds signal arrays and exit rules for a given set of settings quickly.

class HftAdapter:
    """dynamic_hft_bot: 1-minute candles, fixed % take-profit / stop, optional trend filter and time limit."""
    name = "hft"
    interval = 1
    min_trades = 10
    grid = {
        "rsi_oversold": [30, 40],
        "trend_period": [0, 240, 720],          # minutes of 1m closes in the trend SMA (0 = off)
        "profit_pct": [0.01, 0.02, 0.04, 0.10],
        "stop_pct": [0.01, 0.02, 0.04, 0.08],
        "max_hold_minutes": [0, 360],           # 0 = no time limit
    }
    entry_keys = ("rsi_oversold", "trend_period")

    def __init__(self, candles: list, base_params: dict):
        self.candles = candles
        self.base = dict(base_params)
        self.snaps = [None] * len(candles)
        need = max(self.base["rsi_period"] + 5, self.base["high_lookback"], self.base["ma_period"])
        self.warmup = need
        snap_params = {**self.base, "trend_period": 0}  # trend SMA is precomputed below (faster)
        for i in range(need, len(candles)):
            self.snaps[i] = strategies.hft_snapshot(candles[i - need + 1:i + 1], snap_params)
        closes = [c[4] for c in candles]
        self.trend_ma = {}
        periods = set(self.grid["trend_period"]) | {int(self.base.get("trend_period", 0))}
        for n in periods:
            if not n:
                continue
            ma, run = [None] * len(closes), 0.0
            for i, c in enumerate(closes):
                run += c
                if i >= n:
                    run -= closes[i - n]
                if i >= n - 1:
                    ma[i] = run / n
            self.trend_ma[n] = ma

    def signals(self, params: dict) -> list:
        p = {**self.base, **params}
        tp = int(p.get("trend_period", 0))
        ma = self.trend_ma.get(tp)
        out = [None] * len(self.candles)
        for i, snap in enumerate(self.snaps):
            if not snap:
                continue
            if tp:
                snap = {**snap, "trend_ma": ma[i]}
            if strategies.hft_entry(self.candles[i][4], snap, "neutral", p)["enter"]:
                out[i] = True
        return out

    def exits(self, params: dict):
        p = {**self.base, **params}
        max_bars = int(p.get("max_hold_minutes", 0)) // self.interval or None
        return lambda i, entry, sig: (entry * (1 - p["stop_pct"]), entry * (1 + p["profit_pct"]), max_bars)

    def exit_signals(self, params: dict):
        return None


    def breakeven(self, params: dict, costs: Costs) -> float | None:
        p = {**self.base, **params}
        if p.get("max_hold_minutes"):
            return None  # time exits make outcomes non-binary
        return strategies.breakeven_win_rate(p["profit_pct"], p["stop_pct"], costs.round_trip)


class SwingAdapter:
    """stock_swing_bot: 5m candles resampled to 15m/1h/4h, evaluated every 15 minutes."""
    name = "swing"
    interval = 5
    min_trades = 5
    window = 100  # candles per timeframe given to the indicators
    grid = {
        "min_confidence": [0.5, 0.6, 0.7],
        "profit_target": [0.04, 0.06, 0.10],
        "atr_mult": [1.0, 1.5, 2.0],
    }

    def __init__(self, candles: list, config: dict, progress: bool = True):
        self.candles = candles
        self.base = {**config, "atr_mult": 1.5}
        tfs = {"5m": candles, "15m": market_data.resample(candles, 15),
               "1h": market_data.resample(candles, 60), "4h": market_data.resample(candles, 240)}
        tf_secs = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        ptr = {k: 0 for k in tfs}
        self.raw = [None] * len(candles)      # swing_signal() result at evaluation points
        self.atr_at = [0.0] * len(candles)    # latest known 1h ATR at each candle
        self.warmup = len(candles)
        total = len(candles)
        t0 = time.time()
        for i, c in enumerate(candles):
            close_t = c[0] + 300
            if close_t % 900:
                continue  # evaluate on 15-minute boundaries
            analysis = {}
            for name, tf in tfs.items():
                # last candle that has fully closed by close_t
                while ptr[name] < len(tf) and tf[ptr[name]][0] + tf_secs[name] <= close_t:
                    ptr[name] += 1
                window = tf[max(0, ptr[name] - self.window):ptr[name]]
                a = strategies.analyze_timeframe(window)
                if a:
                    analysis[name] = a
            if len(analysis) == 4:
                self.raw[i] = strategies.swing_signal(analysis, self.base)
                self.warmup = min(self.warmup, i)
            if progress and i and i % 20000 == 0:
                print(f"    analysed {i}/{total} candles ({time.time() - t0:.0f}s)")

        last_atr = 0.0
        for i, s in enumerate(self.raw):
            if s:
                last_atr = s["analysis"]["1h"]["atr"]
            self.atr_at[i] = last_atr


    def signals(self, params: dict) -> list:
        p = {**self.base, **params}
        out = [None] * len(self.candles)
        for i, s in enumerate(self.raw):
            if not s or s["signal"] != "buy" or s["confidence"] < p["min_confidence"]:
                continue
            price = s["price"]
            atr_1h = s["analysis"]["1h"]["atr"]
            stop = price - atr_1h * p["atr_mult"] if atr_1h > 0 else price * (1 - p["stop_loss"])
            target = price * (1 + p["profit_target"])
            if price - stop > 0 and (target - price) / (price - stop) >= 2.0:  # the bot's min risk/reward
                out[i] = {"atr": atr_1h}
        return out

    def exits(self, params: dict):
        p = {**self.base, **params}

        def f(i, entry, sig):
            a = sig.get("atr", 0) if isinstance(sig, dict) else 0
            stop = entry - a * p["atr_mult"] if a > 0 else entry * (1 - p["stop_loss"])
            return stop, entry * (1 + p["profit_target"])
        return f

    def exit_signals(self, params: dict):
        return None

    def breakeven(self, params: dict, costs: Costs) -> float | None:
        return None  # stop distance varies with ATR


class TrendAdapter:
    """
    Trend-following candidate (no live bot yet): long while fast EMA > slow EMA
    and close > slow EMA; sell when that breaks. Optional ATR stop.
    """
    name = "trend"
    interval = 1440
    min_trades = 3
    grid = {"fast": [10, 20], "slow": [50, 100, 200], "atr_stop": [0.0, 3.0]}
    entry_keys = ("fast", "slow")

    def __init__(self, candles: list, base_params: dict):
        self.candles = candles
        self.base = {**strategies.TREND_DEFAULTS, **base_params}
        closes = [c[4] for c in candles]
        highs = [c[2] for c in candles]
        lows = [c[3] for c in candles]
        periods = set(self.grid["fast"]) | set(self.grid["slow"]) | {int(self.base["fast"]), int(self.base["slow"])}
        self.ema = {n: strategies.ema_full(closes, n) for n in periods}
        self.atr = [strategies.atr(highs[max(0, i - 14):i + 1], lows[max(0, i - 14):i + 1],
                                   closes[max(0, i - 14):i + 1]) for i in range(len(candles))]
        self.warmup = max(self.grid["slow"])

    def _state(self, params: dict) -> list:
        p = {**self.base, **params}
        fast, slow = self.ema[int(p["fast"])], self.ema[int(p["slow"])]
        return [strategies.trend_state(c[4], fast[i], slow[i]) for i, c in enumerate(self.candles)]

    def signals(self, params: dict) -> list:
        return [True if on else None for on in self._state(params)]

    def exit_signals(self, params: dict) -> list:
        return [not on for on in self._state(params)]

    def exits(self, params: dict):
        k = float({**self.base, **params}["atr_stop"])
        return lambda i, entry, sig: (entry - k * self.atr[i] if k and self.atr[i] else None, None)


    def breakeven(self, params: dict, costs: Costs) -> float | None:
        return None


# ─────────────────────────────────────────────
# EXPERIMENTS
# ─────────────────────────────────────────────

def run_once(adapter, params: dict, costs: Costs, start: int = 0, end: int | None = None) -> dict:
    sig = adapter.signals(params)
    trades, bars = simulate(adapter.candles, sig, adapter.exits(params), costs, start, end,
                            exit_signals=adapter.exit_signals(params))
    return {"metrics": metrics(trades, adapter.candles, bars, start, end), "trades": trades, "signals": sig}


def walk_forward(adapter, costs: Costs, folds: int = 4) -> dict:
    """
    Expanding-window walk-forward: for each fold, pick the best setting combo
    on everything before it, then score that combo on the fold (unseen data).
    """
    n = len(adapter.candles)
    bounds = [int(n * k / folds) for k in range(folds + 1)]
    keys = list(adapter.grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*adapter.grid.values())]
    entry_keys = getattr(adapter, "entry_keys", tuple(keys))
    sig_cache, exit_cache = {}, {}

    def cached(combo):
        k = tuple(combo[x] for x in entry_keys)
        if k not in sig_cache:
            sig_cache[k] = adapter.signals(combo)
            exit_cache[k] = adapter.exit_signals(combo)
        return sig_cache[k], exit_cache[k]

    oos_trades, oos_bars, chosen = [], 0, []
    for f in range(1, folds):
        best, best_ret = None, None
        for combo in combos:
            sig, ex_sig = cached(combo)
            tr, bars = simulate(adapter.candles, sig, adapter.exits(combo), costs, 0, bounds[f], ex_sig)
            m = metrics(tr, adapter.candles, bars, 0, bounds[f])
            if m["trades"] >= adapter.min_trades and (best_ret is None or m["total_return"] > best_ret):
                best, best_ret = combo, m["total_return"]
        if best is None:
            chosen.append({"fold": f, "params": None, "in_sample_return": None})
            continue
        sig, ex_sig = cached(best)
        tr, bars = simulate(adapter.candles, sig, adapter.exits(best), costs, bounds[f], bounds[f + 1], ex_sig)
        oos_trades += tr
        oos_bars += bars
        chosen.append({"fold": f, "params": best, "in_sample_return": best_ret,
                       "out_of_sample_return": metrics(tr, adapter.candles, bars, bounds[f], bounds[f + 1])["total_return"]})
    oos = metrics(oos_trades, adapter.candles, oos_bars, bounds[1], n)
    return {"combos_tested": len(combos), "folds": chosen, "out_of_sample": oos}


def verdict(m: dict, rand_pct: float | None, wf: dict | None, stressed: dict) -> str:
    if m["trades"] < 10:
        state = "losing so far" if m["expectancy"] <= 0 else "profitable so far"
        return f"Too few trades to judge ({m['trades']}, {state}) — test a longer period or more pairs."
    if m["expectancy"] <= 0:
        return "Loses money after costs."
    if rand_pct is not None and rand_pct < 0.90:
        return (f"Profitable here, but {100 - rand_pct*100:.0f}% of random-timing runs did as well — "
                f"no evidence the timing adds an edge.")
    if wf and wf["out_of_sample"]["trades"] and wf["out_of_sample"]["total_return"] <= 0:
        return "Only profitable with settings picked in hindsight (walk-forward loses)."
    if stressed["expectancy"] <= 0:
        return "Edge is thinner than costs: loses with fees/slippage doubled."
    return "Passes these checks on this data — worth a dry run, not yet proof."


def pct(x: float) -> str:
    return f"{x*100:+.2f}%"


def print_report(pair: str, adapter, params: dict, costs: Costs, res: dict, rand: list | None,
                 rand_pct: float | None, wf: dict | None, stressed: dict, text_verdict: str) -> None:
    m = res["metrics"]
    print(f"\n  ── {pair} · {adapter.name} · {len(adapter.candles)} × {adapter.interval}m candles ──")
    print(f"  Settings: {params}")
    print(f"  Costs: fee {costs.fee*100:.2f}% + slippage {costs.slippage*100:.2f}% per side "
          f"(round trip {costs.round_trip*100:.2f}%)")
    pf = "∞" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    print(f"  Trades {m['trades']:>4} | win rate {m['win_rate']*100:5.1f}% | avg win {pct(m['avg_win'])} | "
          f"avg loss {pct(m['avg_loss'])} | profit factor {pf}")
    print(f"  Net return {pct(m['total_return'])} | per trade {pct(m['expectancy'])} | "
          f"max drawdown {m['max_drawdown']*100:.1f}% | time in market {m['exposure']*100:.0f}%")
    print(f"  Buy & hold same period: {pct(m['buy_hold'])} (max drawdown {m['buy_hold_max_drawdown']*100:.1f}%)")
    be = adapter.breakeven(params, costs)
    if be is not None:
        print(f"  Break-even win rate after costs: {be*100:.1f}% (actual {m['win_rate']*100:.1f}%)")
    if rand:
        print(f"  Random timing (same trade count & holding times, {len(rand)} runs): median "
              f"{pct(statistics.median(rand))} — strategy beats {rand_pct*100:.0f}% of them")
    print(f"  Costs doubled: net {pct(stressed['total_return'])}, per trade {pct(stressed['expectancy'])}")
    if wf:
        o = wf["out_of_sample"]
        print(f"  Walk-forward ({wf['combos_tested']} setting combos, picked on past data each fold): "
              f"unseen-data net {pct(o['total_return'])} over {o['trades']} trades, win rate {o['win_rate']*100:.1f}%")
        for f in wf["folds"]:
            if f["params"]:
                print(f"    fold {f['fold']}: picked {f['params']} → in-sample {pct(f['in_sample_return'])}, "
                      f"next period {pct(f['out_of_sample_return'])}")
    print(f"  ➜ {text_verdict}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("strategy", choices=["hft", "swing", "trend"])
    ap.add_argument("--pairs", default=None, help="comma-separated Kraken pairs (default depends on strategy)")
    ap.add_argument("--days", type=float, default=None, help="history length (default hft 30, swing 180, trend 1095)")
    ap.add_argument("--interval", type=int, choices=[240, 1440], default=1440,
                    help="trend only: candle size in minutes (240 = 4h, 1440 = daily)")
    ap.add_argument("--source", choices=["coinbase", "kraken"], default="coinbase")
    ap.add_argument("--csv", action="append", default=[], metavar="PAIR=PATH",
                    help="use a local candle file for PAIR (Kraken OHLCVT export); repeatable")
    ap.add_argument("--costs", choices=list(COST_PRESETS), default="taker")
    ap.add_argument("--fee", type=float, help="override fee per side (e.g. 0.004)")
    ap.add_argument("--slippage", type=float, help="override slippage per side")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a strategy setting, e.g. --set profit_pct=0.03")
    ap.add_argument("--random-runs", type=int, default=200)
    ap.add_argument("--folds", type=int, default=4, help="walk-forward folds (0 = skip)")
    args = ap.parse_args()

    costs = COST_PRESETS[args.costs]
    if args.fee is not None or args.slippage is not None:
        costs = Costs(args.fee if args.fee is not None else costs.fee,
                      args.slippage if args.slippage is not None else costs.slippage)
    overrides = {k: float(v) for k, v in (s.split("=", 1) for s in args.set)}
    csv_files = dict(s.split("=", 1) for s in args.csv)

    if args.strategy == "hft":
        import dynamic_hft_bot
        pairs = (args.pairs or "SOLUSD,XDGUSD,ETHUSD").split(",")
        days = args.days or 30
        base = {**dynamic_hft_bot.STRATEGY_PARAMS, **overrides}
    elif args.strategy == "swing":
        import stock_swing_bot
        pairs = (args.pairs or "XBTUSD,ETHUSD,SOLUSD,ADAUSD").split(",")
        days = args.days or 180
    else:
        pairs = (args.pairs or "XBTUSD,ETHUSD,SOLUSD,ADAUSD,XDGUSD,LINKUSD").split(",")
        days = args.days or 1095
        base = overrides

    interval = {"hft": HftAdapter.interval, "swing": SwingAdapter.interval, "trend": args.interval}[args.strategy]
    fetch_interval = 60 if interval == 240 else interval  # Coinbase has no 4h candles: build from 1h
    report = {"strategy": args.strategy, "created": int(time.time()), "days": days,
              "costs": asdict(costs), "pairs": {}}
    all_trades = []

    for pair in pairs:
        print(f"\n  Loading {pair} {interval}m candles ({days:g} days)...")
        try:
            candles = (market_data.load_csv(csv_files[pair]) if pair in csv_files
                       else market_data.get_candles(pair, fetch_interval, days, args.source))
        except Exception as e:
            print(f"  ⚠️ Could not load {pair}: {e}")
            continue
        if fetch_interval != interval:
            candles = market_data.resample(candles, interval)
        if pair in csv_files:
            candles = candles[-int(days * 1440 / interval):]
        min_candles = 300 if args.strategy == "trend" else 500
        if len(candles) < min_candles:
            print(f"  ⚠️ Only {len(candles)} candles for {pair} — skipping")
            continue

        if args.strategy == "hft":
            adapter = HftAdapter(candles, base)
            params = {k: base[k] for k in adapter.grid}
        elif args.strategy == "trend":
            adapter = TrendAdapter(candles, base)
            adapter.interval = interval
            params = {k: adapter.base[k] for k in adapter.grid}
        else:
            config = {**stock_swing_bot.get_stock_config(stock_symbol(pair)), "min_confidence": 0.6, **overrides}
            adapter = SwingAdapter(candles, config)
            params = {k: adapter.base[k] for k in adapter.grid}

        res = run_once(adapter, params, costs)
        stressed = run_once(adapter, params, costs.scaled(2))["metrics"]
        rand = (random_baseline(adapter, res["trades"], costs, args.random_runs)
                if args.random_runs and res["metrics"]["trades"] else None)
        rand_pct = percentile_of(res["metrics"]["total_return"], rand) if rand else None
        wf = walk_forward(adapter, costs, args.folds) if args.folds >= 2 else None
        text = verdict(res["metrics"], rand_pct, wf, stressed)
        print_report(pair, adapter, params, costs, res, rand, rand_pct, wf, stressed, text)

        all_trades.extend(res["trades"])
        report["pairs"][pair] = {
            "candles": len(candles), "interval": interval, "params": params,
            "metrics": res["metrics"], "costs_doubled": stressed,
            "random_median": statistics.median(rand) if rand else None, "random_percentile": rand_pct,
            "walk_forward": wf, "verdict": text,
            "equity": _equity_points(res["trades"]),
        }

    if len(report["pairs"]) > 1:
        report["summary"] = print_summary(report, all_trades)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{args.strategy}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, default=_json_default))
    print(f"\n  Report saved: {out}")


def print_summary(report: dict, trades: list) -> dict:
    """All coins together: one coin rarely has enough trades to judge on its own."""
    pairs = report["pairs"].values()
    rets = [t.net_return for t in trades]
    wins = [r for r in rets if r > 0]
    avg = lambda xs: statistics.mean(xs) if xs else 0.0  # noqa: E731
    wf = [p["walk_forward"]["out_of_sample"] for p in pairs if p.get("walk_forward")]
    summary = {
        "pairs": len(report["pairs"]),
        "trades": len(rets),
        "win_rate": len(wins) / len(rets) if rets else 0.0,
        "expectancy": avg(rets),
        "avg_return": avg([p["metrics"]["total_return"] for p in pairs]),
        "avg_buy_hold": avg([p["metrics"]["buy_hold"] for p in pairs]),
        "avg_max_drawdown": avg([p["metrics"]["max_drawdown"] for p in pairs]),
        "avg_buy_hold_drawdown": avg([p["metrics"].get("buy_hold_max_drawdown", 0) for p in pairs]),
        "avg_random_percentile": avg([p["random_percentile"] for p in pairs if p.get("random_percentile") is not None]),
        "avg_walk_forward": avg([w["total_return"] for w in wf]) if wf else None,
        "walk_forward_trades": sum(w["trades"] for w in wf),
        "avg_costs_doubled": avg([p["costs_doubled"]["total_return"] for p in pairs]),
    }
    print(f"\n  ══ ALL {summary['pairs']} COINS · {report['strategy']} ══")
    print(f"  Trades {summary['trades']} | win rate {summary['win_rate']*100:.1f}% | "
          f"avg per trade {pct(summary['expectancy'])} (after costs)")
    print(f"  Avg net return per coin {pct(summary['avg_return'])} vs buy & hold {pct(summary['avg_buy_hold'])}")
    print(f"  Avg max drawdown {summary['avg_max_drawdown']*100:.1f}% vs buy & hold "
          f"{summary['avg_buy_hold_drawdown']*100:.1f}% | beats random timing on avg "
          f"{summary['avg_random_percentile']*100:.0f}% of runs")
    if summary["avg_walk_forward"] is not None:
        print(f"  Walk-forward on unseen data: avg {pct(summary['avg_walk_forward'])} per coin "
              f"over {summary['walk_forward_trades']} trades")
    print(f"  With costs doubled: avg {pct(summary['avg_costs_doubled'])} per coin")
    return summary


def stock_symbol(pair: str) -> str:
    return {"XBTUSD": "BTC", "XDGUSD": "DOGE"}.get(pair.upper(), pair.upper().removesuffix("USD"))


def _equity_points(trades: list) -> list:
    eq, pts = 1.0, []
    for tr in trades:
        eq *= 1 + tr.net_return
        pts.append([tr.exit_time, round(eq, 6)])
    return pts


def _json_default(o):
    return str(o)


if __name__ == "__main__":
    main()
