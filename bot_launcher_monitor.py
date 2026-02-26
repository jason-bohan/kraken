#!/usr/bin/env python3
"""
Bot Launcher Monitor — Kraken
Monitors market conditions and automatically launches swing bots when requirements are met.

Features:
- Monitors BTC, ETH, SOL for entry conditions
- Launches bots in new terminals when signals are detected
- Prevents duplicate bot instances
- Configurable monitoring intervals
- Telegram notifications for bot launches

Usage:
    python3 bot_launcher_monitor.py          # start monitoring
    python3 bot_launcher_monitor.py --dry    # dry run (no bot launches)

Requirements:
- All swing bots must be in same directory
- .env with KRAKEN_API_KEY, KRAKEN_API_SECRET
- Optional: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID for notifications
"""

import os
import time
import argparse
import subprocess
import signal
import sys
from datetime import datetime
from kraken_connection import get_ticker, get_ohlc, get_balance
from kraken_connection import calculate_order_size

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

# Assets to monitor
ASSETS = {
    "BTC": {
        "pair": "XBTUSD",
        "asset": "XXBT", 
        "quote": "ZUSD",
        "bot_file": "btc_swing_bot.py",
        "rsi_oversold": 40,
        "dip_min": 0.15,
        "dip_max": 0.25,
        "rsi_overbought": 70,
        "min_usd": 50.0  # minimum USD to start trading
    },
    "ETH": {
        "pair": "ETHUSD",
        "asset": "XETH",
        "quote": "ZUSD", 
        "bot_file": "eth_swing_bot.py",
        "rsi_oversold": 40,
        "dip_min": 0.15,
        "dip_max": 0.25,
        "rsi_overbought": 70,
        "min_usd": 30.0
    },
    "SOL": {
        "pair": "SOLUSD",
        "asset": "SOL",
        "quote": "USDT",
        "bot_file": "sol_swing_bot.py", 
        "rsi_oversold": 40,
        "dip_min": 0.20,
        "dip_max": 0.30,
        "rsi_overbought": 70,
        "min_usd": 20.0
    }
}

# Monitoring settings
CHECK_INTERVAL = 60          # seconds between checks
RSI_PERIOD = 14             # RSI lookback
LAUNCH_COOLDOWN = 300       # seconds between same bot launches (5 min)

# Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# Track running bots and last launch times
running_bots = set()
last_launch_time = {}

# ─────────────────────────────────────────────
# TELEGRAM NOTIFICATIONS
# ─────────────────────────────────────────────

def tg(msg: str):
    """Send Telegram notification if configured."""
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
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────

def calculate_rsi(closes: list, period: int = 14) -> float:
    """Calculate RSI from closing prices."""
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

def get_market_data(symbol: str, config: dict) -> dict:
    """Get current market data for a symbol."""
    try:
        ticker = get_ticker(config["pair"])
        if not ticker:
            return None
            
        price = float(ticker.get("c", [0])[0])  # last trade price
        
        candles = get_ohlc(config["pair"], interval=5)
        if not candles or len(candles) < RSI_PERIOD + 5:
            return {"price": price, "rsi": 50.0, "recent_high": price}
            
        closes = [float(c[4]) for c in candles]  # close prices
        highs = [float(c[2]) for c in candles]   # high prices
        
        rsi = calculate_rsi(closes, RSI_PERIOD)
        recent_high = max(highs[-20:])  # highest in last 20 candles
        
        return {
            "price": price,
            "rsi": rsi, 
            "recent_high": recent_high,
            "dip_from_high": (recent_high - price) / recent_high if recent_high else 0
        }
        
    except Exception as e:
        print(f"  ⚠️ Error getting {symbol} data: {e}")
        return None

def check_balance_requirements(config: dict) -> tuple[bool, dict]:
    """Check if we have sufficient balance to trade."""
    try:
        balances = get_balance()
        usd_balance = float(balances.get(config["quote"], 0))
        asset_balance = float(balances.get(config["asset"], 0))
        
        # Check minimum USD requirement
        has_usd = usd_balance >= config["min_usd"]
        
        # Check if we can afford minimum order
        if has_usd:
            order_info = calculate_order_size(
                config["pair"], 
                100,  # rough price estimate
                available_usd=usd_balance
            )
            can_afford_min = order_info['can_afford']
        else:
            can_afford_min = False
            
        return has_usd or asset_balance > 0, {
            "usd_balance": usd_balance,
            "asset_balance": asset_balance,
            "has_usd": has_usd,
            "can_afford_min": can_afford_min
        }
        
    except Exception as e:
        print(f"  ⚠️ Error checking balances: {e}")
        return False, {}

def check_entry_conditions(symbol: str, config: dict, market_data: dict) -> dict:
    """Check if entry conditions are met for starting a bot."""
    if not market_data:
        return {"signal": False, "reason": "No market data"}
        
    price = market_data["price"]
    rsi = market_data["rsi"]
    dip = market_data["dip_from_high"]
    
    # Buy signals (dip or RSI oversold)
    rsi_signal = rsi <= config["rsi_oversold"]
    dip_signal = config["dip_min"] <= dip <= config["dip_max"]
    
    # Sell signals (overbought or near high)
    rsi_overbought = rsi >= config["rsi_overbought"]
    near_high = dip < 0.05
    
    buy_signal = rsi_signal or dip_signal
    sell_signal = rsi_overbought or near_high
    
    if buy_signal:
        reasons = []
        if rsi_signal: reasons.append(f"RSI {rsi:.1f}")
        if dip_signal: reasons.append(f"dip -{dip*100:.1f}%")
        return {
            "signal": True,
            "type": "buy",
            "reason": ", ".join(reasons),
            "price": price,
            "rsi": rsi,
            "dip": dip
        }
    elif sell_signal:
        reasons = []
        if rsi_overbought: reasons.append(f"RSI {rsi:.1f}")
        if near_high: reasons.append(f"near high")
        return {
            "signal": True,
            "type": "sell", 
            "reason": ", ".join(reasons),
            "price": price,
            "rsi": rsi,
            "dip": dip
        }
    else:
        return {
            "signal": False,
            "reason": f"RSI {rsi:.1f}, dip {dip*100:.1f}%",
            "price": price,
            "rsi": rsi,
            "dip": dip
        }

