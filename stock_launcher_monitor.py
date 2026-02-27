#!/usr/bin/env python3
"""
📈 Stock Launcher Monitor — Kraken 🏛️
Monitors stock market conditions and launches swing trading bots when requirements are met.

Features:
- 📊 Monitors AAPL, TSLA for swing trade opportunities
- 🛡️ Day trade protection (max 3 day trades per stock)
- 🚀 Launches bots in new terminals when signals are detected
- ⏱️ Configurable monitoring intervals
- 📱 Telegram notifications for bot launches

Usage:
    python3 stock_launcher_monitor.py          # start monitoring
    python3 stock_launcher_monitor.py --dry    # dry run (no bot launches)

Requirements:
- Stock swing bots must be in same directory
- .env with KRAKEN_API_KEY, KRAKEN_API_SECRET
- Optional: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID for notifications
"""

import os
import time
import argparse
import subprocess
import signal
import sys
from datetime import datetime, date
from kraken_connection import get_ticker, get_ohlc, get_balance, get_orderbook
from kraken_connection import calculate_order_size

# ─────────────────────────────────────────────
# 📈 STOCK CONFIGURATION
# ─────────────────────────────────────────────

# Stocks to monitor (swing trading only)
# NOTE: Kraken may not support all stocks - these are common ones to test
STOCKS = {
    "AAPL": {
        "pair": "AAPLUSD",  # Will test if this pair exists
        "asset": "AAPL",
        "quote": "ZUSD",
        "bot_file": "stock_swing_bot.py",
        "rsi_oversold": 35,
        "dip_min": 0.03,
        "dip_max": 0.08,
        "rsi_overbought": 75,
        "min_usd": 100.0,
        "emoji": "🍎",
        "swing_only": True
    },
    "TSLA": {
        "pair": "TSLAUSD",  # Will test if this pair exists
        "asset": "TSLA",
        "quote": "ZUSD",
        "bot_file": "stock_swing_bot.py",
        "rsi_oversold": 35,
        "dip_min": 0.04,
        "dip_max": 0.10,
        "rsi_overbought": 75,
        "min_usd": 100.0,
        "emoji": "🚗",
        "swing_only": True
    }
}

# 🎛️ Monitoring settings
CHECK_INTERVAL = 60          # seconds between checks
RSI_PERIOD = 14             # RSI lookback
LAUNCH_COOLDOWN = 300       # seconds between same bot launches (5 min)

# 📊 Day trade protection
MAX_DAY_TRADES = 3          # Maximum day trades per stock (FINRA rule)

# 📱 Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# 📈 Track running bots and day trades
running_bots = set()
last_launch_time = {}
day_trades_today = {}  # symbol -> count
last_trade_date = None  # Track date to reset counter

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
# DAY TRADE PROTECTION
# ─────────────────────────────────────────────

def reset_day_trade_counter():
    """Reset day trade counter at start of new day."""
    global last_trade_date, day_trades_today
    today = date.today()
    if last_trade_date != today:
        day_trades_today = {}
        last_trade_date = today
        print(f"  📅 New day - day trade counters reset")

def check_day_trade_limit(symbol: str) -> tuple[bool, int]:
    """Check if we've hit the day trade limit for a stock."""
    reset_day_trade_counter()
    
    trades_today = day_trades_today.get(symbol, 0)
    can_trade = trades_today < MAX_DAY_TRADES
    
    return can_trade, trades_today

def record_day_trade(symbol: str):
    """Record a day trade for a stock."""
    day_trades_today[symbol] = day_trades_today.get(symbol, 0) + 1
    print(f"  📊 Day trade recorded for {symbol}: {day_trades_today[symbol]}/{MAX_DAY_TRADES}")

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
    """Get current market data for a stock."""
    try:
        ticker = get_ticker(config["pair"])
        if not ticker:
            print(f"  ⚠️ {symbol} pair '{config['pair']}' not found on Kraken")
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
        error_msg = str(e)
        if "Unknown asset pair" in error_msg:
            print(f"  ❌ {symbol} pair '{config['pair']}' not available on Kraken")
        else:
            print(f"  ⚠️ Error getting {symbol} data: {e}")
        return None

