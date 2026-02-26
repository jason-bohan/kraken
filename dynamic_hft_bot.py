#!/usr/bin/env python3
"""
Dynamic HFT Bot — Kraken
Automatically finds the top 5 most volatile pairs across ALL of Kraken
every 15 minutes and trades them aggressively.

Strategy per pair:
  - Buy on RSI < 35 OR 10-20% dip from recent high
  - Sell at +3% profit
  - Stop at -10% loss
  - Max $2 per trade, $10 total exposure across all pairs

Usage:
    python3 dynamic_hft_bot.py              # live
    python3 dynamic_hft_bot.py --dry        # dry run with $50 fake balance
    python3 dynamic_hft_bot.py --dry --sim-balance 100  # custom fake balance

Requires kraken_connection.py in same folder.
.env needs: KRAKEN_API_KEY, KRAKEN_API_SECRET

NOTE: To debug pair discovery, set DEBUG_PAIRS = True below.
To rediscover ticker fields: print(list(tickers["XBTUSD"].keys()))
Ticker fields: a=ask, b=bid, c=last, v=volume, p=vwap, h=high, l=low, o=open
"""

import os
import time
import argparse
import threading
from datetime import datetime
from kraken_connection import (
    get_balance, get_ticker, get_ohlc, place_order
)
import requests as req

BASE_URL = "https://api.kraken.com"

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
TOP_N            = 5            # number of pairs to trade at once
RESCAN_SECS      = 900          # rescan for new top pairs every 15 min
CHECK_SECS       = 30           # how often each pair thread checks price
PROFIT_PCT       = 0.03         # 3% profit target — aggressive
STOP_PCT         = 0.10         # 10% stop loss — cut fast
RSI_OVERSOLD     = 35           # entry RSI threshold
DIP_MIN          = 0.10         # enter on 10%+ dip
DIP_MAX          = 0.20         # skip if drop > 20% (crash territory)
RSI_PERIOD       = 14
MAX_USD_PER_PAIR = 2.0          # max $ to risk per pair
MAX_TOTAL_USD    = 10.0         # hard cap: never risk more than $10 total
MIN_VOLUME_USD   = 500_000      # skip pairs with < $500k 24h volume (illiquid)
MIN_VOLATILITY   = 0.03         # skip pairs with < 3% 24h swing (boring)
RESERVE_USD      = 1.0          # always keep $1 in reserve
DRY_START_BAL    = 50.0         # default fake starting balance for dry run
DEBUG_PAIRS      = False        # set True to print all pair scores during scan
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT    = os.getenv("TELEGRAM_CHAT_ID", "")


# ─────────────────────────────────────────────
# SHARED STATE
# ─────────────────────────────────────────────
positions      = {}             # { pair: { entry_price, volume, cost } }
positions_lock = threading.Lock()
deployed_usd   = 0.0
deployed_lock  = threading.Lock()
active_threads = {}

# Dry run virtual balance — protected by its own lock
dry_balance     = DRY_START_BAL
dry_balance_lock = threading.Lock()
dry_start_bal   = DRY_START_BAL

# Trade log for dry run summary
dry_trades      = []            # list of { pair, side, price, volume, pnl_usd }
dry_trades_lock = threading.Lock()


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def tg(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=8
        )
    except:
        pass


# ─────────────────────────────────────────────
# DRY RUN BALANCE HELPERS
# ─────────────────────────────────────────────
def dry_buy(pair: str, volume: float, price: float) -> bool:
    """Deduct cost from virtual balance. Returns False if insufficient funds."""
    global dry_balance
    cost = volume * price
    with dry_balance_lock:
        if dry_balance - cost < RESERVE_USD:
            return False
        dry_balance -= cost
    with dry_trades_lock:
        dry_trades.append({
            "pair": pair, "side": "buy",
            "price": price, "volume": volume, "pnl_usd": -cost
        })
    return True


