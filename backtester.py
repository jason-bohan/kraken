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


def simulate(candles: list, signals: list, exits, costs: Costs, start: int = 0, end: int | None = None) -> tuple[list, int]:
    """
    Long-only, one position at a time.
    signals[i] is truthy when the strategy would buy at the close of candle i;
    the order fills at the open of candle i+1. exits(i, entry_price, signal) →
    (stop, target). Returns (trades, bars_in_market).
    """
    end = len(candles) if end is None else end
    trades, bars_in = [], 0
    pos = None  # (entry_time, entry_eff, stop, target)
    for i in range(max(start, 1), end):
        t, o, h, lo = candles[i][0], candles[i][1], candles[i][2], candles[i][3]
        if pos is None and signals[i - 1]:
            entry_eff = o * (1 + costs.slippage)
            stop, target = exits(i - 1, o, signals[i - 1])
            pos = (t, entry_eff, stop, target)
        if pos is None:
            continue
        bars_in += 1
        _, entry_eff, stop, target = pos
        exit_price = reason = None
        if o <= stop:
            exit_price, reason = o, "stop"            # gapped through the stop
        elif o >= target:
            exit_price, reason = o, "target"
        elif lo <= stop:
            exit_price, reason = stop, "stop"         # stop first if both were touched
        elif h >= target:
            exit_price, reason = target, "target"
        if exit_price is not None:
            exit_eff = exit_price * (1 - costs.slippage)
            net = (exit_eff * (1 - costs.fee)) / (entry_eff * (1 + costs.fee)) - 1
            trades.append(Trade(pos[0], t, entry_eff, exit_eff, reason, net))
            pos = None
    if pos is not None:  # mark open position to the last close
        last = candles[end - 1][4] * (1 - costs.slippage)
        net = (last * (1 - costs.fee)) / (pos[1] * (1 + costs.fee)) - 1
        trades.append(Trade(pos[0], candles[end - 1][0], pos[1], last, "open_at_end", net))
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
    }


def random_baseline(adapter, params: dict, signals: list, costs: Costs, runs: int, seed: int = 7) -> list:
    """
    Total returns of `runs` simulations that buy at random times (once the
    indicators have warmed up), as often as the strategy signals, with the
    strategy's own exit rules.
    """
    candles = adapter.candles
    start = adapter.warmup
    n_sig = sum(1 for s in signals[start:] if s)
    p = n_sig / max(1, len(candles) - start)
    exits = adapter.exits(params)
    rng = random.Random(seed)
    totals = []
    for _ in range(runs):
        rnd = [adapter.random_signal(i) if i >= start and rng.random() < p else None
               for i in range(len(candles))]
        trades, bars = simulate(candles, rnd, exits, costs, start)
        totals.append(metrics(trades, candles, bars, start)["total_return"])
    return totals


def percentile_of(value: float, dist: list) -> float:
    return sum(1 for d in dist if d < value) / len(dist) if dist else 0.0


# ─────────────────────────────────────────────
# STRATEGY ADAPTERS
# ─────────────────────────────────────────────
# Each adapter precomputes what doesn't depend on the tunable settings, then
# builds signal arrays and exit rules for a given set of settings quickly.

class HftAdapter:
    """dynamic_hft_bot: 1-minute candles, fixed % take-profit / stop."""
    name = "hft"
    interval = 1
    grid = {
        "rsi_oversold": [25, 30, 35, 40],
        "profit_pct": [0.01, 0.02, 0.04, 0.10],
        "stop_pct": [0.01, 0.02, 0.04, 0.08],
    }

    def __init__(self, candles: list, base_params: dict):
        self.candles = candles
        self.base = dict(base_params)
        self.snaps = [None] * len(candles)
        need = max(self.base["rsi_period"] + 5, self.base["high_lookback"], self.base["ma_period"])
        self.warmup = need
        for i in range(need, len(candles)):
            self.snaps[i] = strategies.hft_snapshot(candles[i - need + 1:i + 1], self.base)

    def signals(self, params: dict) -> list:
        p = {**self.base, **params}
        out = [None] * len(self.candles)
        for i, snap in enumerate(self.snaps):
            if snap and strategies.hft_entry(self.candles[i][4], snap, "neutral", p)["enter"]:
                out[i] = True
        return out

    def exits(self, params: dict):
        p = {**self.base, **params}
        return lambda i, entry, sig: (entry * (1 - p["stop_pct"]), entry * (1 + p["profit_pct"]))

    def random_signal(self, i: int):
        return True

    def breakeven(self, params: dict, costs: Costs) -> float | None:
        p = {**self.base, **params}
        return strategies.breakeven_win_rate(p["profit_pct"], p["stop_pct"], costs.round_trip)


class SwingAdapter:
    """stock_swing_bot: 5m candles resampled to 15m/1h/4h, evaluated every 15 minutes."""
    name = "swing"
    interval = 5
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

    def random_signal(self, i: int):
        return {"atr": self.atr_at[i]}

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

    def breakeven(self, params: dict, costs: Costs) -> float | None:
        return None  # stop distance varies with ATR


# ─────────────────────────────────────────────
# EXPERIMENTS
# ─────────────────────────────────────────────

def run_once(adapter, params: dict, costs: Costs, start: int = 0, end: int | None = None) -> dict:
    sig = adapter.signals(params)
    trades, bars = simulate(adapter.candles, sig, adapter.exits(params), costs, start, end)
    return {"metrics": metrics(trades, adapter.candles, bars, start, end), "trades": trades, "signals": sig}


