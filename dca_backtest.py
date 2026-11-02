#!/usr/bin/env python3
"""
dca_backtest.py — regular buying (DCA) with and without a trend safety
switch (RESEARCH.md #8).

Every week $25 is added. Three versions on the same weeks:
  plain   buy with every contribution
  pause   buy only while the coin closes above its N-day average; otherwise
          keep the contribution in cash (never sell)
  switch  above the average: invest all cash; below it: sell to cash

Decisions use completed daily closes and fill at the next day's open with fees
and slippage. Windows start on 1 January of 2017–2023 and run to the latest
data. Drawdown is measured on a time-weighted "unit price", so new
contributions can't hide losses.

Examples:
    python3 dca_backtest.py
    python3 dca_backtest.py --costs maker
"""

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone

import market_data
from backtester import COST_PRESETS, Costs, REPORT_DIR
from momentum_backtest import Universe

PORTFOLIOS = {"BTC": {"XBTUSD": 1.0}, "ETH": {"ETHUSD": 1.0}, "50/50": {"XBTUSD": 0.5, "ETHUSD": 0.5}}
START_YEARS = range(2017, 2024)
DECISION_SMA = 100            # pre-registered; 50 and 200 are informational
INFO_SMAS = (50, 200)
CONTRIBUTION = 25.0
MODES = ("plain", "pause", "switch")


def year_index(u: Universe, year: int) -> int | None:
    t = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    return next((i for i, d in enumerate(u.days) if d >= t), None)


def above_average(u: Universe, pair: str, d: int, n: int) -> bool | None:
    window = u.close[pair][d - n + 1:d + 1] if d - n + 1 >= 0 else []
    if len(window) < n or None in window:
        return None
    return window[-1] > sum(window) / n


def simulate(u: Universe, weights: dict, mode: str, sma_n: int, start: int, costs: Costs,
             contribution: float = CONTRIBUTION) -> dict:
    """Weekly contributions from day `start` to the end of the data."""
    cash = {p: 0.0 for p in weights}
    units = {p: 0.0 for p in weights}
    last_px = {p: None for p in weights}
    fund_units = contributed = fees = 0.0
    trades = 0
    unit_prices, invested_days = [], 0

    def price(p, i):
        px = u.open[p][i]
        if px:
            last_px[p] = px
        return px

    def value(i):
        total = sum(cash.values())
        for p in weights:
            px = price(p, i) or last_px[p]
            if px:
                total += units[p] * px
        return total

    for d in range(start, len(u) - 1):
        nxt = d + 1
        if (d - start) % 7 == 0:
            v = value(nxt)
            unit_price = v / fund_units if fund_units else 1.0
            fund_units += contribution / unit_price
            contributed += contribution
            for p, w in weights.items():
                cash[p] += contribution * w
                px = price(p, nxt)
                if not px:
                    continue  # no market yet: keep the cash
                up = above_average(u, p, d, sma_n)
                buy = mode == "plain" or up is True
                if buy and cash[p] > 0:
                    fill = px * (1 + costs.slippage)
                    qty = cash[p] / (fill * (1 + costs.fee))
                    fees += qty * fill * costs.fee
                    units[p] += qty
                    cash[p] = 0.0
                    trades += 1
                elif mode == "switch" and up is False and units[p] > 0:
                    fill = px * (1 - costs.slippage)
                    proceeds = units[p] * fill
                    fees += proceeds * costs.fee
                    cash[p] += proceeds * (1 - costs.fee)
                    units[p] = 0.0
                    trades += 1
        v = value(nxt)
        if fund_units:
            unit_prices.append(v / fund_units)
            invested_days += any(units[p] > 0 for p in weights)

    peak, dd = 0.0, 0.0
    for x in unit_prices:
        peak = max(peak, x)
        dd = max(dd, 1 - x / peak) if peak else dd
    final = value(len(u) - 1)
    return {"final": final, "contributed": contributed, "multiple": final / contributed if contributed else 0.0,
            "max_drawdown": dd, "unit_return": unit_prices[-1] - 1 if unit_prices else 0.0,
            "trades": trades, "fees": fees, "invested": invested_days / max(1, len(unit_prices))}


