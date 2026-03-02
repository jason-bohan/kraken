#!/usr/bin/env python3
"""
🚀 BTC Momentum Bot — Kraken
Buys Bitcoin when it starts trending upward and manages long positions.

Strategy:
- MOMENTUM DETECTION: Buy BTC when it breaks resistance and trends up
- TECHNICAL SIGNALS: RSI breakouts, golden crosses, volume spikes
- PROFIT TARGET: 10% gains on BTC long positions
- STOP LOSS: 5% to protect against reversals
- TREND FOLLOWING: Buy strength, sell weakness

Perfect for:
- Capturing Bitcoin bull runs
- Momentum-based trading
- Trend following strategies
- Long Bitcoin exposure

Usage:
    python3 btc_momentum_bot.py --scan          # Scan for BTC momentum opportunities
    python3 btc_momentum_bot.py --monitor       # Continuous monitoring
    python3 btc_momentum_bot.py --dry         # Dry run (no real orders)

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
BTC_PAIR       = "XBTUSD"      # Bitcoin trading pair
ASSET          = "XBT"         # Bitcoin asset name in balance
QUOTE          = "ZUSD"        # Quote currency

# Momentum trading parameters
PROFIT_PCT     = 0.10          # 10% profit target on BTC
STOP_PCT       = 0.05          # 5% stop loss (tight for momentum)
RSI_PERIOD     = 14            # RSI lookback periods
RSI_BREAKOUT   = 50            # RSI breaks above 50 = bullish momentum
VOLUME_SPIKE    = 2.0           # 2x average volume = momentum
BREAKOUT_PCT   = 0.02           # 2% above recent high = breakout
MIN_TRADE_USD  = 25.0          # minimum USD per trade
RESERVE_USD    = 5.0           # keep this much USD in reserve
CHECK_SECS     = 60            # seconds between scans

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

def get_btc_momentum_analysis() -> dict:
    """Analyze Bitcoin for momentum signals."""
    print(f"  🚀 Analyzing Bitcoin for upward momentum...")
    
    # Get current ticker data
    ticker = get_ticker(BTC_PAIR)
    if not ticker:
        return {"error": "No BTC ticker data"}
    
    current_price = float(ticker.get("c", [0])[0])  # last trade price
    volume_24h = float(ticker.get("v", [0])[0])  # 24h volume
    
    # Get OHLC data for analysis
    ohlc = get_ohlc(BTC_PAIR, interval=15)  # 15-minute candles
    if not ohlc or len(ohlc) < 50:
        return {"error": "Insufficient BTC OHLC data"}
    
    # Extract price and volume data
    closes = [float(candle[4]) for candle in ohlc]
    highs = [float(candle[2]) for candle in ohlc]
    lows = [float(candle[3]) for candle in ohlc]
    volumes = [float(candle[6]) for candle in ohlc]
    
    # Calculate indicators
    rsi = calculate_rsi(closes, RSI_PERIOD)
    
    # Moving averages for trend analysis
    ma_short = sum(closes[-10:]) / 10
    ma_long = sum(closes[-20:]) / 20
    
    # Recent high/low for breakout analysis
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    
    # Volume analysis
    avg_volume = sum(volumes[-20:]) / 20
    current_volume = volumes[-1]
    volume_spike = current_volume > (avg_volume * VOLUME_SPIKE)
    
    # Calculate trend
    trend_direction = "upward" if ma_short > ma_long else "downward" if ma_short < ma_long else "sideways"
    
    # Momentum signal detection
    momentum_signals = []
    
    # 1. RSI breakout (momentum starting)
    if rsi > RSI_BREAKOUT and rsi < 70:  # Above 50 but not overbought
        momentum_signals.append("RSI_MOMENTUM")
    
    # 2. Golden cross (trend changing)
    if ma_short > ma_long and trend_direction == "upward":
        momentum_signals.append("GOLDEN_CROSS")
    
    # 3. Price breakout (breaking resistance)
    breakout = (current_price - recent_high) / recent_high > BREAKOUT_PCT
    if breakout:
        momentum_signals.append("PRICE_BREAKOUT")
    
    # 4. Volume spike (confirming momentum)
    if volume_spike:
        momentum_signals.append("VOLUME_SPIKE")
    
    # 5. Multiple timeframe confirmation
    # Check 1-hour trend
    ohlc_1h = get_ohlc(BTC_PAIR, interval=60)
    if ohlc_1h and len(ohlc_1h) >= 20:
        closes_1h = [float(candle[4]) for candle in ohlc_1h]
        ma_short_1h = sum(closes_1h[-10:]) / 10
        ma_long_1h = sum(closes_1h[-20:]) / 20
        if ma_short_1h > ma_long_1h:
            momentum_signals.append("MULTI_TIMEFRAME")
    
    # Calculate momentum strength
    momentum_strength = len(momentum_signals)
    
    return {
        "current_price": current_price,
        "rsi": rsi,
        "volume_24h": volume_24h,
        "current_volume": current_volume,
        "avg_volume": avg_volume,
        "volume_spike": volume_spike,
        "ma_short": ma_short,
        "ma_long": ma_long,
        "trend": trend_direction,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "momentum_signals": momentum_signals,
        "momentum_strength": momentum_strength
    }

def get_btc_position_size(price: float, dry_run: bool) -> float:
    """Calculate BTC position size."""
    if dry_run:
        # For dry run, use a reasonable test amount
        order_info = calculate_order_size(BTC_PAIR, price, available_usd=100.0)
        return order_info['volume'] if order_info['can_afford'] else 0.0
    
    balances = get_balance()
    available_usd = float(balances.get(QUOTE, 0)) - RESERVE_USD
    
    if available_usd <= MIN_TRADE_USD:
        return 0.0
    
    order_info = calculate_order_size(BTC_PAIR, price, available_usd=available_usd)
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot afford order')}")
        return 0.0
    
    return order_info['volume']

# ─────────────────────────────────────────────
# TRADING FUNCTIONS
# ─────────────────────────────────────────────
def execute_btc_buy(analysis: dict, dry_run: bool = False) -> dict:
    """Execute BTC buy order for momentum trading."""
    try:
        current_price = analysis["current_price"]
        signal_strength = analysis["momentum_strength"]
        signals = analysis["momentum_signals"]
        
        # Only buy if we have strong momentum signals
        if signal_strength < 2:
            return {"success": False, "reason": f"Weak momentum signals (strength: {signal_strength})"}
        
        # Calculate position size
        buy_volume = get_btc_position_size(current_price, dry_run)
        
        if buy_volume <= 0:
            return {"success": False, "reason": "Insufficient USD or cannot afford order"}
        
        # Calculate profit and stop levels
        profit_target_price = current_price * (1 + PROFIT_PCT)  # 10% profit target
        stop_loss_price = current_price * (1 - STOP_PCT)      # 5% stop loss
        
        print(f"\n  🚀 BTC MOMENTUM SIGNAL DETECTED!")
        print(f"  💰 BTC Price: ${current_price:,.2f}")
        print(f"  📈 Momentum Strength: {signal_strength}/5")
        print(f"  🚨 Signals: {', '.join(signals)}")
        print(f"  📊 RSI: {analysis['rsi']:.1f}")
        print(f"  📊 Trend: {analysis['trend']}")
        print(f"  📊 Volume Spike: {'Yes' if analysis['volume_spike'] else 'No'}")
        print(f"  💰 Buying {buy_volume:.6f} BTC @ ${current_price:,.2f}")
        print(f"  🎯 Profit Target: ${profit_target_price:,.2f} (+{PROFIT_PCT*100:.1f}%)")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:,.2f} (-{STOP_PCT*100:.1f}%)")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place BTC buy order")
            return {"success": True, "action": "DRY_RUN_BUY", "volume": buy_volume}
        
        # Place real order
        order = place_order(
            pair=BTC_PAIR,
            type="buy",
            ordertype="market",
            volume=buy_volume,
            validate=False
        )
        
        if order.get('error'):
            print(f"  ❌ BTC buy order failed: {order['error']}")
            return {"success": False, "error": order['error']}
        
        print(f"  ✅ BTC buy order placed: {order['descr']['order']}")
        
        # Send Telegram notification
        msg = f"🚀 *BTC Momentum Buy*\n\n"
        msg += f"💰 Price: ${current_price:,.2f}\n"
        msg += f"📈 RSI: {analysis['rsi']:.1f}\n"
        msg += f"🚨 Signals: {', '.join(signals)}\n"
        msg += f"📊 Volume Spike: {'Yes' if analysis['volume_spike'] else 'No'}\n"
        msg += f"💰 Buying: {buy_volume:.6f} BTC\n"
        msg += f"🎯 Target: +{PROFIT_PCT*100:.1f}%\n"
        msg += f"🛡️ Stop: -{STOP_PCT*100:.1f}%\n"
        msg += f"🤖 BTC Momentum Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "BUY_BTC",
            "volume": buy_volume,
            "entry_price": current_price,
            "profit_target": profit_target_price,
            "stop_loss": stop_loss_price,
            "signals": signals
        }
        
    except Exception as e:
        print(f"  ❌ BTC buy execution error: {e}")
        return {"success": False, "error": str(e)}

def execute_btc_sell(analysis: dict, dry_run: bool = False) -> dict:
    """Execute BTC sell order for profit taking."""
    try:
        current_price = analysis["current_price"]
        
        # Get current BTC position
        balances = get_balance()
        available_btc = float(balances.get(ASSET, 0))
        
        if available_btc <= 0:
            return {"success": False, "reason": "No BTC position to sell"}
        
        # Sell all available BTC
        sell_volume = available_btc
        
        print(f"\n  💰 SELLING BTC POSITION!")
        print(f"  💰 Current BTC Price: ${current_price:,.2f}")
        print(f"  💰 Selling {sell_volume:.6f} BTC @ market price")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place BTC sell order")
            return {"success": True, "action": "DRY_RUN_SELL", "volume": sell_volume}
        
        # Place real sell order
        order = place_order(
            pair=BTC_PAIR,
            type="sell",
            ordertype="market",
            volume=sell_volume,
            validate=False
        )
        
        if order.get('error'):
            print(f"  ❌ BTC sell order failed: {order['error']}")
            return {"success": False, "error": order['error']}
        
        print(f"  ✅ BTC sell order placed: {order['descr']['order']}")
        
        # Send Telegram notification
        msg = f"💰 *BTC Position Sold*\n\n"
        msg += f"💰 Price: ${current_price:,.2f}\n"
        msg += f"💰 Volume: {sell_volume:.6f}\n"
        msg += f"🤖 BTC Momentum Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "SELL_BTC",
            "volume": sell_volume,
            "exit_price": current_price
        }
        
    except Exception as e:
        print(f"  ❌ BTC sell execution error: {e}")
        return {"success": False, "error": str(e)}

# ─────────────────────────────────────────────
# MAIN FUNCTIONS
# ─────────────────────────────────────────────
def scan_mode():
    """One-time scan for BTC momentum opportunities."""
    print("🚀 BTC Momentum Bot — One-Time Scan")
    print("=" * 50)
    
    analysis = get_btc_momentum_analysis()
    
    if "error" in analysis:
        print(f"  ❌ {analysis['error']}")
        return
    
    print(f"\n  📊 BITCOIN MOMENTUM ANALYSIS:")
    print(f"  💰 Current Price: ${analysis['current_price']:,.2f}")
    print(f"  📈 RSI: {analysis['rsi']:.1f}")
    print(f"  📊 Trend: {analysis['trend']}")
    print(f"  📊 Volume: {analysis['current_volume']:,.0f} (avg: {analysis['avg_volume']:,.0f})")
    
    if analysis["momentum_signals"]:
        print(f"\n  🚀 MOMENTUM SIGNALS DETECTED:")
        for signal in analysis["momentum_signals"]:
            print(f"      🚀 {signal}")
        print(f"  📈 Momentum Strength: {analysis['momentum_strength']}/5")
        
        # Recommend action
        if analysis["momentum_strength"] >= 2:
            print(f"\n  💡 *RECOMMENDATION*: BUY BTC")
            print(f"      🚀 Momentum signals indicate upward trend!")
            print(f"      🎯 Target: +{PROFIT_PCT*100:.1f}% profit")
            print(f"      🛡️ Stop: -{STOP_PCT*100:.1f}% if momentum fails")
            
            # Execute buy if not dry run
            result = execute_btc_buy(analysis, dry_run=False)
            if result["success"]:
                print(f"  ✅ BTC position opened successfully!")
        else:
            print(f"\n  📊 *WEAK MOMENTUM*: Strength {analysis['momentum_strength']}/5")
            print(f"      💡 Wait for stronger momentum signals")
    else:
        print(f"\n  📊 *NO MOMENTUM SIGNALS*: BTC looks calm")
        print(f"      💡 Wait for RSI breakout or golden cross")

def monitor_mode():
    """Continuous monitoring with automatic momentum trading."""
    print("🚀 BTC Momentum Bot — Continuous Monitoring")
    print("=" * 60)
    print(f"  📊 Monitoring BTC every {CHECK_SECS}s")
    print(f"  🚀 Auto-buying on momentum signals (strength >= 2)")
    print(f"  💰 Profit target: +{PROFIT_PCT*100:.1f}%")
    print(f"  🛡️ Stop loss: -{STOP_PCT*100:.1f}%")
    print(f"  💵 Minimum trade: ${MIN_TRADE_USD}")
    print("=" * 60)
    
    tg(f"🚀 *BTC Momentum Bot started* - Monitoring for upward momentum")
    
    position = None  # Track active BTC position
    cycle = 0
    
    try:
        while True:
            cycle += 1
            analysis = get_btc_momentum_analysis()
            
            if "error" in analysis:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] ⚠️ No BTC data, waiting...")
                time.sleep(CHECK_SECS)
                continue
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Cycle {cycle}")
            print(f"  💰 BTC: ${analysis['current_price']:,.2f} | RSI: {analysis['rsi']:.1f}")
            print(f"  📊 Trend: {analysis['trend']} | Momentum: {analysis['momentum_strength']}/5")
            
            # Show current signals
            if analysis["momentum_signals"]:
                signal_str = ', '.join(analysis["momentum_signals"])
                print(f"  🚀 Momentum: {signal_str}")
            else:
                print(f"  📊 No momentum signals")
            
            # Handle existing position
            if position:
                # Check if we should sell (profit target or stop loss)
                current_price = analysis["current_price"]
                entry_price = position["entry_price"]
                
                profit_pct = (current_price - entry_price) / entry_price
                
                if profit_pct >= PROFIT_PCT:
                    print(f"  🎯 Profit target hit: +{profit_pct*100:.1f}%")
                    result = execute_btc_sell(analysis, dry_run=False)
                    if result["success"]:
                        position = None
                        print(f"  ✅ BTC position closed with profit!")
                elif profit_pct <= -STOP_PCT:
                    print(f"  🛡️ Stop loss hit: {profit_pct*100:.1f}%")
                    result = execute_btc_sell(analysis, dry_run=False)
                    if result["success"]:
                        position = None
                        print(f"  ⚠️ BTC position closed with stop loss")
                else:
                    # Show position progress
                    print(f"  📊 Position: {position['volume']:.6f} BTC @ ${entry_price:,.2f}")
                    print(f"  📈 P&L: {profit_pct*100:+.1f}%")
            
            # No position - look for entry signals
            else:
                if analysis["momentum_strength"] >= 2:
                    print(f"  🚀 Strong momentum signals - buying BTC")
                    result = execute_btc_buy(analysis, dry_run=False)
                    if result["success"]:
                        position = {
                            "entry_price": result["entry_price"],
                            "volume": result["volume"],
                            "entry_time": datetime.now(),
                            "signals": result["signals"]
                        }
                        print(f"  ✅ BTC position opened!")
                else:
                    print(f"  📊 No entry signals (momentum: {analysis['momentum_strength']}/5)")
            
            print(f"  ──────────────────────────────────")
            time.sleep(CHECK_SECS)
            
    except KeyboardInterrupt:
        print(f"\n  🛑 BTC Momentum Bot stopped by user")
        tg(f"🛑 *BTC Momentum Bot stopped*")
    except Exception as e:
        print(f"\n  ❌ BTC Momentum Bot error: {e}")
        tg(f"❌ *BTC Momentum Bot error*: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="BTC Momentum Bot — Kraken")
    parser.add_argument("--scan", action="store_true", help="One-time scan for BTC momentum opportunities")
    parser.add_argument("--monitor", action="store_true", help="Continuous monitoring with automatic trading")
    parser.add_argument("--dry", action="store_true", help="Dry run (no real orders)")
    args = parser.parse_args()
    
    if args.scan:
        scan_mode()
    elif args.monitor:
        monitor_mode()
    elif args.dry:
        print("🚀 BTC Momentum Bot — Dry Run Mode")
        print("=" * 50)
        analysis = get_btc_momentum_analysis()
        if "error" not in analysis:
            print(f"\n  📊 BITCOIN MOMENTUM ANALYSIS:")
            print(f"  💰 Current Price: ${analysis['current_price']:,.2f}")
            print(f"  📈 RSI: {analysis['rsi']:.1f}")
            print(f"  📊 Trend: {analysis['trend']}")
            print(f"  📊 Volume: {analysis['current_volume']:,.0f} (avg: {analysis['avg_volume']:,.0f})")
            
            if analysis["momentum_signals"]:
                print(f"\n  🚀 MOMENTUM SIGNALS DETECTED:")
                for signal in analysis["momentum_signals"]:
                    print(f"      🚀 {signal}")
                print(f"  📈 Momentum Strength: {analysis['momentum_strength']}/5")
                print(f"\n  💡 *WOULD BUY BTC*")
                execute_btc_buy(analysis, dry_run=True)
            else:
                print(f"\n  📊 *NO MOMENTUM SIGNALS*")
    else:
        print("🚀 BTC Momentum Bot — Kraken")
        print("Usage:")
        print("  python3 btc_momentum_bot.py --scan    # Scan for BTC momentum opportunities")
        print("  python3 btc_momentum_bot.py --monitor # Continuous monitoring")
        print("  python3 btc_momentum_bot.py --dry     # Dry run mode")
        print("\nStrategy:")
        print("  • Buy BTC when momentum signals appear")
        print("  • RSI breakouts, golden crosses, volume spikes")
        print("  • 10% profit target, 5% stop loss")
        print("  • Trend following momentum trading")

if __name__ == "__main__":
    main()
