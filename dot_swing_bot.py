#!/usr/bin/env python3
"""
DOT Swing Bot — Kraken
Strategy: Two modes working together:
  1. DOT MODE: If you already hold DOT, treat it as an open position.
               Sell when +8% profit from session start price, buy back on 15-25% dip.
  2. USD MODE: If you have USDT/USD, buy DOT on dips, sell on +8% profit.

Entry zone: RSI below 35 OR 15-25% dip from recent high.
Stop loss: -25% from entry.

Optimized for DOT's moderate volatility and technical patterns.
Good for conservative swing trading with 3-7 day holds.

Usage:
    python3 dot_swing_bot.py          # live trading
    python3 dot_swing_bot.py --dry    # dry run (no real orders)

Requires kraken_connection.py in same folder.
.env needs: KRAKEN_API_KEY, KRAKEN_API_SECRET

NOTE: If API fields change, temporarily add to get_dot_data():
    print(f"Ticker keys: {list(ticker.keys())}")
    print(f"Candle sample: {candles[0]}")
"""

import os
import time
import argparse
from datetime import datetime
from kraken_connection import (
    get_balance, get_ticker, get_ohlc, get_orderbook, place_order, place_oco_order, get_open_orders, cancel_order,
    calculate_order_size
)

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
PAIR           = "DOTUSD"      # DOT trading pair on Kraken
ASSET          = "DOT"         # base asset name in balance
QUOTE          = "USDT"        # quote currency (change to ZUSD if using USD)
PROFIT_PCT     = 0.08          # 8% profit target — optimized for DOT volatility
STOP_PCT       = 0.25          # 25% stop loss — DOT is less volatile than SOL
RSI_PERIOD     = 14            # RSI lookback periods
RSI_OVERSOLD   = 35            # enter when RSI below this (higher than SOL's 40)
DIP_MIN        = 0.15          # only enter when DOT is AT LEAST 15% below recent high
DIP_MAX        = 0.25          # skip entry if drop exceeds 25% (possible crash, not bounce)
MIN_TRADE_USD  = 10.0          # minimum USD value per trade (Kraken minimum ~$10)
MIN_TRADE_DOT  = 1.0           # minimum DOT to sell per trade
RESERVE_USD    = 2.0           # keep this much USD in reserve
RESERVE_DOT    = 1.0           # keep this much DOT in reserve (don't sell everything)
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
# TECHNICAL ANALYSIS
# ─────────────────────────────────────────────
def calculate_rsi(closes: list, period: int = 14) -> float:
    """Calculate RSI indicator."""
    if len(closes) < period + 1:
        return 50.0
    
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-i] - closes[-i - 1]
        if delta >= 0:
            gains.append(delta)
        else:
            losses.append(abs(delta))
    
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_dot_data() -> tuple:
    """
    Fetch current DOT price, RSI, and recent high.
    Returns (price: float, rsi: float, recent_high: float)

    NOTE: Uses 5-min candles. To rediscover fields: print(get_ohlc("DOTUSDT", 5)[0])
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
    recent_high = max(highs[-30:])  # highest in last 30 candles (~2.5 hours)

    return price, rsi, recent_high


# ─────────────────────────────────────────────
# TRADE SIZING
# ─────────────────────────────────────────────
def get_buy_size(price: float, dry_run: bool) -> float:
    """How much DOT to BUY with available USD using dynamic minimums."""
    if dry_run:
        # For dry run, use a reasonable test amount
        order_info = calculate_order_size(PAIR, price, available_usd=100.0)
        return order_info['volume'] if order_info['can_afford'] else 0.0
    
    balances = get_balance()
    available_usd = float(balances.get(QUOTE, balances.get("ZUSD", 0))) - RESERVE_USD
    
    if available_usd <= 0:
        return 0.0
    
    order_info = calculate_order_size(PAIR, price, available_usd=available_usd)
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot afford order')}")
        return 0.0
    
    return order_info['volume']


def get_sell_size(dry_run: bool) -> float:
    """How much DOT to SELL from existing holdings using dynamic minimums."""
    if dry_run:
        # For dry run, simulate selling minimum amount
        min_info = calculate_order_size(PAIR, 100, available_asset=1.0)  # rough price
        return min_info.get('volume', 1.0)
    
    balances = get_balance()
    available_dot = float(balances.get(ASSET, 0)) - RESERVE_DOT
    
    if available_dot <= 0:
        return 0.0
    
    price = float(get_ticker(PAIR).get("c", [0])[0])
    order_info = calculate_order_size(PAIR, price, available_asset=available_dot)
    
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot sell')}")
        return 0.0
    
    # Sell all available above minimum
    return available_dot


# ─────────────────────────────────────────────
# MAIN BOT LOOP
# ─────────────────────────────────────────────
def run(dry_run: bool = False):
    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    print("=" * 55)
    print(f"  DOT Swing Bot — Kraken  {mode}")
    print(f"  Profit target : +{PROFIT_PCT*100:.1f}%")
    print(f"  Stop loss     : -{STOP_PCT*100:.1f}%")
    print(f"  RSI entry     : below {RSI_OVERSOLD}")
    print(f"  Dip entry     : -{DIP_MIN*100:.0f}% to -{DIP_MAX*100:.0f}% from recent high")
    print(f"  Check every   : {CHECK_SECS}s")
    print("=" * 55)
    tg(f"🤖 *DOT Swing Bot started* ({mode})")

    # position tracks an active trade: entry_price, volume, mode ("buy" or "sell")
    position = None
    cycle    = 0

    # On startup, check if we already hold DOT and treat it as an open position
    if not dry_run:
        balances = get_balance()
        dot_balance = float(balances.get(ASSET, 0))
        if dot_balance > RESERVE_DOT:
            price, _, _ = get_dot_data()
            if price:
                entry_price = price
                position = {
                    "mode": "sell",
                    "entry_price": entry_price,
                    "volume": dot_balance - RESERVE_DOT,
                    "entry_time": datetime.now()
                }
                print(f"  📊 Found existing DOT position: {position['volume']:.2f} @ ${entry_price:.4f}")
                tg(f"📊 *DOT position detected*: {position['volume']:.2f} @ ${entry_price:.4f}")

    try:
        while True:
            cycle += 1
            price, rsi, recent_high = get_dot_data()
            
            if not price:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] ⚠️ No DOT data, waiting...")
                time.sleep(CHECK_SECS)
                continue

            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Cycle {cycle}")
            print(f"  💰 DOT: ${price:.4f} | RSI: {rsi:.0f} | High: ${recent_high:.4f}")

            # Entry conditions
            dip_pct = (recent_high - price) / recent_high
            rsi_entry = rsi < RSI_OVERSOLD
            dip_entry = DIP_MIN <= dip_pct <= DIP_MAX

            # Show market status
            if rsi < 30:
                print(f"  📉 Oversold (RSI<30): DOTUSD({rsi:.0f})")
            elif rsi > 70:
                print(f"  📈 Overbought (RSI>70): DOTUSD({rsi:.0f})")
            elif rsi_entry:
                print(f"  📉 Oversold (RSI<{RSI_OVERSOLD}): DOTUSD({rsi:.0f})")

            # Handle existing position
            if position:
                if position["mode"] == "sell":
                    # Selling DOT - wait for dip to buy back
                    if dip_entry:
                        buy_price = price
                        buy_volume = get_buy_size(buy_price, dry_run)
                        
                        if buy_volume > 0:
                            print(f"  🔄 Dip detected: buying back {buy_volume:.2f} DOT @ ${buy_price:.4f}")
                            
                            if not dry_run:
                                order = place_order(
                                    pair=PAIR,
                                    type="buy",
                                    ordertype="market",
                                    volume=buy_volume,
                                    validate=False
                                )
                                
                                if order.get('error'):
                                    print(f"  ❌ Buy order failed: {order['error']}")
                                else:
                                    print(f"  ✅ Buy order placed: {order['descr']['order']}")
                                    tg(f"🔄 *DOT dip buy*: {buy_volume:.2f} @ ${buy_price:.4f}")
                            
                            # Reset position (now holding DOT again)
                            position = None
                        else:
                            print(f"  💸 Insufficient USD to buy back DOT")
                    
                elif position["mode"] == "buy":
                    # Bought DOT - wait for profit target
                    profit_pct = (price - position["entry_price"]) / position["entry_price"]
                    
                    if profit_pct >= PROFIT_PCT:
                        print(f"  🎯 Profit target hit: +{profit_pct*100:.1f}%")
                        print(f"  💰 Selling {position['volume']:.2f} DOT @ ${price:.4f}")
                        
                        if not dry_run:
                            order = place_order(
                                pair=PAIR,
                                type="sell",
                                ordertype="market",
                                volume=position["volume"],
                                validate=False
                            )
                            
                            if order.get('error'):
                                print(f"  ❌ Sell order failed: {order['error']}")
                            else:
                                print(f"  ✅ Sell order placed: {order['descr']['order']}")
                                profit_usd = profit_pct * position["entry_price"] * position["volume"]
                                tg(f"🎯 *DOT profit taken*: +{profit_pct*100:.1f}% (${profit_usd:.2f})")
                        
                        # Reset position
                        position = None
                    
                    elif profit_pct <= -STOP_PCT:
                        print(f"  🛡️ Stop loss hit: {profit_pct*100:.1f}%")
                        print(f"  💰 Selling {position['volume']:.2f} DOT @ ${price:.4f}")
                        
                        if not dry_run:
                            order = place_order(
                                pair=PAIR,
                                type="sell",
                                ordertype="market",
                                volume=position["volume"],
                                validate=False
                            )
                            
                            if order.get('error'):
                                print(f"  ❌ Stop loss order failed: {order['error']}")
                            else:
                                loss_usd = abs(profit_pct) * position["entry_price"] * position["volume"]
                                tg(f"🛡️ *DOT stop loss*: {profit_pct*100:.1f}% (${loss_usd:.2f})")
                        
                        # Reset position
                        position = None
                    
                    else:
                        # Still in position - show progress
                        print(f"  📊 Position: BUY {position['volume']:.2f} DOT @ ${position['entry_price']:.4f} | PnL: {profit_pct*100:+.1f}%")

            # No position - look for entry
            else:
                balances = get_balance()
                usd_balance = float(balances.get(QUOTE, balances.get("ZUSD", 0)))
                dot_balance = float(balances.get(ASSET, 0))
                
                # Priority 1: Sell existing DOT holdings
                if dot_balance > RESERVE_DOT:
                    sell_volume = get_sell_size(dry_run)
                    
                    if sell_volume > 0:
                        print(f"  💰 Selling {sell_volume:.2f} DOT @ ${price:.4f}")
                        
                        if not dry_run:
                            order = place_order(
                                pair=PAIR,
                                type="sell",
                                ordertype="market",
                                volume=sell_volume,
                                validate=False
                            )
                            
                            if order.get('error'):
                                print(f"  ❌ Sell order failed: {order['error']}")
                            else:
                                print(f"  ✅ Sell order placed: {order['descr']['order']}")
                                tg(f"💰 *DOT position opened*: {sell_volume:.2f} @ ${price:.4f}")
                                
                                # Track position for later buy-back
                                position = {
                                    "mode": "sell",
                                    "entry_price": price,
                                    "volume": sell_volume,
                                    "entry_time": datetime.now()
                                }
                    else:
                        print(f"  💸 DOT balance too low to sell (need >{RESERVE_DOT})")
                
                # Priority 2: Buy DOT with USD
                elif usd_balance > MIN_TRADE_USD and (rsi_entry or dip_entry):
                    buy_volume = get_buy_size(price, dry_run)
                    
                    if buy_volume > 0:
                        entry_reason = "RSI oversold" if rsi_entry else f"{dip_pct*100:.0f}% dip"
                        print(f"  📈 Entry signal: {entry_reason}")
                        print(f"  💰 Buying {buy_volume:.2f} DOT @ ${price:.4f}")
                        
                        if not dry_run:
                            order = place_order(
                                pair=PAIR,
                                type="buy",
                                ordertype="market",
                                volume=buy_volume,
                                validate=False
                            )
                            
                            if order.get('error'):
                                print(f"  ❌ Buy order failed: {order['error']}")
                            else:
                                print(f"  ✅ Buy order placed: {order['descr']['order']}")
                                tg(f"📈 *DOT position opened*: {buy_volume:.2f} @ ${price:.4f} ({entry_reason})")
                                
                                # Track position for profit taking
                                position = {
                                    "mode": "buy",
                                    "entry_price": price,
                                    "volume": buy_volume,
                                    "entry_time": datetime.now()
                                }
                    else:
                        if usd_balance <= MIN_TRADE_USD:
                            print(f"  💸 Insufficient USD (need >${MIN_TRADE_USD})")
                        elif not (rsi_entry or dip_entry):
                            print(f"  ⏳ No entry signal (RSI: {rsi:.0f}, Dip: {dip_pct*100:.1f}%)")

            print(f"  ──────────────────────────────────")
            time.sleep(CHECK_SECS)

    except KeyboardInterrupt:
        print(f"\n  🛑 DOT Swing Bot stopped by user")
        tg(f"🛑 *DOT Swing Bot stopped*")
    except Exception as e:
        print(f"\n  ❌ DOT Swing Bot error: {e}")
        tg(f"❌ *DOT Swing Bot error*: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="DOT Swing Bot — Kraken")
    parser.add_argument("--dry", action="store_true", help="Dry run (no real orders)")
    args = parser.parse_args()
    
    run(dry_run=args.dry)


if __name__ == "__main__":
    main()
