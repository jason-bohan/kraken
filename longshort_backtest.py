#!/usr/bin/env python3
"""
longshort_backtest.py — long above the N-day average, short below it
(RESEARCH.md #9), against buy-and-hold and the long-only switch.

One coin, lump sum, no leverage. Decisions at the daily close, fills at the
next day's open with fees and slippage (a flip is two trades). A position is
opened at a fixed size and held until the signal flips, like a margin or
futures short: the short loses everything if the price doubles. Holding a
short costs a daily rate (default 0.03%/day ≈ 11%/year).

Examples:
    python3 longshort_backtest.py
    python3 longshort_backtest.py --short-cost 0.0012
"""

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone

import market_data
from backtester import COST_PRESETS, Costs, REPORT_DIR
from dca_backtest import START_YEARS, above_average, year_index
from momentum_backtest import Universe

ASSETS = ("XBTUSD", "ETHUSD")
NAMES = {"XBTUSD": "BTC", "ETHUSD": "ETH"}
DECISION_SMA = 100
INFO_SMAS = (50, 200)
SHORT_COST = 0.0003           # per day ≈ 11%/year (pre-registered)
INFO_SHORT_COSTS = (0.0, 0.0012)
MODES = ("hold", "long_only", "long_short")


def simulate(u: Universe, pair: str, mode: str, sma_n: int, start: int, costs: Costs,
             short_cost: float = SHORT_COST) -> dict:
    """Lump sum of 1.0 from day `start`. Returns final value, drawdown and trade stats."""
    cash = 1.0
    pos = 0                 # +1 long, -1 short, 0 flat
    entry_px = entry_val = 0.0
    held_short_days = 0
    trades = 0
    curve = []
    trade_cost = costs.fee + costs.slippage

    def mark(px: float) -> float:
        if pos == 1:
            return entry_val * px / entry_px
        if pos == -1:
            v = entry_val * (2 - px / entry_px) - entry_val * short_cost * held_short_days
            return max(0.0, v)
        return cash

    for d in range(start, len(u) - 1):
        px = u.open[pair][d + 1]
        if not px:
            continue
        if mode == "hold":
            want = 1
        else:
            up = above_average(u, pair, d, sma_n)
            if up is None:
                want = pos
            elif mode == "long_only":
                want = 1 if up else 0
            else:
                want = 1 if up else -1
        if want != pos:
            value = mark(px)
            if pos != 0:
                value *= 1 - trade_cost          # close the old position
                trades += 1
            cash, pos, held_short_days = value, 0, 0
            if want != 0 and cash > 0:
                entry_val = cash * (1 - trade_cost)
                entry_px = px
                pos = want
                trades += 1
        if pos == -1:
            held_short_days += 1
        curve.append(mark(px))
        if curve[-1] <= 0:
            break  # wiped out

    peak, dd = 0.0, 0.0
    for v in curve:
        peak = max(peak, v)
        if peak:
            dd = max(dd, 1 - v / peak)
    final = curve[-1] if curve else 1.0
    return {"final": final, "max_drawdown": dd, "trades": trades}


def evaluate(u: Universe, pair: str, sma_n: int, costs: Costs, short_cost: float) -> list:
    rows = []
    for year in START_YEARS:
        start = year_index(u, year)
        if start is None:
            continue
        r = {m: simulate(u, pair, m, sma_n, start, costs, short_cost) for m in MODES}
        beats_lo = r["long_short"]["final"] > r["long_only"]["final"]
        dd_ok = r["long_short"]["max_drawdown"] <= r["hold"]["max_drawdown"]
        rows.append({"year": year, **r, "beats_long_only": beats_lo, "dd_ok": dd_ok})
    return rows


