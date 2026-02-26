#!/usr/bin/env python3
"""
Correlation Bot — Kraken
Monitors multiple coins for CORRELATED RSI signals and trades them together.

Strategy:
- When 3+ coins hit RSI < 30 within same 5-min window → BUY the dip
- When 3+ coins hit RSI > 70 within same 5-min window → SELL the rally
- Trade the "leader" — coin that moves first predicts the others

Usage:
    python3 correlation_bot.py          # live
    python3 correlation_bot.py --dry    # dry run

"""

import os
import time
from datetime import datetime, timedelta
from collections import defaultdict
import requests as req

# We'll import kraken functions after checking they're available
try:
    from kraken_connection import get_balance, get_ticker, get_ohlc, place_order, calculate_order_size
    KRANKEN_AVAILABLE = True
except:
    KRANKEN_AVAILABLE = False

BASE_URL = "https://api.kraken.com"

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
PAIRS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD",
    "DOGEUSD", "AVAXUSD", "DOTUSD", "LINKUSD", "MATICUSD"
]
RSI_PERIOD = 14
RSI_OVERSOLD = 35  # Slightly higher to find more buys
RSI_OVERBOUGHT = 80  # Just for display, we won't act on it
CORRELATION_THRESHOLD = 3  # Need 3+ coins in same state to trigger
BUY_ONLY_MODE = True  # Only buy, no shorting
CHECK_SECS = 30
PROFIT_PCT = 0.05  # 5%
STOP_PCT = 0.10    # 10%
RESERVE_USD = 5.0  # Keep this much USD in reserve
DRY_BALANCE = 50.0

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
# Track RSI history for correlation detection
rsi_history = defaultdict(list)  # {pair: [(timestamp, rsi), ...]}
position = None  # {pair, entry_price, volume, side, entry_time}


# ─────────────────────────────────────────────
# HELPERS
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


def get_ticker_data(pair: str) -> dict:
    """Get ticker data from Kraken public API."""
    try:
        res = req.get(f"{BASE_URL}/0/public/Ticker?pair={pair}", timeout=10)
        if res.status_code == 200:
            data = res.json().get("result", {})
            if data:
                # First key is the actual pair name
                key = list(data.keys())[0]
                return data[key]
    except:
        pass
    return {}


def get_ohlc_data(pair: str, interval: int = 1) -> list:
    """Get OHLC data from Kraken public API."""
    try:
        res = req.get(f"{BASE_URL}/0/public/OHLC?pair={pair}&interval={interval}", timeout=10)
        if res.status_code == 200:
            data = res.json().get("result", {})
            if data:
                key = list(data.keys())[0]
                return data[key]
    except:
        pass
    return []


def calculate_rsi(closes: list, period: int = 14) -> float:
    """Calculate RSI from close prices."""
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


def get_rsi_for_pair(pair: str) -> float:
    """Get current RSI for a pair."""
    candles = get_ohlc_data(pair, interval=1)
    if not candles or len(candles) < RSI_PERIOD + 5:
        return 50.0
    
    closes = [float(c[4]) for c in candles]
    return calculate_rsi(closes, RSI_PERIOD)


def analyze_correlations() -> dict:
    """
    Analyze all pairs for correlated RSI signals.
    Returns: {oversold_pairs: [...], overbought_pairs: [...], neutral_pairs: [...]}
    """
    now = datetime.now()
    oversold = []
    overbought = []
    neutral = []
    
    for pair in PAIRS:
        rsi = get_rsi_for_pair(pair)
        
        # Track history
        rsi_history[pair].append((now, rsi))
        
        # Keep only last 5 minutes of history
        cutoff = now - timedelta(minutes=5)
        rsi_history[pair] = [
            (ts, r) for ts, r in rsi_history[pair] if ts > cutoff
        ]
        
        if rsi < RSI_OVERSOLD:
            oversold.append((pair, rsi))
        elif rsi > RSI_OVERBOUGHT:
            overbought.append((pair, rsi))
        else:
            neutral.append((pair, rsi))
    
    return {
        "oversold": sorted(oversold, key=lambda x: x[1]),  # lowest RSI first
        "overbought": sorted(overbought, key=lambda x: x[1], reverse=True),  # highest first
        "neutral": neutral
    }


def check_correlation_signal(analysis: dict) -> tuple:
    """
    Check if we have a correlation signal.
    Returns: (signal_type, pairs)
    signal_type: "buy_oversold", "sell_overbought", or None
    """
    # Only act on oversold (BUY) signals - no shorting
    if len(analysis["oversold"]) >= CORRELATION_THRESHOLD:
        # Multiple coins oversold → likely bounce
        return "buy_oversold", analysis["oversold"]
    
    # Skip overbought signals in BUY_ONLY_MODE
    if not BUY_ONLY_MODE and len(analysis["overbought"]) >= CORRELATION_THRESHOLD:
        # Multiple coins overbought → likely dump
        return "sell_overbought", analysis["overbought"]
    
    return None, []


def get_trade_size(pair: str, price: float, dry_run: bool) -> tuple:
    """Calculate trade size using dynamic minimums from Kraken."""
    if not KRANKEN_AVAILABLE:
        return 0, 0
    
    if dry_run:
        # Use max USD for dry run
        available = MAX_TRADE_USD
    else:
        balances = get_balance()
        usd = float(balances.get("ZUSD", 0))
        available = max(0, usd - RESERVE_USD)
    
    if available < 1.0:
        return 0, 0
    
    # Use dynamic order sizing
    order_info = calculate_order_size(pair, price, available)
    
    if not order_info['can_afford']:
        return 0, 0
    
    return order_info['volume'], order_info['cost']