def analyze_orderbook(symbol: str, config: dict, current_price: float) -> dict:
    """Analyze order book for deep value opportunities."""
    try:
        orderbook = get_orderbook(config["pair"], count=20)
        if not orderbook:
            return {"deep_value": False, "reason": "No orderbook data"}
        
        bids = orderbook.get("bids", [])  # Buy orders [price, volume, timestamp]
        asks = orderbook.get("asks", [])   # Sell orders [price, volume, timestamp]
        
        if not bids or not asks:
            return {"deep_value": False, "reason": "Empty orderbook"}
        
        # Calculate buy wall strength below current price
        buy_wall_strength = 0
        significant_buy_walls = []
        
        for price_str, volume, _ in bids[:10]:  # Top 10 buy orders
            price = float(price_str)
            volume = float(volume)
            
            # Look for buy walls 2-10% below current price
            discount = (current_price - price) / current_price
            if 0.02 <= discount <= 0.10:  # 2-10% below
                buy_wall_strength += volume * price
                if volume * price > 1000:  # Significant wall (> $1000)
                    significant_buy_walls.append({
                        "price": price,
                        "volume": volume,
                        "value_usd": volume * price,
                        "discount": discount * 100
                    })
        
        # Calculate sell wall strength above current price
        sell_wall_strength = 0
        significant_sell_walls = []
        
        for price_str, volume, _ in asks[:10]:  # Top 10 sell orders
            price = float(price_str)
            volume = float(volume)
            
            # Look for sell walls 2-10% above current price
            premium = (price - current_price) / current_price
            if 0.02 <= premium <= 0.10:  # 2-10% above
                sell_wall_strength += volume * price
                if volume * price > 1000:  # Significant wall (> $1000)
                    significant_sell_walls.append({
                        "price": price,
                        "volume": volume,
                        "value_usd": volume * price,
                        "premium": premium * 100
                    })
        
        # Determine deep value opportunities
        deep_value_signals = []
        
        # Strong buy support below
        if buy_wall_strength > 5000:  # > $5000 in buy walls
            deep_value_signals.append(f"Strong buy support ${buy_wall_strength:.0f} below")
        
        # Weak sell resistance above (good for breakout)
        if sell_wall_strength < 2000 and significant_sell_walls:  # < $2000 in sell walls
            deep_value_signals.append(f"Weak sell resistance ${sell_wall_strength:.0f} above")
        
        # Large single wall (whale activity)
        max_buy_wall = max(significant_buy_walls, key=lambda x: x['value_usd']) if significant_buy_walls else None
        if max_buy_wall and max_buy_wall['value_usd'] > 10000:
            deep_value_signals.append(f"Whale buy wall ${max_buy_wall['value_usd']:.0f} at ${max_buy_wall['price']:.0f}")
        
        if deep_value_signals:
            return {
                "deep_value": True,
                "signals": deep_value_signals,
                "buy_walls": significant_buy_walls[:3],  # Top 3
                "sell_walls": significant_sell_walls[:3],  # Top 3
                "buy_strength": buy_wall_strength,
                "sell_strength": sell_wall_strength
            }
        else:
            return {
                "deep_value": False,
                "reason": f"Buy: ${buy_wall_strength:.0f} | Sell: ${sell_wall_strength:.0f}"
            }
            
    except Exception as e:
        print(f"  ⚠️ Orderbook analysis error: {e}")
        return {"deep_value": False, "reason": f"Analysis error: {e}"}

def get_portfolio_value() -> dict:
    """Calculate total portfolio value in USD."""
    try:
        balances = get_balance()
        portfolio_value = 0.0
        holdings = {}
        
        # Get current prices for all stocks
        prices = {}
        for symbol, config in STOCKS.items():
            ticker = get_ticker(config["pair"])
            if ticker:
                prices[symbol] = float(ticker.get("c", [0])[0])
            else:
                prices[symbol] = 0.0
        
        # Calculate USD value of each stock
        for symbol, config in STOCKS.items():
            asset_balance = float(balances.get(config["asset"], 0))
            usd_balance = float(balances.get(config["quote"], 0))
            
            asset_value_usd = asset_balance * prices.get(symbol, 0)
            total_usd_for_asset = usd_balance + asset_value_usd
            
            holdings[symbol] = {
                "usd_balance": usd_balance,
                "asset_balance": asset_balance,
                "asset_value_usd": asset_value_usd,
                "total_usd": total_usd_for_asset,
                "price": prices.get(symbol, 0)
            }
            
            portfolio_value += total_usd_for_asset
        
        return {
            "total_usd": portfolio_value,
            "holdings": holdings
        }
        
    except Exception as e:
        print(f"  ⚠️ Error calculating portfolio value: {e}")
        return {"total_usd": 0.0, "holdings": {}}

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
    """Check if entry conditions are met for starting a swing trade bot."""
    if not market_data:
        return {"signal": False, "reason": "No market data"}
        
    price = market_data["price"]
    rsi = market_data["rsi"]
    dip = market_data["dip_from_high"]
    
    # Buy signals (dip or RSI oversold) - more conservative for stocks
    rsi_signal = rsi <= config["rsi_oversold"]
    dip_signal = config["dip_min"] <= dip <= config["dip_max"]
    
    # Sell signals (overbought or near high)
    rsi_overbought = rsi >= config["rsi_overbought"]
    near_high = dip < 0.02  # Within 2% of high
    
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
        if rsi_overbought: reasons.append(f"RSI {rsi:.1f} 🔥")
        if near_high: reasons.append(f"near high ⚡")
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
            "reason": f"RSI {rsi:.1f} | dip {dip*100:.1f}%",
            "price": price,
            "rsi": rsi,
            "dip": dip
        }

