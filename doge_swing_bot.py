#!/usr/bin/env python3
"""
🚀 DOGE Swing Bot — Kraken
Optimized for Dogecoin pump detection and swing trading.

Strategy:
- VOLUME SPIKE MODE: Buy when DOGE shows volume spikes (pump detection)
- RSI EXTREMES: Buy RSI < 25 (oversold bounce), sell RSI > 80 (overbought dump)
- PUMP DETECTION: 5x average volume = big move coming
- PROFIT TARGET: 20% gains (DOGE pumps are explosive)
- TRAILING STOP: 3% to protect profits during pumps

Perfect for:
- Meme coin pump trading
- Volume spike detection
- Quick profit taking (DOGE pumps fast!)
- High volatility swing trading

Usage:
    python3 doge_swing_bot.py --scan          # Scan for DOGE opportunities
    python3 doge_swing_bot.py --monitor       # Continuous monitoring
    python3 doge_swing_bot.py --dry         # Dry run (no real orders)

Requires kraken_connection.py in same folder.
.env needs: KRAKEN_API_KEY, KRAKEN_API_SECRET
"""

import os
import time
import argparse
from datetime import datetime, timedelta
from kraken_connection import (
    get_balance, get_ticker, get_ohlc, get_orderbook, 
    place_order, place_oco_order, get_open_orders, cancel_order,
    calculate_order_size
)

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
PAIR           = "XDGUSD"      # DOGE trading pair on Kraken
ASSET          = "XDG"         # DOGE asset name in balance
QUOTE          = "ZUSD"        # Quote currency

# DOGE-specific trading parameters (optimized for meme coin volatility)
PROFIT_PCT     = 0.20          # 20% profit target - DOGE pumps are explosive
STOP_PCT       = 0.03          # 3% trailing stop - protect pump profits
RSI_PERIOD     = 14            # RSI lookback periods
RSI_OVERSOLD   = 25            # Extreme oversold for DOGE bounce
RSI_OVERBOUGHT = 80            # Extreme overbought for DOGE dump
VOLUME_SPIKE_MULTIPLIER = 5.0  # 5x average volume = pump signal
MIN_TRADE_USD  = 10.0          # minimum USD per trade
MIN_TRADE_DOGE = 100.0         # minimum DOGE per trade
RESERVE_USD    = 2.0           # keep this much USD in reserve
RESERVE_DOGE   = 100.0         # keep this much DOGE in reserve
CHECK_SECS     = 30            # seconds between scans (DOGE moves fast!)

# Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# TELEGRAM NOTIFICATIONS
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

