#!/usr/bin/env python3
"""
momentum_backtest.py — test cross-coin momentum (RESEARCH.md #6) after costs.

Each week: rank coins by their return over the last `lookback` days, hold
the best `top_k` (only if their return is positive), all in cash while BTC is
below its `filter_sma`-day average. Trades fill at the next day's open; fees
and slippage are charged on every buy and sell.

Checks, as in backtester.py:
  - benchmarks: BTC buy-and-hold and an equal-weight basket of all coins
  - random selection: same number of coins held at the same times, random picks
  - walk-forward over a 36-combo settings grid (settings picked on past data)
  - costs doubled

Examples:
    python3 momentum_backtest.py
    python3 momentum_backtest.py --days 1460 --set top_k=5
    python3 momentum_backtest.py --costs maker

The report is saved to data/backtests/momentum_*.json (terminal: BT momentum).
"""

import argparse
import itertools
import json
import math
import random
import statistics
import time
from dataclasses import asdict

import market_data
import strategies
from backtester import COST_PRESETS, Costs, REPORT_DIR, pct

UNIVERSE = ["XBTUSD", "ETHUSD", "SOLUSD", "ADAUSD", "XDGUSD", "LINKUSD", "AVAXUSD", "XRPUSD",
            "DOTUSD", "LTCUSD", "BCHUSD", "UNIUSD", "AAVEUSD", "XLMUSD", "ATOMUSD", "NEARUSD"]
BTC = "XBTUSD"
GRID = {"lookback": [14, 28, 56], "top_k": [2, 3, 5], "filter_sma": [0, 50, 100, 200]}
DAY = 86400


# ─────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────