def walk_forward(adapter, costs: Costs, folds: int = 4, min_trades: int = 5) -> dict:
    """Expanding-window walk-forward over the parameter grid."""
    n = len(adapter.candles)
    bounds = [int(n * k / folds) for k in range(folds + 1)]
    keys = list(adapter.grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*adapter.grid.values())]
    sig_cache = {}
    oos_trades, oos_bars, chosen = [], 0, []
    for f in range(1, folds):
        best, best_ret = None, None
        for combo in combos:
            key = tuple(combo.items())
            if key not in sig_cache:
                sig_cache[key] = adapter.signals(combo)
            tr, bars = simulate(adapter.candles, sig_cache[key], adapter.exits(combo), costs, 0, bounds[f])
            m = metrics(tr, adapter.candles, bars, 0, bounds[f])
            if m["trades"] >= min_trades and (best_ret is None or m["total_return"] > best_ret):
                best, best_ret = combo, m["total_return"]
        if best is None:
            chosen.append({"fold": f, "params": None, "in_sample_return": None})
            continue
        tr, bars = simulate(adapter.candles, sig_cache[tuple(best.items())], adapter.exits(best),
                            costs, bounds[f], bounds[f + 1])
        oos_trades += tr
        oos_bars += bars
        chosen.append({"fold": f, "params": best, "in_sample_return": best_ret,
                       "out_of_sample_return": metrics(tr, adapter.candles, bars, bounds[f], bounds[f + 1])["total_return"]})
    oos = metrics(oos_trades, adapter.candles, oos_bars, bounds[1], n)
    return {"combos_tested": len(combos), "folds": chosen, "out_of_sample": oos}


def verdict(m: dict, rand_pct: float | None, wf: dict | None, stressed: dict) -> str:
    if m["trades"] < 10:
        return "Too few trades to judge — test a longer period or more pairs."
    if m["expectancy"] <= 0:
        return "Loses money after costs."
    if rand_pct is not None and rand_pct < 0.90:
        return (f"Profitable here, but {100 - rand_pct*100:.0f}% of random-entry runs with the same exits did "
                f"as well — no evidence the entry signal adds an edge.")
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
    print(f"  Buy & hold same period: {pct(m['buy_hold'])}")
    be = adapter.breakeven(params, costs)
    if be is not None:
        print(f"  Break-even win rate after costs: {be*100:.1f}% (actual {m['win_rate']*100:.1f}%)")
    if rand:
        print(f"  Random entries (same exits, {len(rand)} runs): median {pct(statistics.median(rand))} — "
              f"strategy beats {rand_pct*100:.0f}% of them")
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
    ap.add_argument("strategy", choices=["hft", "swing"])
    ap.add_argument("--pairs", default=None, help="comma-separated Kraken pairs (default depends on strategy)")
    ap.add_argument("--days", type=float, default=None, help="history length (default hft 30, swing 180)")
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
    else:
        import stock_swing_bot
        pairs = (args.pairs or "XBTUSD,ETHUSD,SOLUSD,ADAUSD").split(",")
        days = args.days or 180

    interval = HftAdapter.interval if args.strategy == "hft" else SwingAdapter.interval
    report = {"strategy": args.strategy, "created": int(time.time()), "days": days,
              "costs": asdict(costs), "pairs": {}}

    for pair in pairs:
        print(f"\n  Loading {pair} {interval}m candles ({days:g} days)...")
        try:
            candles = (market_data.load_csv(csv_files[pair]) if pair in csv_files
                       else market_data.get_candles(pair, interval, days, args.source))
        except Exception as e:
            print(f"  ⚠️ Could not load {pair}: {e}")
            continue
        if args.strategy == "hft" and pair in csv_files:
            candles = candles[-int(days * 1440):]
        if len(candles) < 500:
            print(f"  ⚠️ Only {len(candles)} candles for {pair} — skipping")
            continue

        if args.strategy == "hft":
            adapter = HftAdapter(candles, base)
            params = {k: base[k] for k in adapter.grid}
        else:
            config = {**stock_swing_bot.get_stock_config(stock_symbol(pair)), "min_confidence": 0.6, **overrides}
            adapter = SwingAdapter(candles, config)
            params = {k: adapter.base[k] for k in adapter.grid}

        res = run_once(adapter, params, costs)
        stressed = run_once(adapter, params, costs.scaled(2))["metrics"]
        rand = (random_baseline(adapter, params, res["signals"], costs, args.random_runs)
                if args.random_runs and res["metrics"]["trades"] else None)
        rand_pct = percentile_of(res["metrics"]["total_return"], rand) if rand else None
        wf = walk_forward(adapter, costs, args.folds) if args.folds >= 2 else None
        text = verdict(res["metrics"], rand_pct, wf, stressed)
        print_report(pair, adapter, params, costs, res, rand, rand_pct, wf, stressed, text)

        report["pairs"][pair] = {
            "candles": len(candles), "interval": interval, "params": params,
            "metrics": res["metrics"], "costs_doubled": stressed,
            "random_median": statistics.median(rand) if rand else None, "random_percentile": rand_pct,
            "walk_forward": wf, "verdict": text,
            "equity": _equity_points(res["trades"]),
        }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{args.strategy}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, default=_json_default))
    print(f"\n  Report saved: {out}")


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
