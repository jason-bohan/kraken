#!/usr/bin/env python3
"""
Backtesting Engine for Kraken Trading Strategies
Tests HFT Bot and Swing Bot strategies against historical OHLC data.

Usage:
    python3 backtester.py                    # full backtest, all pairs
    python3 backtester.py --pair SOLUSD      # single pair
    python3 backtester.py --pair XBTUSD --verbose  # detailed trade log
"""

import argparse
import time
import sys
from datetime import datetime, timedelta

import requests

# ---------------------------------------------------------------------------
# Kraken public API helper (standalone — no auth needed)
# ---------------------------------------------------------------------------
BASE_URL = "https://api.kraken.com"

def fetch_ohlc(pair: str, interval: int = 60, since: int = None) -> list:
    """Fetch OHLC candles from Kraken public API.
    Returns list of [time, open, high, low, close, vwap, volume, count].
    Max 720 candles per call.
    """
    url = f"{BASE_URL}/0/public/OHLC?pair={pair}&interval={interval}"
    if since:
        url += f"&since={since}"
    try:
        res = requests.get(url, timeout=12)
        body = res.json()
        if body.get("error"):
            print(f"  API error for {pair}: {body['error']}")
            return []
        result = body.get("result", {})
        candles = [v for k, v in result.items() if k != "last"]
        return candles[0] if candles else []
    except Exception as e:
        print(f"  Request failed for {pair}: {e}")
        return []