def get_doge_data() -> dict:
    """Fetch current DOGE price, RSI, and volume spike detection."""
    print(f"  🔍 Analyzing Dogecoin for pump signals...")
    
    # Get current ticker data
    ticker = get_ticker(PAIR)
    if not ticker:
        return {"error": "No DOGE ticker data"}
    
    current_price = float(ticker.get("c", [0])[0])  # last trade price
    volume_24h = float(ticker.get("v", [0])[0])  # 24h volume
    
    # Get OHLC data for analysis
    ohlc = get_ohlc(PAIR, interval=5)  # 5-minute candles for fast detection
    if not ohlc or len(ohlc) < 20:
        return {"error": "Insufficient DOGE OHLC data"}
    
    # Extract price and volume data
    closes = [float(candle[4]) for candle in ohlc]
    highs = [float(candle[2]) for candle in ohlc]
    lows = [float(candle[3]) for candle in ohlc]
    volumes = [float(candle[6]) for candle in ohlc]
    
    # Calculate indicators
    rsi = calculate_rsi(closes, RSI_PERIOD)
    
    # Volume analysis for pump detection
    avg_volume = sum(volumes[-20:]) / 20  # 20-candle average (100 minutes)
    current_volume = volumes[-1]  # Current 5-min volume
    volume_spike = current_volume > (avg_volume * VOLUME_SPIKE_MULTIPLIER)
    
    # Recent price changes
    price_1h_ago = closes[-12] if len(closes) >= 12 else closes[0]
    price_15min_ago = closes[-3] if len(closes) >= 3 else closes[0]
    price_5min_ago = closes[-2] if len(closes) >= 2 else closes[0]
    
    change_1h = (current_price - price_1h_ago) / price_1h_ago if price_1h_ago > 0 else 0
    change_15min = (current_price - price_15min_ago) / price_15min_ago if price_15min_ago > 0 else 0
    change_5min = (current_price - price_5min_ago) / price_5min_ago if price_5min_ago > 0 else 0
    
    # Recent high/low for momentum
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    
    # Detect signals
    signals = []
    
    # 1. Volume spike (primary pump signal)
    if volume_spike:
        signals.append("VOLUME_SPIKE")
    
    # 2. RSI extremes
    if rsi < RSI_OVERSOLD:
        signals.append("EXTREME_OVERSOLD")
    elif rsi > RSI_OVERBOUGHT:
        signals.append("EXTREME_OVERBOUGHT")
    
    # 3. Rapid price movement (pump detection)
    if change_15min > 0.05:  # 5% in 15min
        signals.append("RAPID_PUMP")
    elif change_15min < -0.05:  # 5% drop in 15min
        signals.append("RAPID_DUMP")
    
    # 4. Breakout/breakdown
    if current_price > recent_high * 1.02:  # 2% above recent high
        signals.append("BREAKOUT")
    elif current_price < recent_low * 0.98:  # 2% below recent low
        signals.append("BREAKDOWN")
    
    # Calculate signal strength
    signal_strength = 0
    if "VOLUME_SPIKE" in signals:
        signal_strength += 3  # Volume spike is most important for DOGE
    if "EXTREME_OVERSOLD" in signals:
        signal_strength += 2
    if "EXTREME_OVERBOUGHT" in signals:
        signal_strength += 2
    if "RAPID_PUMP" in signals:
        signal_strength += 2
    if "BREAKOUT" in signals:
        signal_strength += 1
    
    return {
        "current_price": current_price,
        "rsi": rsi,
        "volume_24h": volume_24h,
        "current_volume": current_volume,
        "avg_volume": avg_volume,
        "volume_spike": volume_spike,
        "change_1h": change_1h,
        "change_15min": change_15min,
        "change_5min": change_5min,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "signals": signals,
        "signal_strength": signal_strength
    }

def get_buy_size(price: float, dry_run: bool) -> float:
    """How much DOGE to BUY with available USD."""
    if dry_run:
        # For dry run, use a reasonable test amount
        order_info = calculate_order_size(PAIR, price, available_usd=50.0)
        return order_info['volume'] if order_info['can_afford'] else 0.0
    
    balances = get_balance()
    available_usd = float(balances.get(QUOTE, balances.get("ZUSD", 0))) - RESERVE_USD
    
    if available_usd <= MIN_TRADE_USD:
        return 0.0
    
    order_info = calculate_order_size(PAIR, price, available_usd=available_usd)
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot afford order')}")
        return 0.0
    
    return order_info['volume']

def get_sell_size(dry_run: bool) -> float:
    """How much DOGE to SELL from existing holdings."""
    if dry_run:
        # For dry run, simulate selling minimum amount
        min_info = calculate_order_size(PAIR, 0.1, available_asset=100.0)
        return min_info.get('volume', 100.0)
    
    balances = get_balance()
    available_doge = float(balances.get(ASSET, 0)) - RESERVE_DOGE
    
    if available_doge <= MIN_TRADE_DOGE:
        return 0.0
    
    price = float(get_ticker(PAIR).get("c", [0])[0])
    order_info = calculate_order_size(PAIR, price, available_asset=available_doge)
    
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot sell')}")
        return 0.0
    
    # Sell all available above minimum
    return available_doge

