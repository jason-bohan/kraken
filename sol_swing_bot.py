#!/usr/bin/env python3
"""
SOL Swing Bot — Kraken
Strategy: Buy SOL dips using RSI + price drop, sell on 1% gain, cut at 5% loss.
Runs continuously, checks every 60 seconds.

Usage:
    python3 sol_swing_bot.py          # live trading
    python3 sol_swing_bot.py --dry    # dry run (no real orders)

Requires kraken_connection.py in same folder.
.env needs: KRAKEN_API_KEY, KRAKEN_API_SECRET
"""

import os
import sys
import time
import argparse
from datetime import datetime, timezone
from kraken_connection import (
    get_balance, get_ticker, get_ohlc,
    place_order, get_open_orders, cancel_order
)

# ─────────────────────────────────────────────
# SETTINGS — OPTIMIZED FOR WIN RATE
# ─────────────────────────────────────────────
PAIR         = "SOLUSDT"       # SOL trading pair on Kraken
ASSET        = "SOL"           # asset name in balance
QUOTE        = "USDT"          # quote currency (change to ZUSD if using USD)
PROFIT_PCT   = 0.05            # 5% profit target (room for fees)
STOP_PCT     = 0.15            # 15% stop loss — cut early
RSI_PERIOD   = 14              # RSI lookback
RSI_OVERSOLD = 30              # enter when RSI below 30 (stricter)
DIP_MIN      = 0.10            # enter only when dip is AT LEAST 10% from recent high
DIP_MAX      = 0.18            # stop entering if dip exceeds 18% (avoid crashes)
MIN_TRADE    = 10.0            # minimum USD value per trade
RESERVE_USD  = 5.0             # keep $5 USD reserve
CHECK_SECS   = 120             # seconds between scans (slower = fewer false signals)


# ─────────────────────────────────────────────
# TELEGRAM (optional)
# ─────────────────────────────────────────────
def tg(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        import requests as req
        req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=8
        )
    except:
        pass


# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────
def calculate_rsi(closes: list, period: int = 14) -> float:
    """
    RSI from a list of closing prices.
    Returns float 0-100. Below 30 = oversold, above 70 = overbought.
    
    NOTE: To debug, print(closes[-period:]) to see recent prices used.
    """
    if len(closes) < period + 1:
        return 50.0  # neutral if not enough data

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


def get_sol_data() -> tuple:
    """
    Fetch current SOL price and RSI.
    Returns (price: float, rsi: float, recent_high: float)
    
    NOTE: Uses 5-min candles. To switch timeframe change interval=5 below.
    To rediscover candle fields: print(get_ohlc("SOLUSDT", 5)[0])
    Candle format: [time, open, high, low, close, vwap, volume, count]
    """
    # Current price from ticker
    ticker = get_ticker(PAIR)
    if not ticker:
        return None, None, None

    price = float(ticker.get("a", [0])[0])  # ask price

    # OHLC for RSI (5-min candles, last 100)
    candles = get_ohlc(PAIR, interval=5)
    if not candles or len(candles) < RSI_PERIOD + 5:
        return price, 50.0, price

    closes = [float(c[4]) for c in candles]  # index 4 = close price
    highs  = [float(c[2]) for c in candles]  # index 2 = high

    rsi = calculate_rsi(closes, RSI_PERIOD)
    recent_high = max(highs[-20:])  # highest price in last 20 candles (~100 min)

    return price, rsi, recent_high


# ─────────────────────────────────────────────
# TRADE SIZING
# ─────────────────────────────────────────────
def get_trade_size(price: float, dry_run: bool) -> float:
    """
    Calculate how much SOL to buy based on available USD.
    Uses all available USD minus reserve.
    Returns volume in SOL (rounded to 4 decimal places — Kraken minimum).
    """
    if dry_run:
        return round(MIN_TRADE / price, 4)

    balances = get_balance()
    usd = float(balances.get(QUOTE, balances.get("ZUSD", 0)))
    available = max(0, usd - RESERVE_USD)

    if available < MIN_TRADE:
        return 0.0

    volume = available / price
    return round(volume, 4)