# ---------------------------------------------------------------------------
# RSI — matches the HFT bot's implementation
# ---------------------------------------------------------------------------
def calculate_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-period + i] - closes[-period + i - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_sma(values: list, period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    return sum(values[-period:]) / period


# ---------------------------------------------------------------------------
# Data fetching — all API calls happen here, then simulation is offline
# ---------------------------------------------------------------------------

# Pairs to test
HFT_PAIRS = [
    "XBTUSD", "ETHUSD", "SOLUSD", "XDGUSD", "DOTUSD",
    "WARUSD", "FARTCOINUSD", "KASUSD", "HYPEUSD", "TAOUSD",
]
SWING_PAIRS = ["XBTUSD", "ETHUSD", "SOLUSD", "XDGUSD", "DOTUSD"]

# Swing bot per-asset configs (from stock_swing_bot.py get_stock_config)
SWING_CONFIGS = {
    "XBTUSD":  {"rsi_oversold": 40, "rsi_overbought": 70, "dip_min": 0.02, "dip_max": 0.06, "profit_target": 0.04, "stop_loss": 0.02},
    "ETHUSD":  {"rsi_oversold": 35, "rsi_overbought": 75, "dip_min": 0.03, "dip_max": 0.08, "profit_target": 0.06, "stop_loss": 0.03},
    "SOLUSD":  {"rsi_oversold": 30, "rsi_overbought": 80, "dip_min": 0.05, "dip_max": 0.12, "profit_target": 0.08, "stop_loss": 0.04},
    "XDGUSD":  {"rsi_oversold": 35, "rsi_overbought": 70, "dip_min": 0.03, "dip_max": 0.08, "profit_target": 0.06, "stop_loss": 0.03},
    "DOTUSD":  {"rsi_oversold": 35, "rsi_overbought": 75, "dip_min": 0.04, "dip_max": 0.09, "profit_target": 0.07, "stop_loss": 0.035},
}

TRADE_SIZE_USD = 15.0


def fetch_all_data(pairs: list, intervals: list) -> dict:
    """Fetch OHLC data for all pairs and intervals. Returns nested dict:
    data[pair][interval] = list of candles
    """
    data = {}
    total_calls = len(pairs) * len(intervals)
    call_num = 0
    for pair in pairs:
        data[pair] = {}
        for iv in intervals:
            call_num += 1
            print(f"  [{call_num}/{total_calls}] Fetching {pair} {iv}m candles...")
            candles = fetch_ohlc(pair, interval=iv)
            if candles:
                data[pair][iv] = candles
                print(f"    Got {len(candles)} candles ({iv}m) for {pair}")
            else:
                data[pair][iv] = []
                print(f"    WARNING: No data for {pair} {iv}m")
            time.sleep(1.1)  # rate limit
    return data


# ---------------------------------------------------------------------------
# Simulation engine
# ---------------------------------------------------------------------------

def simulate_strategy(candles: list, params: dict, verbose: bool = False) -> list:
    """Run a strategy simulation on OHLC candles.

    params:
        rsi_oversold    — RSI entry threshold
        rsi_overbought  — block entry above this
        dip_min         — min dip from recent high to enter
        dip_max         — max dip (skip crashes)
        tp_pct          — take profit %
        sl_pct          — stop loss %
        trend_filter    — if True, skip buys when 24-period SMA is declining
        lookback_high   — number of candles to compute recent high (default 30)

    Each candle: [time, open, high, low, close, vwap, volume, count]
    Returns list of trade dicts.
    """
    rsi_oversold = params.get("rsi_oversold", 40)
    rsi_overbought = params.get("rsi_overbought", 70)
    dip_min = params.get("dip_min", 0.02)
    dip_max = params.get("dip_max", 0.15)
    tp_pct = params.get("tp_pct", 0.08)
    sl_pct = params.get("sl_pct", 0.04)
    trend_filter = params.get("trend_filter", False)
    lookback_high = params.get("lookback_high", 30)
    rsi_period = 14
    sma_period = 24

    trades = []
    position = None  # {"entry_price", "entry_time", "entry_idx"}

    # We need at least rsi_period+1 candles before we can compute RSI
    start_idx = max(rsi_period + 1, lookback_high, sma_period)

    for i in range(start_idx, len(candles)):
        c = candles[i]
        ts = int(c[0])
        o = float(c[1])
        high = float(c[2])
        low = float(c[3])
        close = float(c[4])

        # Compute RSI on closes up to this candle (inclusive)
        closes_so_far = [float(candles[j][4]) for j in range(max(0, i - rsi_period - 5), i + 1)]
        rsi = calculate_rsi(closes_so_far, rsi_period)

        # Recent high for dip calculation
        highs_window = [float(candles[j][2]) for j in range(max(0, i - lookback_high), i + 1)]
        recent_high = max(highs_window) if highs_window else close

        # SMA for trend filter
        if trend_filter:
            closes_window = [float(candles[j][4]) for j in range(max(0, i - sma_period - 1), i + 1)]
            if len(closes_window) >= sma_period + 1:
                sma_now = sum(closes_window[-sma_period:]) / sma_period
                sma_prev = sum(closes_window[-sma_period - 1:-1]) / sma_period
                sma_rising = sma_now >= sma_prev
            else:
                sma_rising = True
        else:
            sma_rising = True

        # --- MANAGE POSITION ---
        if position is not None:
            entry = position["entry_price"]
            tp_price = entry * (1 + tp_pct)
            sl_price = entry * (1 - sl_pct)

            # Check within this candle: did high hit TP or low hit SL?
            hit_tp = high >= tp_price
            hit_sl = low <= sl_price

            if hit_tp and hit_sl:
                # Both hit in same candle — assume SL hit first if open is closer to SL
                # Conservative: assume the worse outcome (SL) unless open > entry
                if o <= entry:
                    exit_price = sl_price
                    exit_reason = "stop_loss"
                else:
                    exit_price = tp_price
                    exit_reason = "take_profit"
            elif hit_tp:
                exit_price = tp_price
                exit_reason = "take_profit"
            elif hit_sl:
                exit_price = sl_price
                exit_reason = "stop_loss"
            else:
                continue  # still holding

            volume = TRADE_SIZE_USD / entry
            pnl_usd = (exit_price - entry) * volume
            pnl_pct = (exit_price - entry) / entry
            hold_candles = i - position["entry_idx"]

            trade = {
                "entry_price": entry,
                "exit_price": exit_price,
                "entry_time": position["entry_time"],
                "exit_time": ts,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "hold_candles": hold_candles,
                "reason": exit_reason,
            }
            trades.append(trade)
            if verbose:
                et = datetime.utcfromtimestamp(position["entry_time"]).strftime("%m/%d %H:%M")
                xt = datetime.utcfromtimestamp(ts).strftime("%m/%d %H:%M")
                print(f"    {exit_reason:12s} | entry ${entry:.4f} -> exit ${exit_price:.4f} | "
                      f"P&L ${pnl_usd:+.4f} ({pnl_pct*100:+.2f}%) | {et} -> {xt} ({hold_candles} candles)")
            position = None
            continue

        # --- LOOK FOR ENTRY ---
        dip = (recent_high - close) / recent_high if recent_high > 0 else 0

        rsi_signal = rsi <= rsi_oversold
        dip_signal = dip_min <= dip <= dip_max
        not_overbought = rsi < rsi_overbought

        if (rsi_signal or dip_signal) and not_overbought and sma_rising:
            position = {
                "entry_price": close,  # enter at close of signal candle
                "entry_time": ts,
                "entry_idx": i,
            }
            if verbose:
                et = datetime.utcfromtimestamp(ts).strftime("%m/%d %H:%M")
                print(f"    ENTRY      | ${close:.4f} | RSI {rsi:.1f} | Dip {dip*100:.1f}% | {et}")

    return trades


def summarize_trades(trades: list) -> dict:
    """Compute summary stats from a list of trades."""
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "avg_pnl": 0.0, "avg_hold": 0.0,
            "best": 0.0, "worst": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        }
    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    total_pnl = sum(t["pnl_usd"] for t in trades)
    avg_hold = sum(t["hold_candles"] for t in trades) / len(trades)

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "total_pnl": total_pnl,
        "avg_pnl": total_pnl / len(trades),
        "avg_hold": avg_hold,
        "best": max(t["pnl_usd"] for t in trades),
        "worst": min(t["pnl_usd"] for t in trades),
        "avg_win": sum(t["pnl_usd"] for t in wins) / len(wins) if wins else 0,
        "avg_loss": sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0,
    }