def dry_sell(pair: str, volume: float, price: float, entry_price: float):
    """Add proceeds to virtual balance and record P&L."""
    global dry_balance
    proceeds = volume * price
    cost     = volume * entry_price
    pnl      = proceeds - cost
    with dry_balance_lock:
        dry_balance += proceeds
    with dry_trades_lock:
        dry_trades.append({
            "pair": pair, "side": "sell",
            "price": price, "volume": volume, "pnl_usd": pnl
        })
    return pnl


def dry_available_usd() -> float:
    """How much virtual USD is available to trade."""
    with dry_balance_lock:
        return max(0, dry_balance - RESERVE_USD)


def print_dry_summary():
    """Print full dry run P&L summary."""
    with dry_balance_lock:
        bal = dry_balance
    with dry_trades_lock:
        trades = list(dry_trades)

    sells   = [t for t in trades if t["side"] == "sell"]
    wins    = [t for t in sells if t["pnl_usd"] > 0]
    losses  = [t for t in sells if t["pnl_usd"] <= 0]
    total_pnl = sum(t["pnl_usd"] for t in sells)
    win_rate  = len(wins) / len(sells) * 100 if sells else 0

    print("\n" + "=" * 55)
    print("  📊 DRY RUN BALANCE SUMMARY")
    print("=" * 55)
    print(f"  Starting balance : ${dry_start_bal:.2f}")
    print(f"  Current balance  : ${bal:.2f}")
    print(f"  Total P&L        : ${total_pnl:+.2f}")
    print(f"  Net change       : {((bal - dry_start_bal) / dry_start_bal * 100):+.2f}%")
    print(f"  Completed trades : {len(sells)}")
    print(f"  Wins / Losses    : {len(wins)}W / {len(losses)}L ({win_rate:.0f}% win rate)")
    if sells:
        best  = max(sells, key=lambda t: t["pnl_usd"])
        worst = min(sells, key=lambda t: t["pnl_usd"])
        print(f"  Best trade       : {best['pair']} ${best['pnl_usd']:+.4f}")
        print(f"  Worst trade      : {worst['pair']} ${worst['pnl_usd']:+.4f}")
    print("=" * 55)


# ─────────────────────────────────────────────
# PAIR DISCOVERY
# ─────────────────────────────────────────────
def get_all_tickers() -> dict:
    """
    Fetch all Kraken tickers in one API call (public, no auth needed).
    NOTE: To see all fields: print(list(result["XBTUSD"].keys()))
    Fields: a=ask, b=bid, c=last, v=volume[today,24h], h=high, l=low, o=open
    """
    try:
        res = req.get(f"{BASE_URL}/0/public/Ticker", timeout=10)
        if res.status_code == 200:
            return res.json().get("result", {})
    except Exception as e:
        print(f"  ⚠️ Ticker fetch error: {e}")
    return {}


def score_pairs(tickers: dict) -> list:
    """
    Score all pairs by volatility and volume.
    Volatility = (24h high - 24h low) / 24h low
    """
    scored = []
    for pair, data in tickers.items():
        try:
            high       = float(data["h"][1])
            low        = float(data["l"][1])
            last       = float(data["c"][0])
            vol        = float(data["v"][1])
            volume_usd = vol * last

            if low == 0 or volume_usd < MIN_VOLUME_USD:
                continue

            volatility = (high - low) / low

            if volatility < MIN_VOLATILITY or last < 0.000001:
                continue

            scored.append((pair, volatility, volume_usd))

            if DEBUG_PAIRS:
                print(f"  {pair:<20} vol={volatility*100:.1f}% vol_usd=${volume_usd:,.0f}")

        except (KeyError, ValueError, ZeroDivisionError):
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def get_top_pairs(n: int = TOP_N) -> list:
    """Returns top N most volatile pairs on Kraken right now."""
    tickers = get_all_tickers()
    if not tickers:
        return []

    scored = score_pairs(tickers)
    top    = scored[:n]

    print(f"\n  🔍 Top {n} volatile pairs right now:")
    for pair, vol, vol_usd in top:
        print(f"     {pair:<20} {vol*100:.1f}% swing | ${vol_usd:,.0f} 24h volume")

    return [pair for pair, _, _ in top]


# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────
def calculate_rsi(closes: list, period: int = 14) -> float:
    """RSI from closing prices. NOTE: To debug: print(closes[-period:])"""
    if len(closes) < period + 1:
        return 50.0

    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-period + i] - closes[-period + i - 1]
        if delta >= 0:
            gains.append(delta); losses.append(0)
        else:
            gains.append(0); losses.append(abs(delta))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_pair_data(pair: str) -> tuple:
    """
    Returns (price, rsi, recent_high) for any pair.
    NOTE: To rediscover OHLC fields: print(get_ohlc(pair, 1)[0])
    Candle: [time, open, high, low, close, vwap, volume, count]
    """
    ticker = get_ticker(pair)
    if not ticker:
        return None, None, None

    try:
        price = float(ticker.get("a", [0])[0])
    except:
        return None, None, None

    candles = get_ohlc(pair, interval=1)  # 1-min candles for HFT
    if not candles or len(candles) < RSI_PERIOD + 5:
        return price, 50.0, price

    closes      = [float(c[4]) for c in candles]
    highs       = [float(c[2]) for c in candles]
    rsi         = calculate_rsi(closes, RSI_PERIOD)
    recent_high = max(highs[-30:])

    return price, rsi, recent_high


# ─────────────────────────────────────────────
# TRADE SIZING
# ─────────────────────────────────────────────
def get_trade_size(pair: str, price: float, dry_run: bool) -> float:
    """Position size respecting MAX_USD_PER_PAIR and MAX_TOTAL_USD."""
    if price <= 0:
        return 0.0

    with deployed_lock:
        remaining = MAX_TOTAL_USD - deployed_usd
        trade_usd = min(MAX_USD_PER_PAIR, remaining)

    if dry_run:
        avail     = dry_available_usd()
        trade_usd = min(trade_usd, avail)
        if trade_usd < 0.50:
            return 0.0
        return round(trade_usd / price, 6)

    balances  = get_balance()
    usd_avail = float(balances.get("ZUSD", 0))
    trade_usd = min(trade_usd, max(0, usd_avail - RESERVE_USD))

    if trade_usd < 0.50:
        return 0.0

    return round(trade_usd / price, 6)


