#!/usr/bin/env python3
"""
BTC Swing Bot — Kraken
Strategy: Two modes working together:
  1. BTC MODE: If you already hold BTC, treat it as an open position.
               Sell when +8% profit from session start price, buy back on 5-15% dip.
  2. USD MODE (buy BTC on dips): If you have USD, buy BTC on dips, sell on +8% profit.

Entry zone: RSI below 35 OR 5-15% dip from recent high.
Stop loss: -4% from entry (tight — cut losses fast, 2:1 reward/risk).

Usage:
    python3 btc_swing_bot.py          # live trading
    python3 btc_swing_bot.py --dry    # dry run (no real orders)

Requires kraken_connection.py in same folder.
.env needs: KRAKEN_API_KEY, KRAKEN_API_SECRET

NOTE: If API fields change, temporarily add to get_btc_data():
    print(f"Ticker keys: {list(ticker.keys())}")
    print(f"Candle sample: {candles[0]}")
"""

import os
import time
import argparse
from datetime import datetime
from kraken_connection import (
    get_balance, get_ticker, get_ohlc,
    place_order, place_oco_order, get_open_orders, cancel_order,
    calculate_order_size
)
from position_guardian import place_exit_oco, scan_and_protect

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
PAIR           = "XBTUSD"     # BTC trading pair on Kraken (Kraken uses XBT not BTC)
ASSET          = "XXBT"        # BTC asset name in Kraken balance (Kraken uses XXBT prefix)
QUOTE          = "ZUSD"        # Kraken USD
PROFIT_PCT     = 0.08          # 8% profit target — 2:1 reward/risk vs 4% stop
STOP_PCT       = 0.04          # 4% stop loss — cut losses fast before they compound
RSI_PERIOD     = 14            # RSI lookback periods
RSI_OVERSOLD   = 40            # enter when RSI below this
DIP_MIN        = 0.02          # buy BTC after 2%+ pullback from recent high
DIP_MAX        = 0.15          # skip if drop exceeds 15% (possible breakdown, not bounce)
MIN_TRADE_USD  = 10.0          # minimum USD value per trade (Kraken minimum ~$10)
MIN_TRADE_BTC  = 0.00001       # minimum BTC to sell per trade (BTC is expensive, small fractions)
RESERVE_USD    = 2.0           # keep this much USD in reserve
RESERVE_BTC    = 0.00001       # keep a tiny BTC reserve (don't sell everything)
CHECK_SECS     = 60            # seconds between scans
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")


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


def get_btc_data() -> tuple:
    """
    Fetch current SOL price, RSI, and recent high.
    Returns (price: float, rsi: float, recent_high: float)

    NOTE: Uses 5-min candles. To rediscover fields: print(get_ohlc("XBTUSD", 5)[0])
    Candle format: [time, open, high, low, close, vwap, volume, count]
    """
    ticker = get_ticker(PAIR)
    if not ticker:
        return None, None, None

    price = float(ticker.get("a", [0])[0])  # ask price

    candles = get_ohlc(PAIR, interval=5)
    if not candles or len(candles) < RSI_PERIOD + 5:
        return price, 50.0, price

    closes = [float(c[4]) for c in candles]  # index 4 = close
    highs  = [float(c[2]) for c in candles]  # index 2 = high

    rsi = calculate_rsi(closes, RSI_PERIOD)
    recent_high = max(highs[-20:])  # highest in last 20 candles (~100 min)

    return price, rsi, recent_high


# ─────────────────────────────────────────────
# TRADE SIZING
# ─────────────────────────────────────────────
def get_buy_size(price: float, dry_run: bool) -> float:
    """How much BTC to BUY with available USD using dynamic minimums."""
    if dry_run:
        # For dry run, use a reasonable test amount
        order_info = calculate_order_size(PAIR, price, available_usd=100.0)
        return order_info['volume'] if order_info['can_afford'] else 0.0
    
    balances = get_balance()
    available_usd = float(balances.get(QUOTE, 0)) - RESERVE_USD
    
    if available_usd <= 0:
        return 0.0
    
    order_info = calculate_order_size(PAIR, price, available_usd=available_usd)
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot afford order')}")
        return 0.0
    
    return order_info['volume']