def place_trade(pair: str, side: str, volume: float, cost: float, dry_run: bool) -> bool:
    """Place a trade with both volume and cost."""
    if dry_run:
        print(f"  [DRY] {'BUY' if side == 'buy' else 'SELL'} {volume} {pair} (~${cost:.2f})")
        return True
    
    if not KRANKEN_AVAILABLE:
        return False
    
    ok, result = place_order(pair, side, "market", volume=volume, cost=cost)
    if ok:
        tg(f"📊 *Correlation trade* {side.upper()} {volume} {pair} (~${cost:.2f})")
    return ok


def run(dry_run: bool = False):
    global position
    
    print("=" * 60)
    print("  Correlation Bot — Kraken")
    print(f"  Pairs: {len(PAIRS)} coins")
    print(f"  Signal: {CORRELATION_THRESHOLD}+ coins RSI < {RSI_OVERSOLD} or > {RSI_OVERBOUGHT}")
    print(f"  Target: {PROFIT_PCT*100}% | Stop: {STOP_PCT*100}%")
    print(f"  Mode: {'🔵 DRY RUN' if dry_run else '🟢 LIVE'}")
    print("=" * 60)
    
    if dry_run:
        print(f"  💰 Starting balance: ${DRY_BALANCE:.2f}")
    
    cycle = 0
    
    while True:
        try:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")
            
            # Analyze correlations
            analysis = analyze_correlations()
            signal_type, signal_pairs = check_correlation_signal(analysis)
            
            # Print current state
            oversold_str = ", ".join([f"{p}({r:.0f})" for p, r in analysis["oversold"]]) or "none"
            overbought_str = ", ".join([f"{p}({r:.0f})" for p, r in analysis["overbought"]]) or "none"
            
            print(f"\n[{ts}] Cycle {cycle}")
            print(f"  📉 Oversold (RSI<{RSI_OVERSOLD}): {oversold_str}")
            print(f"  📈 Overbought (RSI>{RSI_OVERBOUGHT}): {overbought_str}")
            
            # Manage existing position
            if position:
                pair = position["pair"]
                entry = position["entry_price"]
                volume = position["volume"]
                side = position["side"]
                
                ticker = get_ticker_data(pair)
                if ticker:
                    # Use bid for selling (what you can actually get)
                    if side == "buy":
                        current_price = float(ticker.get("b", [0])[0])  # bid
                    else:
                        current_price = float(ticker.get("a", [0])[0])  # ask
                    
                    pnl_pct = (current_price - entry) / entry if side == "buy" else (entry - current_price) / entry
                    sell_cost = round(volume * current_price, 2)
                    
                    print(f"  📦 Position: {side.upper()} {volume} {pair} @ ${entry:.4f} | PnL: {pnl_pct*100:+.1f}%")
                    
                    # Take profit
                    if pnl_pct >= PROFIT_PCT:
                        print(f"  💰 TARGET HIT +{pnl_pct*100:.1f}%")
                        place_trade(pair, "sell" if side == "buy" else "buy", volume, sell_cost, dry_run)
                        position = None
                    
                    # Stop loss
                    elif pnl_pct <= -STOP_PCT:
                        print(f"  🛑 STOP LOSS {pnl_pct*100:.1f}%")
                        place_trade(pair, "sell" if side == "buy" else "buy", volume, sell_cost, dry_run)
                        position = None
            
            # Check for new signals
            if not position and signal_type:
                if signal_type == "buy_oversold":
                    # Buy the weakest (lowest RSI)
                    pair, rsi = signal_pairs[0]
                    ticker = get_ticker_data(pair)
                    if ticker:
                        price = float(ticker.get("a", [0])[0])
                        volume, cost = get_trade_size(pair, price, dry_run)
                        
                        if volume > 0:
                            print(f"  🎯 CORRELATION BUY: {pair} RSI={rsi:.0f} @ ${price:.4f} (~${cost:.2f})")
                            ok = place_trade(pair, "buy", volume, cost)
                            if ok or dry_run:
                                position = {
                                    "pair": pair,
                                    "entry_price": price,
                                    "volume": volume,
                                    "side": "buy",
                                    "entry_time": ts
                                }
                                tg(f"🎯 *Correlation BUY* {pair} RSI={rsi:.0f} @ ${price:.4f} (~${cost:.2f})")
                
                elif signal_type == "sell_overbought" and not BUY_ONLY_MODE:
                    # Sell the strongest (highest RSI) - SKIPPED IN BUY_ONLY_MODE
                    print(f"  💤 Sell signal skipped (BUY_ONLY_MODE)")
            
            elif not position:
                print(f"  💤 Waiting for oversold signal ({len(analysis['oversold'])}/{CORRELATION_THRESHOLD} coins)")
            
            time.sleep(CHECK_SECS)
            
        except KeyboardInterrupt:
            print("\n👋 Stopped")
            break
        except Exception as e:
            print(f"  ⚠️ Error: {e}")
            time.sleep(CHECK_SECS)


if __name__ == "__main__":
    import sys
    dry = "--dry" in sys.argv
    run(dry_run=dry)