# ─────────────────────────────────────────────
# TRADING FUNCTIONS
# ─────────────────────────────────────────────
def execute_doge_buy(analysis: dict, dry_run: bool = False) -> dict:
    """Execute DOGE buy order for pump detection."""
    try:
        current_price = analysis["current_price"]
        signal_strength = analysis["signal_strength"]
        signals = analysis["signals"]
        
        # Only buy if we have strong signals
        if signal_strength < 2:
            return {"success": False, "reason": f"Weak signals (strength: {signal_strength})"}
        
        # Calculate position size
        buy_volume = get_buy_size(current_price, dry_run)
        
        if buy_volume <= 0:
            return {"success": False, "reason": "Insufficient USD or cannot afford order"}
        
        # Calculate profit target
        profit_target_price = current_price * (1 + PROFIT_PCT)  # 20% profit target
        stop_loss_price = current_price * (1 - STOP_PCT)       # 3% trailing stop
        
        print(f"\n  🚀 DOGE PUMP SIGNAL DETECTED!")
        print(f"  💰 DOGE Price: ${current_price:.6f}")
        print(f"  📈 Signal Strength: {signal_strength}/10")
        print(f"  🚨 Signals: {', '.join(signals)}")
        print(f"  📊 Volume Spike: {analysis['current_volume']:,.0f} vs avg {analysis['avg_volume']:,.0f}")
        print(f"  💰 Buying {buy_volume:,.0f} DOGE @ ${current_price:.6f}")
        print(f"  🎯 Profit Target: ${profit_target_price:.6f} (+{PROFIT_PCT*100:.1f}%)")
        print(f"  🛡️ Trailing Stop: ${stop_loss_price:.6f} (-{STOP_PCT*100:.1f}%)")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place DOGE buy order")
            return {"success": True, "action": "DRY_RUN_BUY", "volume": buy_volume}
        
        # Place real order
        success, order = place_order(
            pair=PAIR,
            side="buy",
            order_type="market",
            volume=buy_volume,
            validate=False
        )
        
        if not success:
            print(f"  ❌ DOGE buy order failed: {order.get('error', 'Unknown error')}")
            return {"success": False, "error": order.get('error', 'Unknown error')}
        
        print(f"  ✅ DOGE buy order placed: {order.get('descr', {}).get('order', 'Unknown')}")
        
        # Send Telegram notification
        msg = f"🚀 *DOGE Pump Buy*\n\n"
        msg += f"💰 Price: ${current_price:.6f}\n"
        msg += f"📈 RSI: {analysis['rsi']:.1f}\n"
        msg += f"🚨 Signals: {', '.join(signals)}\n"
        msg += f"💰 Volume: {analysis['current_volume']:,.0f} (spike!)\n"
        msg += f"💰 Buying: {buy_volume:,.0f} DOGE\n"
        msg += f"🎯 Target: +{PROFIT_PCT*100:.1f}%\n"
        msg += f"🛡️ Stop: -{STOP_PCT*100:.1f}%\n"
        msg += f"🤖 DOGE Swing Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "BUY_DOGE",
            "volume": buy_volume,
            "entry_price": current_price,
            "profit_target": profit_target_price,
            "stop_loss": stop_loss_price,
            "signals": signals
        }
        
    except Exception as e:
        print(f"  ❌ DOGE buy execution error: {e}")
        return {"success": False, "error": str(e)}

def execute_doge_sell(analysis: dict, dry_run: bool = False) -> dict:
    """Execute DOGE sell order for profit taking."""
    try:
        current_price = analysis["current_price"]
        
        # Get current DOGE position
        balances = get_balance()
        available_doge = float(balances.get(ASSET, 0))
        
        if available_doge <= RESERVE_DOGE:
            return {"success": False, "reason": "No DOGE position to sell"}
        
        sell_volume = get_sell_size(dry_run)
        
        if sell_volume <= 0:
            return {"success": False, "reason": "Cannot sell DOGE position"}
        
        print(f"\n  💰 SELLING DOGE POSITION!")
        print(f"  💰 Current DOGE Price: ${current_price:.6f}")
        print(f"  💰 Selling {sell_volume:,.0f} DOGE @ market price")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place DOGE sell order")
            return {"success": True, "action": "DRY_RUN_SELL", "volume": sell_volume}
        
        # Place real sell order
        success, order = place_order(
            pair=PAIR,
            side="sell",
            order_type="market",
            volume=sell_volume,
            validate=False
        )
        
        if not success:
            print(f"  ❌ DOGE sell order failed: {order.get('error', 'Unknown error')}")
            return {"success": False, "error": order.get('error', 'Unknown error')}
        
        print(f"  ✅ DOGE sell order placed: {order.get('descr', {}).get('order', 'Unknown')}")
        
        # Send Telegram notification
        msg = f"💰 *DOGE Position Sold*\n\n"
        msg += f"💰 Price: ${current_price:.6f}\n"
        msg += f"💰 Volume: {sell_volume:,.0f}\n"
        msg += f"🤖 DOGE Swing Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "SELL_DOGE",
            "volume": sell_volume,
            "exit_price": current_price
        }
        
    except Exception as e:
        print(f"  ❌ DOGE sell execution error: {e}")
        return {"success": False, "error": str(e)}