def get_sell_size(dry_run: bool) -> float:
    """How much BTC to SELL from existing holdings using dynamic minimums."""
    if dry_run:
        # For dry run, simulate selling minimum amount
        min_info = calculate_order_size(PAIR, 50000, available_asset=0.001)  # rough price
        return min_info.get('volume', 0.001)
    
    balances = get_balance()
    available_btc = float(balances.get(ASSET, 0)) - RESERVE_BTC
    
    if available_btc <= 0:
        return 0.0
    
    price = float(get_ticker(PAIR).get("c", [0])[0])
    order_info = calculate_order_size(PAIR, price, available_asset=available_btc)
    
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot sell')}")
        return 0.0
    
    # Sell all available above minimum
    return available_btc


# ─────────────────────────────────────────────
# MAIN BOT LOOP
# ─────────────────────────────────────────────
def run(dry_run: bool = False):
    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    print("=" * 55)
    print(f"  BTC Swing Bot — Kraken  {mode}")
    print(f"  Profit target : +{PROFIT_PCT*100:.1f}%")
    print(f"  Stop loss     : -{STOP_PCT*100:.1f}%")
    print(f"  RSI entry     : below {RSI_OVERSOLD}")
    print(f"  Dip entry     : -{DIP_MIN*100:.0f}% to -{DIP_MAX*100:.0f}% from recent high")
    print(f"  Check every   : {CHECK_SECS}s")
    print("=" * 55)
    tg(f"🤖 *BTC Swing Bot started* ({mode})")

    # position tracks an active trade: entry_price, volume, mode ("buy" or "sell")
    position = None
    cycle    = 0

    # On startup, protect any existing BTC holdings with OCO orders
    if not dry_run:
        scan_and_protect(
            {PAIR: {"asset": ASSET, "reserve": RESERVE_BTC}},
            PROFIT_PCT, STOP_PCT
        )
        balances = get_balance()
        btc_held = float(balances.get(ASSET, 0))
        if btc_held > RESERVE_BTC:
            ticker = get_ticker(PAIR)
            start_price = float(ticker.get("a", [0])[0]) if ticker else 0
            if start_price > 0:
                position = {
                    "entry_price": start_price,
                    "volume": round(btc_held - RESERVE_BTC, 4),
                    "entry_time": datetime.now().strftime("%H:%M:%S"),
                    "mode": "sell"   # we're holding SOL, looking to sell it
                }
                print(f"  📦 Found existing BTC: {btc_held} — tracking as open position @ ${start_price:.2f}")
                tg(f"📦 *Existing BTC detected* {btc_held} @ ${start_price:.2f} — watching to sell")

    while True:
        try:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")

            price, rsi, recent_high = get_btc_data()
            if price is None:
                print(f"[{ts}] ⚠️ Could not fetch BTC data, retrying...")
                time.sleep(CHECK_SECS)
                continue

            dip_from_high = (recent_high - price) / recent_high if recent_high else 0

            print(f"\n[{ts}] BTC ${price:.2f} | RSI {rsi:.1f} | High ${recent_high:.2f} | Dip {dip_from_high*100:.1f}%")

            # ── MANAGE OPEN POSITION ──────────────────────
            if position:
                entry    = position["entry_price"]
                volume   = position["volume"]
                pos_mode = position["mode"]
                pnl_pct  = (price - entry) / entry
                pnl_usd  = (price - entry) * volume

                print(f"  📦 [{pos_mode.upper()}] {volume} BTC | Entry ${entry:.2f} | PnL {pnl_pct*100:+.2f}% (${pnl_usd:+.2f})")

                # ── SELL MODE: holding SOL, waiting for price to rise ──
                if pos_mode == "sell":

                    # Take profit: price rose +1% — sell SOL for USDT
                    if pnl_pct >= PROFIT_PCT:
                        print(f"  💰 TARGET HIT +{pnl_pct*100:.2f}% | Selling BTC @ ${price:.2f}")
                        if not dry_run:
                            ok, result = place_order(PAIR, "sell", "market", volume)
                            if not ok:
                                print(f"  ❌ Sell failed: {result}")
                            else:
                                tg(f"💰 *BTC sold* +{pnl_pct*100:.2f}% (${pnl_usd:+.2f}) @ ${price:.2f}")
                                position = None  # now holding USDT, look to buy dip
                        else:
                            print(f"  [DRY] Would sell {volume} BTC")
                            position = None

                    # Stop loss: price dropped -40% — cut and hold USDT
                    elif pnl_pct <= -STOP_PCT:
                        print(f"  🛑 STOP LOSS {pnl_pct*100:.2f}% | Selling BTC @ ${price:.2f}")
                        if not dry_run:
                            ok, result = place_order(PAIR, "sell", "market", volume)
                            if not ok:
                                print(f"  ❌ Stop sell failed: {result}")
                            else:
                                tg(f"🛑 *Stop loss* BTC {pnl_pct*100:.2f}% @ ${price:.2f}")
                                position = None
                        else:
                            print(f"  [DRY] Would stop-sell {volume} SOL")
                            position = None

                    else:
                        print(f"  ⏳ Holding BTC... waiting for +{PROFIT_PCT*100:.1f}% to sell")

                # ── BUY MODE: holding USDT, waiting for price to fall ──
                elif pos_mode == "buy":

                    # Take profit: price dropped to dip zone — already bought, now hold and wait to sell
                    if pnl_pct >= PROFIT_PCT:
                        print(f"  💰 TARGET HIT +{pnl_pct*100:.2f}% | Selling BTC @ ${price:.2f}")
                        if not dry_run:
                            ok, result = place_order(PAIR, "sell", "market", volume)
                            if not ok:
                                print(f"  ❌ Sell failed: {result}")
                            else:
                                tg(f"💰 *BTC sold* +{pnl_pct*100:.2f}% (${pnl_usd:+.2f}) @ ${price:.2f}")
                                position = None
                        else:
                            print(f"  [DRY] Would sell {volume} BTC")
                            position = None

                    elif pnl_pct <= -STOP_PCT:
                        print(f"  🛑 STOP LOSS {pnl_pct*100:.2f}% | Selling BTC @ ${price:.2f}")
                        if not dry_run:
                            ok, result = place_order(PAIR, "sell", "market", volume)
                            if not ok:
                                print(f"  ❌ Stop sell failed: {result}")
                            else:
                                tg(f"🛑 *Stop loss* BTC {pnl_pct*100:.2f}% @ ${price:.2f}")
                                position = None
                        else:
                            print(f"  [DRY] Would stop-sell {volume} SOL")
                            position = None

                    else:
                        print(f"  ⏳ Bought BTC... waiting for +{PROFIT_PCT*100:.1f}% to sell")

            # ── NO POSITION — LOOK FOR ENTRY ─────────────
            else:
                balances    = get_balance() if not dry_run else {}
                btc_bal     = float(balances.get(ASSET, 0)) if not dry_run else 0
                usd_bal     = float(balances.get(QUOTE, balances.get("ZUSD", 0))) if not dry_run else 0
                has_btc     = btc_bal > RESERVE_BTC
                has_usd     = usd_bal > MIN_TRADE_USD + RESERVE_USD

                rsi_signal  = rsi <= RSI_OVERSOLD
                dip_signal  = DIP_MIN <= dip_from_high <= DIP_MAX
                rsi_high    = rsi >= 70   # overbought — good time to sell SOL
                rip_signal  = dip_from_high < 0.05  # price near recent high — sell opportunity

                # SELL signal: SOL is near its high or RSI overbought — sell existing SOL
                if has_btc and (rsi_high or rip_signal):
                    reason = []
                    if rsi_high:   reason.append(f"RSI overbought {rsi:.1f}")
                    if rip_signal: reason.append(f"near high (only {dip_from_high*100:.1f}% below)")
                    reason_str = ", ".join(reason)

                    volume = get_sell_size(dry_run)
                    if volume <= 0:
                        print(f"  ⚠️ Not enough BTC to sell (need >{MIN_TRADE_BTC} above reserve)")
                    else:
                        print(f"  🎯 SELL signal: {reason_str}")
                        print(f"  💸 Selling BTC @ ${price:.2f} (${volume*price:.2f})")
                        if not dry_run:
                            ok, result = place_order(PAIR, "sell", "market", volume)
                            if ok:
                                position = {
                                    "entry_price": price,
                                    "volume": volume,
                                    "entry_time": ts,
                                    "mode": "buy"  # sold SOL, now watching to buy dip
                                }
                                tg(f"💸 *BTC sold* {volume} @ ${price:.2f} | {reason_str} — waiting for dip to rebuy")
                            else:
                                print(f"  ❌ Sell failed: {result}")
                        else:
                            position = {
                                "entry_price": price,
                                "volume": volume,
                                "entry_time": ts,
                                "mode": "buy"
                            }
                            print(f"  [DRY] Would sell {volume} SOL @ ${price:.2f}")

                # BUY signal: dip in 20-30% zone or RSI oversold — buy SOL with USD
                elif has_usd and (rsi_signal or dip_signal):
                    reason = []
                    if rsi_signal: reason.append(f"RSI {rsi:.1f}")
                    if dip_signal: reason.append(f"dip -{dip_from_high*100:.1f}% (20-30% zone)")
                    reason_str = ", ".join(reason)

                    volume = get_buy_size(price, dry_run)
                    if volume <= 0:
                        print(f"  ⚠️ Not enough USD (need ${MIN_TRADE_USD:.0f}+), skipping")
                    else:
                        print(f"  🎯 BUY signal: {reason_str}")
                        print(f"  🛒 Buying BTC @ ${price:.2f} (${volume*price:.2f})")
                        if not dry_run:
                            ok, result = place_order(PAIR, "buy", "market", volume)
                            if ok:
                                position = {
                                    "entry_price": price,
                                    "volume": volume,
                                    "entry_time": ts,
                                    "mode": "sell"  # bought BTC, now watching to sell
                                }
                                place_exit_oco(PAIR, volume, price, PROFIT_PCT, STOP_PCT)
                                tg(f"🛒 *BTC bought* {volume} @ ${price:.2f} | {reason_str}")
                            else:
                                print(f"  ❌ Buy failed: {result}")
                        else:
                            position = {
                                "entry_price": price,
                                "volume": volume,
                                "entry_time": ts,
                                "mode": "sell"
                            }
                            print(f"  [DRY] Would buy {volume} BTC @ ${price:.2f}")

                # SOL held but no sell signal yet — just monitor
                elif has_btc:
                    print(f"  👀 Holding BTC — waiting for sell signal (RSI≥70 or near high)")

                else:
                    print(f"  💤 No signal & no balance to trade with")

            # P&L summary every 10 cycles
            if cycle % 10 == 0:
                balances = get_balance()
                btc_bal  = float(balances.get(ASSET, 0))
                usd_bal  = float(balances.get(QUOTE, balances.get("ZUSD", 0)))
                total    = usd_bal + (btc_bal * price)
                print(f"\n  📊 Cycle {cycle} | SOL: {btc_bal:.4f} | USD: ${usd_bal:.2f} | Total: ~${total:.2f}")

            time.sleep(CHECK_SECS)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped.")
            tg("🛑 *BTC Swing Bot stopped*")
            break
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Error: {e}")
            time.sleep(CHECK_SECS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Dry run — no real orders placed")
    args = parser.parse_args()
    run(dry_run=args.dry)