def launch_bot(symbol: str, config: dict, signal_info: dict, dry_run: bool = False) -> bool:
    """Print command to launch a trading bot."""
    # Get absolute path to bot file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bot_file = os.path.join(script_dir, config["bot_file"])
    
    # Check cooldown
    now = time.time()
    if symbol in last_launch_time and (now - last_launch_time[symbol]) < LAUNCH_COOLDOWN:
        print(f"  ⏰ {symbol} bot launched recently, skipping (cooldown)")
        return False
    
    if dry_run:
        print(f"  🚀 [DRY] Would launch {symbol} bot: {signal_info['type'].upper()} signal - {signal_info['reason']}")
        return True
    
    try:
        # Print the command to run instead of launching terminal
        cmd = f'python "{bot_file}"'
        print(f"\n" + "="*60)
        print(f"🚀 {symbol} BOT LAUNCH COMMAND:")
        print(f"   Signal: {signal_info['type'].upper()} - {signal_info['reason']}")
        print(f"   Price: ${signal_info['price']:.2f}")
        print(f"   RSI: {signal_info['rsi']:.1f}")
        print(f"   Dip: {signal_info['dip']*100:.1f}%")
        print(f"\n   RUN THIS COMMAND:")
        print(f"   {cmd}")
        print(f"="*60)
        
        # Send notification
        msg = f"🤖 *{symbol} Bot Signal Detected*\n"
        msg += f"Signal: {signal_info['type'].upper()}\n"
        msg += f"Reason: {signal_info['reason']}\n"
        msg += f"Price: ${signal_info['price']:.2f}\n"
        msg += f"Command: `{cmd}`"
        tg(msg)
        
        # Update tracking
        running_bots.add(symbol)
        last_launch_time[symbol] = now
        
        return True
        
    except Exception as e:
        print(f"  ❌ Failed to prepare {symbol} bot command: {e}")
        return False

# ─────────────────────────────────────────────
# MAIN MONITOR LOOP
# ─────────────────────────────────────────────

def monitor(dry_run: bool = False):
    """Main monitoring loop."""
    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    print("=" * 60)
    print(f"  Bot Launcher Monitor — Kraken  {mode}")
    print(f"  Monitoring: {', '.join(ASSETS.keys())}")
    print(f"  Check interval: {CHECK_INTERVAL}s")
    print(f"  Launch cooldown: {LAUNCH_COOLDOWN}s")
    print("=" * 60)
    
    if dry_run:
        print("  🚀 DRY RUN MODE - Will show launches but won't actually start bots")
    
    tg(f"🔍 *Bot Monitor started* ({mode})")
    
    cycle = 0
    
    try:
        while True:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] Cycle {cycle} - Checking conditions...")
            
            for symbol, config in ASSETS.items():
                print(f"\n📊 {symbol}:")
                
                # Check market data
                market_data = get_market_data(symbol, config)
                if not market_data:
                    print(f"  ⚠️ No market data")
                    continue
                
                # Check balance requirements
                can_trade, balance_info = check_balance_requirements(config)
                if not can_trade:
                    print(f"  💰 Insufficient balance: ${balance_info.get('usd_balance', 0):.2f} USD, {balance_info.get('asset_balance', 0):.6f} {symbol}")
                    continue
                
                # Check entry conditions
                signal_info = check_entry_conditions(symbol, config, market_data)
                
                if signal_info["signal"]:
                    print(f"  🎯 SIGNAL: {signal_info['type'].upper()} - {signal_info['reason']}")
                    print(f"  💰 Price: ${signal_info['price']:.2f} | RSI: {signal_info['rsi']:.1f} | Dip: {signal_info['dip']*100:.1f}%")
                    
                    # Launch bot if conditions met
                    if symbol not in running_bots:
                        launch_bot(symbol, config, signal_info, dry_run)
                    else:
                        print(f"  ⚠️ {symbol} bot already running")
                else:
                    print(f"  😴 No signal: {signal_info['reason']}")
                    print(f"  💰 Price: ${signal_info['price']:.2f} | RSI: {signal_info['rsi']:.1f} | Dip: {signal_info['dip']*100:.1f}%")
            
            # Wait for next check
            print(f"\n⏳ Waiting {CHECK_INTERVAL}s...")
            time.sleep(CHECK_INTERVAL)
            
    except KeyboardInterrupt:
        print(f"\n\n👋 Monitor stopped by user")
        tg("👋 *Bot Monitor stopped*")
    except Exception as e:
        print(f"\n❌ Monitor error: {e}")
        tg(f"❌ *Bot Monitor error*: {e}")

# ─────────────────────────────────────────────
# SIGNAL HANDLING
# ─────────────────────────────────────────────

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    print(f"\n👋 Shutting down monitor...")
    sys.exit(0)

# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Dry run — monitor but don't print launch commands")
    args = parser.parse_args()
    
    # Start monitoring
    monitor(dry_run=args.dry)