def verdict(rows: list, stressed: dict) -> tuple[bool, list]:
    c1 = sum(r["beats_long_only"] for r in rows)
    c2 = sum(r["dd_ok"] for r in rows)
    c3 = stressed["long_short"]["final"] > stressed["long_only"]["final"]
    checks = [(f"Beats the long-only switch in {c1}/{len(rows)} windows (need 5)", c1 >= 5),
              (f"Drawdown no worse than buy & hold in {c2}/{len(rows)} windows (need 5)", c2 >= 5),
              (f"From 2017 with costs doubled: long/short {stressed['long_short']['final']:.2f}× vs "
               f"long-only {stressed['long_only']['final']:.2f}×", c3)]
    return all(ok for _, ok in checks), checks


def print_table(pair: str, rows: list) -> None:
    print(f"\n  ── {NAMES[pair]} · $1 lump sum · {DECISION_SMA}-day average · short cost "
          f"{SHORT_COST*100:.2f}%/day ──")
    print(f"  {'Start':>5} │ {'Hold':>7} {'DD':>5} │ {'Long-only':>9} {'DD':>5} │ {'Long/short':>10} {'DD':>5} {'trades':>6}"
          f" │ beats LO  DD≤hold")
    for r in rows:
        h, lo, ls = r["hold"], r["long_only"], r["long_short"]
        print(f"  {r['year']:>5} │ {h['final']:>6.2f}× {h['max_drawdown']*100:>4.0f}% │ {lo['final']:>8.2f}× "
              f"{lo['max_drawdown']*100:>4.0f}% │ {ls['final']:>9.2f}× {ls['max_drawdown']*100:>4.0f}% {ls['trades']:>6}"
              f" │ {'yes' if r['beats_long_only'] else 'no':>8}  {'yes' if r['dd_ok'] else 'no':>7}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=3800)
    ap.add_argument("--costs", choices=list(COST_PRESETS), default="taker")
    ap.add_argument("--short-cost", type=float, default=SHORT_COST, help="per day, e.g. 0.0003 ≈ 11%%/year")
    ap.add_argument("--source", choices=["coinbase", "kraken"], default="coinbase")
    args = ap.parse_args()
    costs = COST_PRESETS[args.costs]

    candles = {}
    for pair in ASSETS:
        print(f"  Loading {pair} daily candles ({args.days:g} days)...")
        candles[pair] = market_data.get_candles(pair, 1440, args.days, args.source, progress=False)
    u = Universe(candles)
    print(f"  Data: {datetime.fromtimestamp(u.days[0], timezone.utc).date()} → "
          f"{datetime.fromtimestamp(u.days[-1], timezone.utc).date()}. Costs: fee {costs.fee*100:.2f}% + "
          f"slippage {costs.slippage*100:.2f}% per trade")

    report = {"idea": "RESEARCH.md #9", "created": int(time.time()), "costs": asdict(costs),
              "short_cost": args.short_cost, "results": {}}
    for pair in ASSETS:
        rows = evaluate(u, pair, DECISION_SMA, costs, args.short_cost)
        print_table(pair, rows)
        start = year_index(u, 2017)
        stressed = {m: simulate(u, pair, m, DECISION_SMA, start, costs.scaled(2), args.short_cost * 2)
                    for m in MODES}
        passed, checks = verdict(rows, stressed)
        for text, ok in checks:
            print(f"  {'PASS' if ok else 'fail'}  {text}")
        info = []
        for sc in INFO_SHORT_COSTS:
            r = evaluate(u, pair, DECISION_SMA, costs, sc)
            info.append(f"short cost {sc*100:.2f}%/day: beats long-only in {sum(x['beats_long_only'] for x in r)}/{len(r)}")
        for n in INFO_SMAS:
            r = evaluate(u, pair, n, costs, args.short_cost)
            info.append(f"{n}-day average: beats long-only in {sum(x['beats_long_only'] for x in r)}/{len(r)}")
        print("  For information: " + "; ".join(info))
        print(f"  ➜ {NAMES[pair]}: {'PASS' if passed else 'FAIL'}" + (" (decision is judged on BTC)" if pair != "XBTUSD" else ""))
        report["results"][pair] = {"rows": rows, "stressed_from_2017": stressed, "passed": passed, "info": info}

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"longshort_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n  Report saved: {out}")


if __name__ == "__main__":
    main()