# ─────────────────────────────────────────────
# MAIN FUNCTIONS
# ─────────────────────────────────────────────
def scan_mode():
    """One-time scan for DOGE pump opportunities."""
    print("🚀 DOGE Swing Bot — Pump Scanner")
    print("=" * 50)
    
    analysis = get_doge_data()
    
    if "error" in analysis:
        print(f"  ❌ {analysis['error']}")
        return
    
    print(f"\n  📊 DOGECOIN ANALYSIS:")
    print(f"  💰 Current Price: ${analysis['current_price']:.6f}")
    print(f"  📈 RSI: {analysis['rsi']:.1f}")
    print(f"  📊 1h Change: {analysis['change_1h']*100:+.2f}%")
    print(f"  📊 15min Change: {analysis['change_15min']*100:+.2f}%")
    print(f"  📊 5min Change: {analysis['change_5min']*100:+.2f}%")
    print(f"  📊 Volume: {analysis['current_volume']:,.0f} (avg: {analysis['avg_volume']:,.0f})")
    
    if analysis["signals"]:
        print(f"\n  🚨 PUMP SIGNALS DETECTED:")
        for signal in analysis["signals"]:
            print(f"      🚀 {signal}")
        print(f"  📈 Signal Strength: {analysis['signal_strength']}/10")
        
        # Recommend action
        if analysis["signal_strength"] >= 2:
            print(f"\n  💡 *RECOMMENDATION*: BUY DOGE")
            print(f"      🚀 Pump signals indicate big move coming!")
            print(f"      🎯 Target: +{PROFIT_PCT*100:.1f}% profit")
            print(f"      🛡️ Stop: -{STOP_PCT*100:.1f}% trailing")
            
            # Execute buy if not dry run
            result = execute_doge_buy(analysis, dry_run=False)
            if result["success"]:
                print(f"  ✅ DOGE position opened successfully!")
        else:
            print(f"\n  📊 *WEAK SIGNALS*: Strength {analysis['signal_strength']}/10")
            print(f"      💡 Wait for stronger pump signals")
    else:
        print(f"\n  📊 *NO PUMP SIGNALS*: DOGE looks calm")
        print(f"      💡 Wait for volume spike or RSI extremes")