# ─────────────────────────────────────────────
# MAIN BOT LOOP
# ─────────────────────────────────────────────
def run(dry_run: bool = False):
    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    print("=" * 55)
    print(f"  SOL Swing Bot — Kraken  {mode}")
    print(f"  Profit target : +{PROFIT_PCT*100:.1f}%")
    print(f"  Stop loss     : -{STOP_PCT*100:.1f}%")
    print(f"  RSI entry     : below {RSI_OVERSOLD} AND dip 10-18%")
    print(f"  Check every   : {CHECK_SECS}s")
    print("=" * 55)
    tg(f"🤖 *SOL Swing Bot started* ({mode})")

    # State
    position = None   # dict with entry_price, volume, entry_time when in a trade
    cycle    = 0

    while True:
        try:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")

            price, rsi, recent_high = get_sol_data()
            if price is None:
                print(f"[{ts}] ⚠️ Could not fetch SOL data, retrying...")
                time.sleep(CHECK_SECS)
                continue

            dip_from_high = (recent_high - price) / recent_high if recent_high else 0

            print(f"\n[{ts}] SOL ${price:.2f} | RSI {rsi:.1f} | High ${recent_high:.2f} | Dip {dip_from_high*100:.1f}%")

            # ── MANAGE OPEN POSITION ──────────────────────
            if position:
                entry    = position["entry_price"]
                volume   = position["volume"]
                pnl_pct  = (price - entry) / entry
                pnl_usd  = (price - entry) * volume

                print(f"  📦 Holding {volume} SOL | Entry ${entry:.2f} | PnL {pnl_pct*100:+.2f}% (${pnl_usd:+.2f})")

                # Take profit
                if pnl_pct >= PROFIT_PCT:
                    print(f"  💰 TARGET HIT +{pnl_pct*100:.2f}% | Selling {volume} SOL @ ${price:.2f}")
                    if not dry_run:
                        ok, result = place_order(PAIR, "sell", "market", volume)
                        if not ok:
                            print(f"  ❌ Sell failed: {result}")
                        else:
                            tg(f"💰 *SOL sold* +{pnl_pct*100:.2f}% (${pnl_usd:+.2f}) @ ${price:.2f}")
                            position = None
                    else:
                        print(f"  [DRY] Would sell {volume} SOL")
                        tg(f"🔵 *DRY SELL* SOL +{pnl_pct*100:.2f}% @ ${price:.2f}")
                        position = None

                # Stop loss
                elif pnl_pct <= -STOP_PCT:
                    print(f"  🛑 STOP LOSS {pnl_pct*100:.2f}% | Selling {volume} SOL @ ${price:.2f}")
                    if not dry_run:
                        ok, result = place_order(PAIR, "sell", "market", volume)
                        if not ok:
                            print(f"  ❌ Stop sell failed: {result}")
                        else:
                            tg(f"🛑 *Stop loss* SOL {pnl_pct*100:.2f}% (${pnl_usd:+.2f}) @ ${price:.2f}")
                            position = None
                    else:
                        print(f"  [DRY] Would stop-sell {volume} SOL")
                        tg(f"🔵 *DRY STOP* SOL {pnl_pct*100:.2f}% @ ${price:.2f}")
                        position = None

                else:
                    print(f"  ⏳ Holding... waiting for +{PROFIT_PCT*100:.1f}% or -{STOP_PCT*100:.1f}%")

            # ── LOOK FOR ENTRY ────────────────────────────
            else:
                rsi_signal = rsi <= RSI_OVERSOLD
                dip_signal = DIP_MIN <= dip_from_high <= DIP_MAX

                # STRICTER ENTRY: Both RSI AND dip must confirm (AND, not OR)
                if rsi_signal and dip_signal:
                    print(f"  🎯 ENTRY signal: RSI {rsi:.1f} + dip -{dip_from_high*100:.1f}%")

                    volume = get_trade_size(price, dry_run)
                    if volume <= 0:
                        print(f"  ⚠️ Not enough USD (need ${MIN_TRADE:.0f}+), skipping")
                    else:
                        cost = volume * price
                        print(f"  🛒 Buying {volume} SOL @ ${price:.2f} (${cost:.2f})")
                        if not dry_run:
                            ok, result = place_order(PAIR, "buy", "market", volume)
                            if ok:
                                position = {
                                    "entry_price": price,
                                    "volume": volume,
                                    "entry_time": ts
                                }
                                tg(f"🛒 *SOL bought* {volume} @ ${price:.2f} | {', '.join(reason)}")
                            else:
                                print(f"  ❌ Buy failed: {result}")
                        else:
                            position = {
                                "entry_price": price,
                                "volume": volume,
                                "entry_time": ts
                            }
                            print(f"  [DRY] Would buy {volume} SOL @ ${price:.2f}")
                            tg(f"🔵 *DRY BUY* SOL {volume} @ ${price:.2f}")
                else:
                    print(f"  💤 No signal (need RSI < {RSI_OVERSOLD} AND dip 10-18%)")

            # P&L summary every 10 cycles
            if cycle % 10 == 0:
                balances = get_balance()
                sol_bal  = float(balances.get(ASSET, 0))
                usd_bal  = float(balances.get(QUOTE, balances.get("ZUSD", 0)))
                total    = usd_bal + (sol_bal * price)
                print(f"\n  📊 Cycle {cycle} | SOL: {sol_bal:.4f} | USD: ${usd_bal:.2f} | Total: ~${total:.2f}")

            time.sleep(CHECK_SECS)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped.")
            tg("🛑 *SOL Swing Bot stopped*")
            break
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Error: {e}")
            time.sleep(CHECK_SECS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Dry run — no real orders placed")
    args = parser.parse_args()
    run(dry_run=args.dry)