# ─────────────────────────────────────────────
# PER-PAIR TRADING THREAD
# ─────────────────────────────────────────────
def trade_pair(pair: str, dry_run: bool, stop_event: threading.Event):
    """Runs in its own thread. Monitors one pair and trades it."""
    global deployed_usd
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] 🚀 Starting thread for {pair}")

    position = None

    while not stop_event.is_set():
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            price, rsi, recent_high = get_pair_data(pair)

            if price is None:
                time.sleep(CHECK_SECS)
                continue

            dip = (recent_high - price) / recent_high if recent_high else 0

            # ── MANAGE POSITION ──────────────────────────
            if position:
                entry       = position["entry_price"]
                volume      = position["volume"]
                pnl_pct     = (price - entry) / entry
                pnl_usd     = (price - entry) * volume

                # Show running dry balance next to P&L
                bal_str = ""
                if dry_run:
                    with dry_balance_lock:
                        bal_str = f" | 💰 Bal: ${dry_balance:.2f}"

                print(f"  [{ts}] {pair:<18} ${price:.4f} | RSI {rsi:.0f} | PnL {pnl_pct*100:+.2f}% (${pnl_usd:+.4f}){bal_str}")

                # Take profit
                if pnl_pct >= PROFIT_PCT:
                    print(f"  [{ts}] 💰 {pair} +{pnl_pct*100:.2f}% | Selling @ ${price:.4f}")
                    if dry_run:
                        actual_pnl = dry_sell(pair, volume, price, entry)
                        with dry_balance_lock:
                            new_bal = dry_balance
                        print(f"  [DRY] Sold {volume} {pair} | P&L: ${actual_pnl:+.4f} | Balance: ${new_bal:.2f}")
                    else:
                        ok, result = place_order(pair, "sell", "market", volume)
                        if not ok:
                            print(f"  ❌ Sell failed: {result}")
                            time.sleep(CHECK_SECS)
                            continue
                        tg(f"💰 *{pair}* +{pnl_pct*100:.2f}% (${pnl_usd:+.4f})")

                    with deployed_lock:
                        deployed_usd = max(0, deployed_usd - (entry * volume))
                    position = None
                    with positions_lock:
                        positions.pop(pair, None)

                # Stop loss
                elif pnl_pct <= -STOP_PCT:
                    print(f"  [{ts}] 🛑 {pair} {pnl_pct*100:.2f}% STOP | Selling @ ${price:.4f}")
                    if dry_run:
                        actual_pnl = dry_sell(pair, volume, price, entry)
                        with dry_balance_lock:
                            new_bal = dry_balance
                        print(f"  [DRY] Stop sold {volume} {pair} | P&L: ${actual_pnl:+.4f} | Balance: ${new_bal:.2f}")
                    else:
                        ok, result = place_order(pair, "sell", "market", volume)
                        if not ok:
                            print(f"  ❌ Stop sell failed: {result}")
                            time.sleep(CHECK_SECS)
                            continue
                        tg(f"🛑 *{pair}* stop {pnl_pct*100:.2f}% (${pnl_usd:+.4f})")

                    with deployed_lock:
                        deployed_usd = max(0, deployed_usd - (entry * volume))
                    position = None
                    with positions_lock:
                        positions.pop(pair, None)

            # ── LOOK FOR ENTRY ────────────────────────────
            else:
                rsi_signal = rsi <= RSI_OVERSOLD
                dip_signal = DIP_MIN <= dip <= DIP_MAX

                bal_str = f" | 💰 Bal: ${dry_balance:.2f}" if dry_run else ""
                print(f"  [{ts}] {pair:<18} ${price:.4f} | RSI {rsi:.0f} | Dip {dip*100:.1f}%{bal_str}")

                if rsi_signal or dip_signal:
                    reason = []
                    if rsi_signal: reason.append(f"RSI {rsi:.0f}")
                    if dip_signal: reason.append(f"dip -{dip*100:.1f}%")

                    volume = get_trade_size(pair, price, dry_run)
                    if volume <= 0:
                        print(f"  [{ts}] ⚠️ {pair} signal but no budget left")
                    else:
                        cost = volume * price
                        print(f"  [{ts}] 🎯 {pair} ENTRY: {', '.join(reason)} | Buying {volume} @ ${price:.4f} (${cost:.2f})")

                        if dry_run:
                            ok = dry_buy(pair, volume, price)
                            if ok:
                                position = {"entry_price": price, "volume": volume}
                                with positions_lock:
                                    positions[pair] = position
                                with deployed_lock:
                                    deployed_usd += cost
                                with dry_balance_lock:
                                    new_bal = dry_balance
                                print(f"  [DRY] Bought {volume} {pair} @ ${price:.4f} | Balance: ${new_bal:.2f}")
                            else:
                                print(f"  [DRY] ⚠️ Insufficient virtual balance")
                        else:
                            ok, result = place_order(pair, "buy", "market", volume)
                            if ok:
                                position = {"entry_price": price, "volume": volume}
                                with positions_lock:
                                    positions[pair] = position
                                with deployed_lock:
                                    deployed_usd += cost
                                tg(f"🛒 *{pair}* buy @ ${price:.4f} | {', '.join(reason)}")
                            else:
                                print(f"  ❌ Buy failed: {result}")

            time.sleep(CHECK_SECS)

        except Exception as e:
            print(f"  [{ts}] ⚠️ {pair} error: {e}")
            time.sleep(CHECK_SECS)

    print(f"  [{datetime.now().strftime('%H:%M:%S')}] 🛑 Thread stopped: {pair}")


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def run(dry_run: bool = False, sim_balance: float = DRY_START_BAL):
    global dry_balance, dry_start_bal

    if dry_run:
        dry_balance   = sim_balance
        dry_start_bal = sim_balance

    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    print("=" * 60)
    print(f"  Dynamic HFT Bot — Kraken  {mode}")
    print(f"  Top pairs       : {TOP_N} (rescanned every {RESCAN_SECS//60} min)")
    print(f"  Profit target   : +{PROFIT_PCT*100:.0f}%")
    print(f"  Stop loss       : -{STOP_PCT*100:.0f}%")
    print(f"  Max per pair    : ${MAX_USD_PER_PAIR}")
    print(f"  Max total       : ${MAX_TOTAL_USD}")
    print(f"  Check every     : {CHECK_SECS}s per pair")
    if dry_run:
        print(f"  Sim balance     : ${sim_balance:.2f}")
    print("=" * 60)
    tg(f"🤖 *Dynamic HFT Bot started* ({mode}) — {TOP_N} pairs, ${MAX_TOTAL_USD} max")

    stop_events = {}
    scan_cycle  = 0

    while True:
        try:
            scan_cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] 🔍 Scan #{scan_cycle} — finding top {TOP_N} volatile pairs...")

            new_pairs = get_top_pairs(TOP_N)
            if not new_pairs:
                print(f"  ⚠️ Could not fetch pairs, retrying in 60s")
                time.sleep(60)
                continue

            current_pairs = set(active_threads.keys())
            pairs_to_add  = set(new_pairs) - current_pairs
            pairs_to_drop = current_pairs - set(new_pairs)

            for pair in pairs_to_drop:
                print(f"  [{ts}] ⏹ Dropping {pair} from rotation")
                if pair in stop_events:
                    stop_events[pair].set()
                if pair in active_threads:
                    active_threads[pair].join(timeout=5)
                    del active_threads[pair]
                if pair in stop_events:
                    del stop_events[pair]

            for pair in pairs_to_add:
                print(f"  [{ts}] ▶️ Adding {pair} to rotation")
                stop_event = threading.Event()
                stop_events[pair] = stop_event
                t = threading.Thread(
                    target=trade_pair,
                    args=(pair, dry_run, stop_event),
                    daemon=True,
                    name=f"thread-{pair}"
                )
                t.start()
                active_threads[pair] = t

            if not pairs_to_add and not pairs_to_drop:
                print(f"  ✅ Same top {TOP_N} pairs — no rotation needed")

            # Portfolio summary
            with deployed_lock:
                dep = deployed_usd
            with positions_lock:
                pos_count = len(positions)

            if dry_run:
                with dry_balance_lock:
                    bal = dry_balance
                total_pnl = bal - dry_start_bal
                print(f"\n  📊 Positions: {pos_count} | Deployed: ${dep:.2f} | 💰 Balance: ${bal:.2f} | P&L: ${total_pnl:+.2f}")
                print_dry_summary()
            else:
                balances = get_balance()
                usd_bal  = float(balances.get("ZUSD", 0))
                print(f"\n  📊 Positions: {pos_count} | Deployed: ${dep:.2f} | USD balance: ${usd_bal:.2f}")

            time.sleep(RESCAN_SECS)

        except KeyboardInterrupt:
            print("\n\n👋 Shutting down all threads...")
            for pair, event in stop_events.items():
                event.set()
            for pair, thread in active_threads.items():
                thread.join(timeout=5)
            if dry_run:
                print_dry_summary()
            tg("🛑 *Dynamic HFT Bot stopped*")
            break
        except Exception as e:
            print(f"⚠️ Main loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Dry run — no real orders placed")
    parser.add_argument("--sim-balance", type=float, default=DRY_START_BAL,
                        help=f"Starting virtual balance for dry run (default: ${DRY_START_BAL})")
    args = parser.parse_args()
    run(dry_run=args.dry, sim_balance=args.sim_balance)