def launch_bot(symbol: str, config: dict, signal_info: dict, dry_run: bool = False) -> bool:
    """Print command to launch a stock trading bot."""
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
        # Check day trade limit for stocks
        can_day_trade, trades_today = check_day_trade_limit(symbol)
        if not can_day_trade:
            print(f"  🚫 Day trade limit reached for {symbol}: {trades_today}/{MAX_DAY_TRADES}")
            return False
        
        # Print the command to run instead of launching terminal
        cmd = f'python "{bot_file}"'
        print(f"\n" + "="*60)
        print(f"📈 {symbol} STOCK BOT LAUNCH COMMAND:")
        print(f"   Signal: {signal_info['type'].upper()} - {signal_info['reason']}")
        print(f"   Price: ${signal_info['price']:.2f}")
        print(f"   RSI: {signal_info['rsi']:.1f}")
        print(f"   Dip: {signal_info['dip']*100:.1f}%")
        print(f"   Day trades: {trades_today}/{MAX_DAY_TRADES}")
        print(f"\n   RUN THIS COMMAND:")
        print(f"   {cmd}")
        print(f"="*60)
        
        # Send notification
        msg = f"📈 *{symbol} Stock Bot Signal Detected*\n"
        msg += f"Signal: {signal_info['type'].upper()}\n"
        msg += f"Reason: {signal_info['reason']}\n"
        msg += f"Price: ${signal_info['price']:.2f}\n"
        msg += f"Day trades: {trades_today}/{MAX_DAY_TRADES}\n"
        msg += f"Command: `{cmd}`"
        tg(msg)
        
        # Record the day trade
        record_day_trade(symbol)
        
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
    print(f"  📈 Stock Launcher Monitor — Kraken  {mode}")
    print(f"  Monitoring: {', '.join(STOCKS.keys())}")
    print(f"  Check interval: {CHECK_INTERVAL}s")
    print(f"  Launch cooldown: {LAUNCH_COOLDOWN}s")
    print(f"  Day trade limit: {MAX_DAY_TRADES} per stock")
    print("=" * 60)
    
    if dry_run:
        print("  🚀 DRY RUN MODE - Will show launches but won't actually start bots")
    
    tg(f"📈 *Stock Monitor started* ({mode})")
    
    cycle = 0
    
    try:
        while True:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] Cycle {cycle} - Checking stock conditions...")
            
            # Get portfolio value once per cycle
            portfolio = get_portfolio_value()
            print(f"\n💼 Portfolio Total: ${portfolio['total_usd']:.2f} USD")
            
            for symbol, config in STOCKS.items():
                print(f"\n📊 {symbol}:")
                
                # Use portfolio data for balance info
                holding = portfolio['holdings'].get(symbol, {})
                usd_balance = holding.get('usd_balance', 0)
                asset_balance = holding.get('asset_balance', 0)
                asset_value_usd = holding.get('asset_value_usd', 0)
                total_for_symbol = holding.get('total_usd', 0)
                
                # Show detailed balance info
                print(f"  💰 Balance: ${usd_balance:.2f} USD + {asset_balance:.6f} {symbol} (${asset_value_usd:.2f}) = ${total_for_symbol:.2f}")
                
                # Check market data
                market_data = get_market_data(symbol, config)
                if not market_data:
                    print(f"  ⚠️ No market data")
                    continue
                
                # Analyze order book for deep value
                orderbook_analysis = analyze_orderbook(symbol, config, market_data['price'])
                
                # Show order book info
                if orderbook_analysis.get('deep_value'):
                    print(f"  🏛️ DEEP VALUE: {', '.join(orderbook_analysis['signals'])}")
                else:
                    print(f"  📊 Orderbook: {orderbook_analysis['reason']}")
                
                # Check balance requirements
                can_trade, balance_info = check_balance_requirements(config)
                
                if not can_trade:
                    print(f"  ⚠️ Insufficient for trading (min: ${config['min_usd']})")
                    continue
                
                # Check entry conditions
                signal_info = check_entry_conditions(symbol, config, market_data)
                
                # Check for deep value signals as additional entry criteria
                deep_value_signal = orderbook_analysis.get('deep_value', False)
                
                if signal_info["signal"] or deep_value_signal:
                    if deep_value_signal and not signal_info["signal"]:
                        print(f"  🏛️ DEEP VALUE SIGNAL: {', '.join(orderbook_analysis['signals'])}")
                        # Create a signal info for deep value
                        signal_info = {
                            "signal": True,
                            "type": "buy",
                            "reason": f"Deep value: {', '.join(orderbook_analysis['signals'])}",
                            "price": market_data['price'],
                            "rsi": market_data['rsi'],
                            "dip": market_data['dip_from_high']
                        }
                    else:
                        print(f"  🎯 SIGNAL: {signal_info['type'].upper()} - {signal_info['reason']}")
                        if deep_value_signal:
                            print(f"  🏛️ + Deep value: {', '.join(orderbook_analysis['signals'])}")
                    
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
        tg("👋 *Stock Monitor stopped*")
    except Exception as e:
        print(f"\n❌ Monitor error: {e}")
        tg(f"❌ *Stock Monitor error*: {e}")

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