class Universe:
    """Daily opens/closes for every coin on one shared calendar (None where a coin has no data)."""

    def __init__(self, candles_by_pair: dict):
        days = sorted({c[0] // DAY * DAY for cs in candles_by_pair.values() for c in cs})
        self.days = days
        index = {d: i for i, d in enumerate(days)}
        self.pairs = list(candles_by_pair)
        self.open = {p: [None] * len(days) for p in self.pairs}
        self.close = {p: [None] * len(days) for p in self.pairs}
        for p, cs in candles_by_pair.items():
            for c in cs:
                i = index[c[0] // DAY * DAY]
                self.open[p][i], self.close[p][i] = c[1], c[4]

    def __len__(self):
        return len(self.days)

    def closes_until(self, pair: str, i: int, n: int) -> list:
        """The last n+1 closes up to day i, or [] if any is missing."""
        window = self.close[pair][max(0, i - n):i + 1]
        return window if len(window) == n + 1 and None not in window else []


# ─────────────────────────────────────────────
# SIMULATION
# ─────────────────────────────────────────────

def warmup(params: dict) -> int:
    return max(params["lookback"], params["filter_sma"]) + 1


def decide(u: Universe, i: int, params: dict) -> tuple[list, list]:
    """(picks, eligible coins) from data up to the close of day i."""
    btc = u.closes_until(BTC, i, max(params["filter_sma"], 1))
    market_ok = strategies.market_filter_ok(btc, params["filter_sma"]) if btc else False
    scores, eligible = {}, []
    for p in u.pairs:
        closes = u.closes_until(p, i, params["lookback"])
        if closes and u.open[p][i + 1] is not None:
            eligible.append(p)
            scores[p] = strategies.momentum_score(closes, params["lookback"])
    return strategies.momentum_picks(scores, params["top_k"], market_ok), eligible


def simulate(u: Universe, params: dict, costs: Costs, start: int | None = None, end: int | None = None,
             picker=None) -> dict:
    """
    Run the strategy from day `start` (decisions may use earlier data) to `end`.
    picker(rebalance_no, picks, eligible, currently_held) → coins to hold; default = the strategy's picks.
    Returns the equity curve (at daily opens) and trade statistics.
    """
    p = {**strategies.MOMENTUM_DEFAULTS, **params}
    start = max(start if start is not None else 0, warmup(p))
    end = len(u) if end is None else end
    cash, hold = 1.0, {}          # dollars in cash and per coin
    equity, period_returns, held_counts = [], [], []   # held_counts: (coins held, coins kept from last time)
    trades = invested_days = rebalances = 0
    last_value = None
    for d in range(start, end - 2):  # decide at close d, trade at open d+1, mark to open d+2
        if (d - start) % p["rebalance_days"] == 0:
            picks, eligible = decide(u, d, p)
            if picker:
                picks = picker(rebalances, picks, eligible, list(hold))
            held_counts.append((len(picks), len(set(picks) & set(hold))))
            rebalances += 1
            total = cash + sum(hold.values())
            if last_value is not None and hold:
                period_returns.append(total / last_value - 1)
            target = {c: total / p["top_k"] for c in picks}
            for c in set(hold) | set(target):
                diff = target.get(c, 0.0) - hold.get(c, 0.0)
                if abs(diff) > 1e-12 * total:
                    trades += 1
                    cash -= diff + abs(diff) * (costs.fee + costs.slippage)
                    hold[c] = hold.get(c, 0.0) + diff
            hold = {c: v for c, v in hold.items() if v > 1e-12}
            last_value = cash + sum(hold.values())
        # mark to the next day's open
        for c in hold:
            o0, o1 = u.open[c][d + 1], u.open[c][d + 2]
            if o0 and o1:
                hold[c] *= o1 / o0
        invested_days += bool(hold)
        equity.append([u.days[d + 2], cash + sum(hold.values())])
    return {"equity": equity, "trades": trades, "rebalances": rebalances, "held_counts": held_counts,
            "invested": invested_days / max(1, len(equity)), "period_returns": period_returns}


def stats(sim: dict) -> dict:
    eq = [v for _, v in sim["equity"]]
    if not eq:
        return {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "trades": 0,
                "win_rate": 0.0, "exposure": 0.0}
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    rets = [b / a - 1 for a, b in zip([1.0] + eq, eq)]
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    years = len(eq) / 365
    pr = sim["period_returns"]
    return {
        "total_return": eq[-1] - 1,
        "cagr": eq[-1] ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else -1.0,
        "max_drawdown": dd,
        "sharpe": statistics.mean(rets) / sd * math.sqrt(365) if sd else 0.0,
        "trades": sim["trades"],
        "win_rate": sum(r > 0 for r in pr) / len(pr) if pr else 0.0,
        "exposure": sim["invested"],
    }


def buy_hold(u: Universe, pairs: list, start: int, end: int) -> dict:
    """Equal-dollar buy-and-hold of `pairs` (those with data on day `start`), no rebalancing."""
    live = [p for p in pairs if u.open[p][start]]
    eq = []
    for d in range(start, end):
        vals = []
        for p in live:
            # carry the last known price over gaps
            o = next((u.open[p][k] for k in range(d, start - 1, -1) if u.open[p][k]), u.open[p][start])
            vals.append(o / u.open[p][start])
        eq.append([u.days[d], sum(vals) / len(vals)])
    return stats({"equity": eq, "trades": len(live), "invested": 1.0, "period_returns": []})


# ─────────────────────────────────────────────
# CHECKS
# ─────────────────────────────────────────────

def random_selection(u: Universe, params: dict, costs: Costs, held_counts: list, runs: int, seed: int = 7) -> list:
    """
    Random picks with the same rebalance dates, number of coins held and
    number of coins kept from the previous week as the strategy — so random
    runs trade about as often (and pay about the same fees). Tests whether the
    momentum *selection* adds anything.
    """
    rng = random.Random(seed)
    totals = []
    for _ in range(runs):
        def picker(n, picks, eligible, held):
            k, kept = held_counts[n] if n < len(held_counts) else (len(picks), 0)
            keep_pool = [c for c in held if c in eligible]
            keep = rng.sample(keep_pool, min(kept, len(keep_pool), k))
            fresh = [c for c in eligible if c not in keep]
            return keep + rng.sample(fresh, min(k - len(keep), len(fresh)))
        totals.append(stats(simulate(u, params, costs, picker=picker))["total_return"])
    return totals


def walk_forward(u: Universe, costs: Costs, folds: int = 4) -> dict:
    combos = [dict(zip(GRID, vals)) for vals in itertools.product(*GRID.values())]
    first = max(warmup(c) for c in combos)
    bounds = [first + int((len(u) - first) * k / folds) for k in range(folds + 1)]
    oos_value, chosen, oos_trades = 1.0, [], 0
    for f in range(1, folds):
        best, best_ret = None, None
        for combo in combos:
            r = stats(simulate(u, combo, costs, start=first, end=bounds[f]))
            if r["trades"] and (best_ret is None or r["total_return"] > best_ret):
                best, best_ret = combo, r["total_return"]
        oos = stats(simulate(u, best, costs, start=bounds[f], end=bounds[f + 1]))
        oos_value *= 1 + oos["total_return"]
        oos_trades += oos["trades"]
        chosen.append({"fold": f, "params": best, "in_sample_return": best_ret,
                       "out_of_sample_return": oos["total_return"]})
    btc_oos = buy_hold(u, [BTC], bounds[1], len(u))["total_return"]
    return {"combos_tested": len(combos), "folds": chosen,
            "out_of_sample": {"total_return": oos_value - 1, "trades": oos_trades, "win_rate": 0.0,
                              "btc_buy_hold": btc_oos}}


def verdict(m: dict, rand_pct: float, wf: dict, doubled: dict) -> str:
    if m["trades"] < 30:
        return f"Too few trades to judge ({m['trades']})."
    fails = []
    if wf["out_of_sample"]["total_return"] <= 0:
        fails.append("loses on unseen data (walk-forward)")
    if rand_pct < 0.90:
        fails.append(f"random picks did as well in {100 - rand_pct*100:.0f}% of runs")
    if doubled["total_return"] <= 0:
        fails.append("loses with costs doubled")
    if not fails:
        return "Passes all pre-registered checks — worth a dry run, not yet proof."
    return "Fails: " + "; ".join(fails) + "."


def robustness(u: Universe, costs: Costs, default: dict, folds: int = 4) -> dict:
    """
    RESEARCH.md #6b: every grid combo over one common window and its two
    halves, against BTC buy-and-hold; plus the default settings on the
    walk-forward's unseen periods.
    """
    combos = [dict(zip(GRID, vals)) for vals in itertools.product(*GRID.values())]
    first = max(warmup(c) for c in combos)
    mid = first + (len(u) - first) // 2
    btc = {"full": buy_hold(u, [BTC], first, len(u)), "h1": buy_hold(u, [BTC], first, mid),
           "h2": buy_hold(u, [BTC], mid, len(u))}
    rows = []
    for combo in combos:
        r = {k: stats(simulate(u, combo, costs, start=a, end=b))
             for k, (a, b) in {"full": (first, len(u)), "h1": (first, mid), "h2": (mid, len(u))}.items()}
        rows.append({"params": combo, **r,
                     "beats_sharpe": r["full"]["sharpe"] > btc["full"]["sharpe"],
                     "beats_both_halves": (r["h1"]["total_return"] > btc["h1"]["total_return"]
                                           and r["h2"]["total_return"] > btc["h2"]["total_return"])})
    rows.sort(key=lambda r: r["full"]["sharpe"], reverse=True)

    bounds_first = first + int((len(u) - first) / folds)
    d = {**strategies.MOMENTUM_DEFAULTS, **default}
    default_unseen = stats(simulate(u, d, costs, start=bounds_first))["total_return"]
    btc_unseen = buy_hold(u, [BTC], bounds_first, len(u))["total_return"]

    n = len(rows)
    sharpe_count = sum(r["beats_sharpe"] for r in rows)
    halves_count = sum(r["beats_both_halves"] for r in rows)
    checks = {
        "two_thirds_beat_btc_sharpe": sharpe_count >= 2 * n / 3,
        "half_beat_btc_both_halves": halves_count >= n / 2,
        "default_beats_btc_unseen": default_unseen > btc_unseen,
    }
    return {"combos": n, "window_days": len(u) - first, "btc": btc, "rows": rows,
            "beats_sharpe": sharpe_count, "beats_both_halves": halves_count,
            "default_unseen": default_unseen, "btc_unseen": btc_unseen,
            "checks": checks, "robust": all(checks.values())}


def print_robustness(r: dict, default: dict) -> None:
    b = r["btc"]
    print(f"\n  ══ MOMENTUM ROBUSTNESS · {r['combos']} setting combos · common window {r['window_days']} days ══")
    print(f"  BTC buy & hold: whole {pct(b['full']['total_return'])} (Sharpe {b['full']['sharpe']:.2f}) | "
          f"1st half {pct(b['h1']['total_return'])} | 2nd half {pct(b['h2']['total_return'])}")
    print(f"\n  {'lookback':>8} {'top_k':>5} {'filter':>6} {'Whole':>9} {'Sharpe':>7} {'Max DD':>7} "
          f"{'1st half':>9} {'2nd half':>9}  beats BTC")
    for row in r["rows"]:
        p = row["params"]
        mark = " ◀ default" if all(p[k] == default.get(k) for k in GRID) else ""
        tags = ("Sharpe " if row["beats_sharpe"] else "") + ("both-halves" if row["beats_both_halves"] else "")
        print(f"  {p['lookback']:>8} {p['top_k']:>5} {p['filter_sma']:>6} {pct(row['full']['total_return']):>9} "
              f"{row['full']['sharpe']:>7.2f} {row['full']['max_drawdown']*100:>6.1f}% "
              f"{pct(row['h1']['total_return']):>9} {pct(row['h2']['total_return']):>9}  {tags}{mark}")
    c = r["checks"]
    ok = lambda x: "PASS" if x else "fail"  # noqa: E731
    print(f"\n  1. Beat BTC on Sharpe (need ≥ {2*r['combos']/3:.0f}): {r['beats_sharpe']}/{r['combos']} "
          f"→ {ok(c['two_thirds_beat_btc_sharpe'])}")
    print(f"  2. Beat BTC in both halves (need ≥ {r['combos']/2:.0f}): {r['beats_both_halves']}/{r['combos']} "
          f"→ {ok(c['half_beat_btc_both_halves'])}")
    print(f"  3. Default settings on the walk-forward's unseen periods: {pct(r['default_unseen'])} vs BTC "
          f"{pct(r['btc_unseen'])} → {ok(c['default_beats_btc_unseen'])}")
    print("  ➜ " + ("Robust enough to paper-trade (not to trade real money)." if r["robust"] else
                    "Not robust: stop testing momentum; fall back to DCA + trend filter."))


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", default=",".join(UNIVERSE))
    ap.add_argument("--days", type=float, default=1095)
    ap.add_argument("--source", choices=["coinbase", "kraken"], default="coinbase")
    ap.add_argument("--costs", choices=list(COST_PRESETS), default="taker")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--random-runs", type=int, default=200)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--robustness", action="store_true",
                    help="run every settings combo over a common window and both halves (RESEARCH.md #6b)")
    args = ap.parse_args()

    costs = COST_PRESETS[args.costs]
    params = {**strategies.MOMENTUM_DEFAULTS, **{k: int(float(v)) for k, v in (s.split("=", 1) for s in args.set)}}
    pairs = args.pairs.split(",")
    if BTC not in pairs:
        pairs.insert(0, BTC)  # needed for the market filter

    candles = {}
    for pair in pairs:
        print(f"  Loading {pair} daily candles ({args.days:g} days)...")
        try:
            cs = market_data.get_candles(pair, 1440, args.days, args.source, progress=False)
        except Exception as e:
            print(f"    ⚠️ skipped: {e}")
            continue
        if len(cs) < 200:
            print(f"    ⚠️ skipped: only {len(cs)} days of data")
            continue
        candles[pair] = cs
    if BTC not in candles:
        raise SystemExit("BTC data is required for the market filter.")
    u = Universe(candles)
    start = warmup(params)

    if args.robustness:
        r = robustness(u, costs, params, args.folds)
        print_robustness(r, params)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"robustness_momentum_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps({**r, "costs": asdict(costs), "default": params}, indent=2, default=str))
        print(f"\n  Report saved: {out}")
        return

    sim = simulate(u, params, costs)
    m = stats(sim)
    doubled = stats(simulate(u, params, costs.scaled(2)))
    btc = buy_hold(u, [BTC], start, len(u))
    basket = buy_hold(u, list(candles), start, len(u))
    print(f"\n  Running random-selection baseline ({args.random_runs} runs) and walk-forward...")
    rand = random_selection(u, params, costs, sim["held_counts"], args.random_runs)
    rand_pct = sum(r < m["total_return"] for r in rand) / len(rand) if rand else 0.0
    wf = walk_forward(u, costs, args.folds)
    text = verdict(m, rand_pct, wf, doubled)

    years = (len(u) - start) / 365
    print(f"\n  ══ MOMENTUM · {len(candles)} coins · {years:.1f} years · settings {params} ══")
    print(f"  Costs: fee {costs.fee*100:.2f}% + slippage {costs.slippage*100:.2f}% per side")
    print(f"  {'':22}{'Total':>10}{'Per year':>10}{'Max DD':>9}{'Sharpe':>8}")
    for name, r in (("Momentum", m), ("BTC buy & hold", btc), ("Equal-weight basket", basket)):
        print(f"  {name:22}{pct(r['total_return']):>10}{pct(r['cagr']):>10}{r['max_drawdown']*100:>8.1f}%"
              f"{r['sharpe']:>8.2f}")
    print(f"  Trades {m['trades']} over {sim['rebalances']} weekly rebalances | invested {m['exposure']*100:.0f}% "
          f"of days | winning weeks {m['win_rate']*100:.0f}%")
    print(f"  Random picks (same timing, coin count & turnover, {len(rand)} runs): median "
          f"{pct(statistics.median(rand))} — momentum beats {rand_pct*100:.0f}% of them")
    print(f"  Costs doubled: {pct(doubled['total_return'])}")
    o = wf["out_of_sample"]
    print(f"  Walk-forward ({wf['combos_tested']} combos, picked on past data): unseen-data "
          f"{pct(o['total_return'])} vs BTC buy & hold {pct(o['btc_buy_hold'])} over the same period")
    for f in wf["folds"]:
        print(f"    fold {f['fold']}: picked {f['params']} → in-sample {pct(f['in_sample_return'])}, "
              f"next period {pct(f['out_of_sample_return'])}")
    print(f"  ➜ {text}")
    print("  Note: the coin list is today's survivors, which flatters any long-only test.")

    report = {"strategy": "momentum", "created": int(time.time()), "days": args.days, "costs": asdict(costs),
              "pairs": {"PORTFOLIO": {
                  "params": params,
                  "metrics": {**m, "buy_hold": btc["total_return"], "buy_hold_max_drawdown": btc["max_drawdown"]},
                  "basket": basket, "costs_doubled": doubled,
                  "random_median": statistics.median(rand) if rand else None, "random_percentile": rand_pct,
                  "walk_forward": wf, "verdict": text,
                  "equity": [[t, round(v, 6)] for t, v in sim["equity"]]}}}
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"momentum_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n  Report saved: {out}")


if __name__ == "__main__":
    main()