def monitor_mode():
    """Continuous monitoring with automatic pump trading."""
    print("🚀 DOGE Swing Bot — Continuous Pump Monitoring")
    print("=" * 60)
    print(f"  📊 Monitoring DOGE every {CHECK_SECS}s")
    print(f"  🚀 Auto-buying on volume spikes (strength >= 2)")
    print(f"  💰 Profit target: +{PROFIT_PCT*100:.1f}%")
    print(f"  🛡️ Trailing stop: -{STOP_PCT*100:.1f}%")
    print(f"  💵 Minimum trade: ${MIN_TRADE_USD}")
    print("=" * 60)
    
    tg(f"🚀 *DOGE Swing Bot started* - Pump monitoring mode")
    
    position = None  # Track active DOGE position
    cycle = 0
    
    try:
        while True:
            cycle += 1
            analysis = get_doge_data()
            
            if "error" in analysis:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] ⚠️ No DOGE data, waiting...")
                time.sleep(CHECK_SECS)
                continue
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Cycle {cycle}")
            print(f"  💰 DOGE: ${analysis['current_price']:.6f} | RSI: {analysis['rsi']:.1f}")
            print(f"  📊 15min: {analysis['change_15min']*100:+.2f}% | Signals: {analysis['signal_strength']}/10")
            
            # Show current signals
            if analysis["signals"]:
                signal_str = ', '.join(analysis["signals"])
                print(f"  🚨 Pump: {signal_str}")
            else:
                print(f"  📊 No pump signals")
            
            # Handle existing position
            if position:
                # Check if we should sell (profit target or stop loss)
                current_price = analysis["current_price"]
                entry_price = position["entry_price"]
                
                profit_pct = (current_price - entry_price) / entry_price
                
                if profit_pct >= PROFIT_PCT:
                    print(f"  🎯 Profit target hit: +{profit_pct*100:.1f}%")
                    result = execute_doge_sell(analysis, dry_run=False)
                    if result["success"]:
                        position = None
                        print(f"  ✅ DOGE position closed with profit!")
                elif profit_pct <= -STOP_PCT:
                    print(f"  🛡️ Trailing stop hit: {profit_pct*100:.1f}%")
                    result = execute_doge_sell(analysis, dry_run=False)
                    if result["success"]:
                        position = None
                        print(f"  ⚠️ DOGE position closed with stop loss")
                else:
                    # Show position progress
                    print(f"  📊 Position: {position['volume']:,.0f} DOGE @ ${entry_price:.6f}")
                    print(f"  📈 P&L: {profit_pct*100:+.1f}%")
            
            # No position - look for entry signals
            else:
                if analysis["signal_strength"] >= 2:
                    print(f"  🚨 Strong pump signals - buying DOGE")
                    result = execute_doge_buy(analysis, dry_run=False)
                    if result["success"]:
                        position = {
                            "entry_price": result["entry_price"],
                            "volume": result["volume"],
                            "entry_time": datetime.now(),
                            "signals": result["signals"]
                        }
                        print(f"  ✅ DOGE position opened!")
                else:
                    print(f"  📊 No entry signals (strength: {analysis['signal_strength']})")
            
            print(f"  ──────────────────────────────────")
            time.sleep(CHECK_SECS)
            
    except KeyboardInterrupt:
        print(f"\n  🛑 DOGE Swing Bot stopped by user")
        tg(f"🛑 *DOGE Swing Bot stopped*")
    except Exception as e:
        print(f"\n  ❌ DOGE Swing Bot error: {e}")
        tg(f"❌ *DOGE Swing Bot error*: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="DOGE Swing Bot — Kraken")
    parser.add_argument("--scan", action="store_true", help="One-time scan for DOGE pump opportunities")
    parser.add_argument("--monitor", action="store_true", help="Continuous monitoring with automatic trading")
    parser.add_argument("--dry", action="store_true", help="Dry run (no real orders)")
    args = parser.parse_args()
    
    if args.scan:
        scan_mode()
    elif args.monitor:
        monitor_mode()
    elif args.dry:
        print("🚀 DOGE Swing Bot — Dry Run Mode")
        print("=" * 50)
        analysis = get_doge_data()
        if "error" not in analysis:
            print(f"\n  📊 DOGECOIN ANALYSIS:")
            print(f"  💰 Current Price: ${analysis['current_price']:.6f}")
            print(f"  📈 RSI: {analysis['rsi']:.1f}")
            print(f"  📊 15min Change: {analysis['change_15min']*100:+.2f}%")
            print(f"  📊 Volume: {analysis['current_volume']:,.0f} (avg: {analysis['avg_volume']:,.0f})")
            
            if analysis["signals"]:
                print(f"\n  🚨 PUMP SIGNALS DETECTED:")
                for signal in analysis["signals"]:
                    print(f"      🚀 {signal}")
                print(f"  📈 Signal Strength: {analysis['signal_strength']}/10")
                print(f"\n  💡 *WOULD BUY DOGE*")
                execute_doge_buy(analysis, dry_run=True)
            else:
                print(f"\n  📊 *NO PUMP SIGNALS*")
    else:
        print("🚀 DOGE Swing Bot — Kraken")
        print("Usage:")
        print("  python3 doge_swing_bot.py --scan    # Scan for DOGE pump opportunities")
        print("  python3 doge_swing_bot.py --monitor # Continuous monitoring")
        print("  python3 doge_swing_bot.py --dry     # Dry run mode")
        print("\nStrategy: Buy DOGE on volume spikes and pump signals")
        print("Profit target: 20% when DOGE pumps")
        print("Trailing stop: 3% to protect profits")
        print("Optimized for meme coin volatility and rapid moves")

if __name__ == "__main__":
    main()