def evaluate(u: Universe, weights: dict, sma_n: int, costs: Costs) -> list:
    rows = []
    for year in START_YEARS:
        start = year_index(u, year)
        if start is None:
            continue
        res = {m: simulate(u, weights, m, sma_n, start, costs) for m in MODES}
        plain, switch = res["plain"], res["switch"]
        dd_cut = 1 - switch["max_drawdown"] / plain["max_drawdown"] if plain["max_drawdown"] else 0.0
        keeps = switch["final"] / plain["final"] if plain["final"] else 0.0
        rows.append({"year": year, **res, "dd_cut": dd_cut, "keeps": keeps,
                     "pass": dd_cut >= 1 / 3 and keeps >= 0.80})
    return rows


def print_table(name: str, rows: list) -> None:
    print(f"\n  ── {name} · $25/week · 100-day average ──")
    print(f"  {'Start':>5} {'Added':>8} │ {'Plain':>9} {'DD':>5} │ {'Pause':>9} {'DD':>5} │ "
          f"{'Switch':>9} {'DD':>5} │ {'DD cut':>6} {'Keeps':>6}  Pass?")
    for r in rows:
        p, q, s = r["plain"], r["pause"], r["switch"]
        print(f"  {r['year']:>5} {p['contributed']:>8,.0f} │ {p['final']:>9,.0f} {p['max_drawdown']*100:>4.0f}% │ "
              f"{q['final']:>9,.0f} {q['max_drawdown']*100:>4.0f}% │ {s['final']:>9,.0f} {s['max_drawdown']*100:>4.0f}% │ "
              f"{r['dd_cut']*100:>5.0f}% {r['keeps']*100:>5.0f}%  {'yes' if r['pass'] else 'no'}")
    n = sum(r["pass"] for r in rows)
    print(f"  Switch passes in {n}/{len(rows)} windows (needs 5/7 for BTC and 50/50)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=3800, help="history to load (default ~10.4 years)")
    ap.add_argument("--costs", choices=list(COST_PRESETS), default="taker")
    ap.add_argument("--source", choices=["coinbase", "kraken"], default="coinbase")
    args = ap.parse_args()
    costs = COST_PRESETS[args.costs]

    candles = {}
    for pair in ("XBTUSD", "ETHUSD"):
        print(f"  Loading {pair} daily candles ({args.days:g} days)...")
        candles[pair] = market_data.get_candles(pair, 1440, args.days, args.source, progress=False)
    u = Universe(candles)
    first = datetime.fromtimestamp(u.days[0], timezone.utc).date()
    last = datetime.fromtimestamp(u.days[-1], timezone.utc).date()
    print(f"  Data: {first} → {last}. Costs: fee {costs.fee*100:.2f}% + slippage {costs.slippage*100:.2f}% per trade")

    report = {"idea": "RESEARCH.md #8", "created": int(time.time()), "costs": asdict(costs), "results": {}}
    verdicts = {}
    for name, weights in PORTFOLIOS.items():
        rows = evaluate(u, weights, DECISION_SMA, costs)
        print_table(name, rows)
        info = {n: sum(r["pass"] for r in evaluate(u, weights, n, costs)) for n in INFO_SMAS}
        print("  For information: switch passes with a " +
              ", ".join(f"{n}-day average in {k}/{len(rows)}" for n, k in info.items()))
        report["results"][name] = {"rows": rows, "info_passes": info}
        verdicts[name] = sum(r["pass"] for r in rows) >= 5

    print("\n  ══ DECISION (pre-registered: BTC and 50/50, 100-day average) ══")
    for name in ("BTC", "50/50"):
        print(f"  {name}: {'PASS — the switch is worth using' if verdicts[name] else 'FAIL — plain DCA (or pause) is enough'}")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"dca_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n  Report saved: {out}")


if __name__ == "__main__":
    main()