def print_pair_results(pair: str, summary: dict, params: dict):
    """Print results for one pair."""
    s = summary
    if s["trades"] == 0:
        print(f"  {pair:<16} No trades")
        return
    print(f"  {pair:<16} {s['trades']:>3} trades | "
          f"{s['win_rate']:5.1f}% win | "
          f"P&L ${s['total_pnl']:>+7.3f} | "
          f"Avg ${s['avg_pnl']:>+.4f} | "
          f"Best ${s['best']:>+.4f} / Worst ${s['worst']:>+.4f} | "
          f"Hold {s['avg_hold']:.0f} candles")


# ---------------------------------------------------------------------------
# Parameter grid for optimization
# ---------------------------------------------------------------------------
TP_LEVELS = [0.04, 0.06, 0.08, 0.10]
SL_LEVELS = [0.04, 0.05, 0.08]
TREND_OPTIONS = [False, True]


def run_optimization(candles_by_pair: dict, base_params: dict, label: str, verbose: bool = False):
    """Run parameter grid search across all pairs.
    candles_by_pair: {pair: candles_list}
    base_params: default params to use as template
    Returns (results_grid, best_combo)
    """
    results = []  # list of (params_dict, combined_summary)

    combos = []
    for tp in TP_LEVELS:
        for sl in SL_LEVELS:
            for tf in TREND_OPTIONS:
                combos.append({"tp_pct": tp, "sl_pct": sl, "trend_filter": tf})

    print(f"\n  Testing {len(combos)} parameter combinations for {label}...")

    for combo in combos:
        params = dict(base_params)
        params.update(combo)

        all_trades = []
        for pair, candles in candles_by_pair.items():
            if not candles:
                continue
            trades = simulate_strategy(candles, params)
            all_trades.extend(trades)

        summary = summarize_trades(all_trades)
        summary["params"] = combo
        results.append(summary)

    # Sort by total P&L
    results.sort(key=lambda r: r["total_pnl"], reverse=True)

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Backtest Kraken trading strategies")
    parser.add_argument("--pair", type=str, default=None, help="Test a single pair (e.g. SOLUSD)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print individual trades")
    args = parser.parse_args()

    print("=" * 80)
    print("  KRAKEN STRATEGY BACKTESTER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # Determine pairs to test
    if args.pair:
        pair = args.pair.upper()
        hft_pairs = [pair]
        swing_pairs = [pair]
    else:
        hft_pairs = list(HFT_PAIRS)
        swing_pairs = list(SWING_PAIRS)

    all_pairs = list(dict.fromkeys(hft_pairs + swing_pairs))  # deduplicated, preserves order

    # ------------------------------------------------------------------
    # PHASE 1: Fetch all data (the only phase with API calls)
    # ------------------------------------------------------------------
    print("\n--- PHASE 1: Fetching historical data from Kraken ---")
    print(f"  Pairs: {', '.join(all_pairs)}")
    print(f"  Intervals: 5m (RSI granularity), 60m (main backtest)")
    print(f"  Note: 60m candles cover ~30 days, 5m candles cover ~2.5 days")
    print()

    data = fetch_all_data(all_pairs, [5, 60])

    # Quick data summary
    print("\n  Data summary:")
    for pair in all_pairs:
        c5 = len(data.get(pair, {}).get(5, []))
        c60 = len(data.get(pair, {}).get(60, []))
        days_60 = c60 / 24 if c60 else 0
        print(f"    {pair:<16} 5m: {c5:>4} candles | 60m: {c60:>4} candles (~{days_60:.0f} days)")

    # ------------------------------------------------------------------
    # PHASE 2: HFT Bot Backtest (60m candles, current params)
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  PHASE 2: HFT BOT STRATEGY (current params)")
    print(f"  RSI < 40, Dip 2-15%, TP +8%, SL -4%, trend filter ON")
    print("=" * 80)

    hft_base_params = {
        "rsi_oversold": 40,
        "rsi_overbought": 70,
        "dip_min": 0.02,
        "dip_max": 0.15,
        "tp_pct": 0.08,
        "sl_pct": 0.04,
        "trend_filter": True,  # HFT bot uses uptrend = price >= ma20
        "lookback_high": 30,
    }

    hft_all_trades = []
    print(f"\n  Per-pair results (60m candles):")
    for pair in hft_pairs:
        candles = data.get(pair, {}).get(60, [])
        if not candles:
            print(f"  {pair:<16} No data")
            continue
        if args.verbose:
            print(f"\n  --- {pair} trades ---")
        trades = simulate_strategy(candles, hft_base_params, verbose=args.verbose)
        hft_all_trades.extend(trades)
        summary = summarize_trades(trades)
        print_pair_results(pair, summary, hft_base_params)

    hft_current = summarize_trades(hft_all_trades)
    print(f"\n  HFT TOTAL: {hft_current['trades']} trades | "
          f"{hft_current['win_rate']:.1f}% win rate | "
          f"P&L ${hft_current['total_pnl']:+.4f}")

    # Also test with 5m candles (shorter window but more granular)
    print(f"\n  HFT with 5m candles (2.5 days, more granular):")
    hft_5m_trades = []
    for pair in hft_pairs:
        candles = data.get(pair, {}).get(5, [])
        if not candles:
            continue
        trades = simulate_strategy(candles, hft_base_params, verbose=False)
        hft_5m_trades.extend(trades)
        summary = summarize_trades(trades)
        if summary["trades"] > 0:
            print_pair_results(pair, summary, hft_base_params)
    hft_5m_summary = summarize_trades(hft_5m_trades)
    if hft_5m_summary["trades"] > 0:
        print(f"\n  HFT 5m TOTAL: {hft_5m_summary['trades']} trades | "
              f"{hft_5m_summary['win_rate']:.1f}% win rate | "
              f"P&L ${hft_5m_summary['total_pnl']:+.4f}")

    # ------------------------------------------------------------------
    # PHASE 3: Swing Bot Backtest (60m candles, per-asset params)
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  PHASE 3: SWING BOT STRATEGY (per-asset params)")
    print("=" * 80)

    swing_all_trades = []
    print(f"\n  Per-pair results (60m candles):")
    for pair in swing_pairs:
        candles = data.get(pair, {}).get(60, [])
        if not candles:
            print(f"  {pair:<16} No data")
            continue

        cfg = SWING_CONFIGS.get(pair, {
            "rsi_oversold": 35, "rsi_overbought": 70,
            "dip_min": 0.03, "dip_max": 0.08,
            "profit_target": 0.06, "stop_loss": 0.03,
        })
        swing_params = {
            "rsi_oversold": cfg["rsi_oversold"],
            "rsi_overbought": cfg["rsi_overbought"],
            "dip_min": cfg["dip_min"],
            "dip_max": cfg["dip_max"],
            "tp_pct": cfg["profit_target"],
            "sl_pct": cfg["stop_loss"],
            "trend_filter": False,  # swing bot doesn't use SMA trend filter
            "lookback_high": 30,
        }

        if args.verbose:
            print(f"\n  --- {pair} trades (TP {cfg['profit_target']*100:.0f}% / SL {cfg['stop_loss']*100:.0f}%) ---")
        trades = simulate_strategy(candles, swing_params, verbose=args.verbose)
        swing_all_trades.extend(trades)
        summary = summarize_trades(trades)
        print_pair_results(pair, summary, swing_params)

    swing_current = summarize_trades(swing_all_trades)
    print(f"\n  SWING TOTAL: {swing_current['trades']} trades | "
          f"{swing_current['win_rate']:.1f}% win rate | "
          f"P&L ${swing_current['total_pnl']:+.4f}")

    # ------------------------------------------------------------------
    # PHASE 4: Parameter Optimization
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  PHASE 4: PARAMETER OPTIMIZATION")
    print(f"  TP levels: {[f'{x*100:.0f}%' for x in TP_LEVELS]}")
    print(f"  SL levels: {[f'{x*100:.0f}%' for x in SL_LEVELS]}")
    print(f"  Trend filter: ON / OFF")
    print("=" * 80)

    # HFT optimization (using 60m candles across all HFT pairs)
    hft_candles = {p: data.get(p, {}).get(60, []) for p in hft_pairs}
    hft_results = run_optimization(hft_candles, hft_base_params, "HFT Bot")

    print(f"\n  HFT Bot — Top 5 parameter combos:")
    print(f"  {'TP':>6} {'SL':>6} {'Trend':>7} | {'Trades':>6} {'Win%':>6} {'P&L':>10} {'AvgPnL':>10}")
    print(f"  {'-'*65}")
    for r in hft_results[:5]:
        p = r["params"]
        tf_str = "ON" if p["trend_filter"] else "OFF"
        print(f"  {p['tp_pct']*100:5.0f}% {p['sl_pct']*100:5.0f}% {tf_str:>7} | "
              f"{r['trades']:>6} {r['win_rate']:>5.1f}% ${r['total_pnl']:>+9.4f} ${r['avg_pnl']:>+9.4f}")

    # Swing optimization (using 60m candles, swing pairs, with a neutral base)
    swing_opt_base = {
        "rsi_oversold": 35,
        "rsi_overbought": 70,
        "dip_min": 0.03,
        "dip_max": 0.10,
        "lookback_high": 30,
    }
    swing_candles = {p: data.get(p, {}).get(60, []) for p in swing_pairs}
    swing_results = run_optimization(swing_candles, swing_opt_base, "Swing Bot")

    print(f"\n  Swing Bot — Top 5 parameter combos:")
    print(f"  {'TP':>6} {'SL':>6} {'Trend':>7} | {'Trades':>6} {'Win%':>6} {'P&L':>10} {'AvgPnL':>10}")
    print(f"  {'-'*65}")
    for r in swing_results[:5]:
        p = r["params"]
        tf_str = "ON" if p["trend_filter"] else "OFF"
        print(f"  {p['tp_pct']*100:5.0f}% {p['sl_pct']*100:5.0f}% {tf_str:>7} | "
              f"{r['trades']:>6} {r['win_rate']:>5.1f}% ${r['total_pnl']:>+9.4f} ${r['avg_pnl']:>+9.4f}")

    # Worst combos too (for context)
    print(f"\n  HFT Bot — Worst 3 parameter combos:")
    for r in hft_results[-3:]:
        p = r["params"]
        tf_str = "ON" if p["trend_filter"] else "OFF"
        print(f"  {p['tp_pct']*100:5.0f}% {p['sl_pct']*100:5.0f}% {tf_str:>7} | "
              f"{r['trades']:>6} {r['win_rate']:>5.1f}% ${r['total_pnl']:>+9.4f} ${r['avg_pnl']:>+9.4f}")

    # ------------------------------------------------------------------
    # PHASE 5: Trend Filter Analysis
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  PHASE 5: TREND FILTER ANALYSIS")
    print("=" * 80)

    for label, candles_map, base in [
        ("HFT", hft_candles, hft_base_params),
        ("Swing", swing_candles, swing_opt_base),
    ]:
        # Test same TP/SL with and without trend filter
        for tf_on in [False, True]:
            params = dict(base)
            params["trend_filter"] = tf_on
            all_t = []
            for pair, candles in candles_map.items():
                if candles:
                    all_t.extend(simulate_strategy(candles, params))
            s = summarize_trades(all_t)
            tf_str = "ON " if tf_on else "OFF"
            print(f"  {label:>6} | Trend filter {tf_str} | "
                  f"{s['trades']:>3} trades | {s['win_rate']:>5.1f}% win | P&L ${s['total_pnl']:>+8.4f}")

    # ------------------------------------------------------------------
    # PHASE 6: Final Summary & Recommendations
    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("  FINAL SUMMARY & RECOMMENDATIONS")
    print("=" * 80)

    # Current strategy performance
    print(f"\n  CURRENT STRATEGY PERFORMANCE (30-day backtest):")
    print(f"  {'Strategy':<12} {'Trades':>7} {'Win%':>7} {'Total P&L':>11} {'Per Trade':>11}")
    print(f"  {'-'*52}")
    print(f"  {'HFT Bot':<12} {hft_current['trades']:>7} {hft_current['win_rate']:>6.1f}% "
          f"${hft_current['total_pnl']:>+10.4f} ${hft_current['avg_pnl']:>+10.4f}")
    print(f"  {'Swing Bot':<12} {swing_current['trades']:>7} {swing_current['win_rate']:>6.1f}% "
          f"${swing_current['total_pnl']:>+10.4f} ${swing_current['avg_pnl']:>+10.4f}")

    # Best parameters found
    hft_best = hft_results[0] if hft_results else None
    swing_best = swing_results[0] if swing_results else None

    print(f"\n  BEST PARAMETERS FOUND:")
    if hft_best:
        p = hft_best["params"]
        print(f"  HFT Bot:   TP {p['tp_pct']*100:.0f}% / SL {p['sl_pct']*100:.0f}% / "
              f"Trend {'ON' if p['trend_filter'] else 'OFF'} "
              f"-> {hft_best['trades']} trades, {hft_best['win_rate']:.1f}% win, "
              f"P&L ${hft_best['total_pnl']:+.4f}")
    if swing_best:
        p = swing_best["params"]
        print(f"  Swing Bot: TP {p['tp_pct']*100:.0f}% / SL {p['sl_pct']*100:.0f}% / "
              f"Trend {'ON' if p['trend_filter'] else 'OFF'} "
              f"-> {swing_best['trades']} trades, {swing_best['win_rate']:.1f}% win, "
              f"P&L ${swing_best['total_pnl']:+.4f}")

    # Comparison
    print(f"\n  CURRENT vs OPTIMIZED:")
    if hft_best:
        delta = hft_best["total_pnl"] - hft_current["total_pnl"]
        arrow = "BETTER" if delta > 0 else "SAME/WORSE"
        print(f"  HFT:   Current P&L ${hft_current['total_pnl']:+.4f} -> "
              f"Best P&L ${hft_best['total_pnl']:+.4f} ({arrow}, delta ${delta:+.4f})")
    if swing_best:
        delta = swing_best["total_pnl"] - swing_current["total_pnl"]
        arrow = "BETTER" if delta > 0 else "SAME/WORSE"
        print(f"  Swing: Current P&L ${swing_current['total_pnl']:+.4f} -> "
              f"Best P&L ${swing_best['total_pnl']:+.4f} ({arrow}, delta ${delta:+.4f})")

    # Actionable recommendations
    print(f"\n  ACTIONABLE RECOMMENDATIONS:")
    print(f"  {'-'*60}")

    rec_num = 0
    if hft_best:
        hp = hft_best["params"]
        # Check if best differs from current
        if hp["tp_pct"] != 0.08 or hp["sl_pct"] != 0.04:
            rec_num += 1
            print(f"  {rec_num}. In dynamic_hft_bot.py, consider changing:")
            if hp["tp_pct"] != 0.08:
                print(f"     PROFIT_PCT = {hp['tp_pct']:.2f}  (currently 0.08)")
            if hp["sl_pct"] != 0.04:
                print(f"     STOP_PCT   = {hp['sl_pct']:.2f}  (currently 0.04)")

        # Trend filter
        if not hp["trend_filter"]:
            rec_num += 1
            print(f"  {rec_num}. HFT Bot: Consider DISABLING the trend filter (uptrend check).")
            print(f"     The backtest shows it may filter out profitable entries.")
        else:
            rec_num += 1
            print(f"  {rec_num}. HFT Bot: Keep the trend filter ON - it helps in this period.")

    if swing_best:
        sp = swing_best["params"]
        rec_num += 1
        print(f"  {rec_num}. For Swing Bot, best universal params: "
              f"TP {sp['tp_pct']*100:.0f}% / SL {sp['sl_pct']*100:.0f}% / "
              f"Trend {'ON' if sp['trend_filter'] else 'OFF'}")

    # Win rate analysis
    if hft_current["trades"] > 0 and hft_current["win_rate"] < 50:
        rec_num += 1
        print(f"  {rec_num}. HFT win rate is {hft_current['win_rate']:.0f}% -- consider tightening entry "
              f"(lower RSI threshold or wider dip requirement).")

    if hft_current["trades"] == 0 and swing_current["trades"] == 0:
        rec_num += 1
        print(f"  {rec_num}. No trades generated in the 30-day window. This could mean:")
        print(f"     - Entry conditions are too strict for current market")
        print(f"     - Consider relaxing RSI thresholds or dip requirements")

    if rec_num == 0:
        print(f"  Current parameters look optimal for the tested period.")

    print(f"\n  NOTE: This backtest covers ~30 days of 60-min candles.")
    print(f"  Results may differ in other market conditions.")
    print(f"  Trade size: ${TRADE_SIZE_USD} per position (matching real bot config).")
    print("=" * 80)


if __name__ == "__main__":
    main()
