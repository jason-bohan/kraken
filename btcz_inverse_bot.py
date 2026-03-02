#!/usr/bin/env python3
"""
📉 BTCZ Inverse Bot — Kraken
Automated inverse Bitcoin trading for bearish market conditions.

Strategy:
- BEARISH MODE: Buy BTCZ when BTC shows bearish signals
- BULLISH HEDGE: Hold BTCZ as insurance during BTC uptrends
- PROFIT TARGET: 15% gains when BTC drops
- STOP LOSS: 10% if BTC starts rising (against our position)
- RSI TRIGGERS: RSI > 70 (overbought) + death cross + breakdown

Perfect for:
- Bitcoin bears who think BTC will drop
- Portfolio hedging against long BTC positions  
- Volatility trading without margin borrowing
- Regulatory compliance (no real shorting)

Usage:
    python3 btcz_inverse_bot.py --scan    # Scan for BTCZ opportunities
    python3 btcz_inverse_bot.py --monitor  # Continuous monitoring with alerts
    python3 btcz_inverse_bot.py --dry     # Dry run (no real orders)

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
BTCZ_PAIR      = "XBTZUSD"     # Inverse Bitcoin pair
ASSET_BTC     = "XXBT"         # Bitcoin asset name
ASSET_BTCZ    = "XBTZ"         # Inverse Bitcoin asset name
QUOTE          = "ZUSD"         # Quote currency

# Trading parameters
PROFIT_PCT     = 0.15          # 15% profit target when BTC drops
STOP_PCT       = 0.10          # 10% stop loss if BTC rises
RSI_PERIOD     = 14            # RSI lookback periods
RSI_OVERBOUGHT = 70            # RSI level for bearish signals
MIN_TRADE_USD  = 25.0          # minimum USD per BTCZ trade
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

def get_btc_analysis() -> dict:
    """Analyze Bitcoin for bearish signals."""
    print(f"  🔍 Analyzing Bitcoin for bearish signals...")
    
    # Get BTC ticker data
    btc_ticker = get_ticker(BTC_PAIR)
    if not btc_ticker:
        return {"error": "No BTC ticker data"}
    
    btc_price = float(btc_ticker.get("c", [0])[0])  # last trade price
    
    # Get OHLC data for technical analysis
    ohlc = get_ohlc(BTC_PAIR, interval=15)
    if not ohlc or len(ohlc) < 30:
        return {"error": "Insufficient BTC OHLC data"}
    
    # Extract price data
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
    
    # Calculate trend
    trend_direction = "upward" if ma_short > ma_long else "downward" if ma_short < ma_long else "sideways"
    
    # Bearish signal detection
    signals = []
    
    # 1. RSI Overbought (bearish signal)
    rsi_overbought = rsi > RSI_OVERBOUGHT
    if rsi_overbought:
        signals.append("RSI_OVERBOUGHT")
    
    # 2. Death Cross (MA cross down)
    death_cross = ma_short < ma_long and trend_direction == "downward"
    if death_cross:
        signals.append("DEATH_CROSS")
    
    # 3. Price Breakdown
    breakdown = (recent_low - btc_price) / recent_low > 0.02  # 2% below recent low
    if breakdown:
        signals.append("BREAKDOWN")
    
    # 4. Volume Analysis (optional - for confirmation)
    avg_volume = sum(volumes[-20:]) / 20
    current_volume = volumes[-1]
    high_volume = current_volume > (avg_volume * 1.5)  # 50% above average
    if high_volume:
        signals.append("HIGH_VOLUME")
    
    # Calculate signal strength
    signal_strength = len(signals)
    
    # Get BTCZ price for correlation
    btcz_ticker = get_ticker(BTCZ_PAIR)
    btcz_price = float(btcz_ticker.get("c", [0])[0]) if btcz_ticker else 0.0
    
    # Calculate correlation
    btc_change_1h = (closes[-1] - closes[-4]) / closes[-4] if len(closes) >= 4 else 0
    btcz_change_1h = (btcz_price - btcz_price) / btcz_price if btcz_price > 0 else 0
    
    return {
        "btc_price": btc_price,
        "btcz_price": btcz_price,
        "rsi": rsi,
        "trend": trend_direction,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "signals": signals,
        "signal_strength": signal_strength,
        "btc_change_1h": btc_change_1h,
        "btcz_change_1h": btcz_change_1h,
        "avg_volume": avg_volume,
        "current_volume": current_volume,
        "high_volume": high_volume
    }

def get_btcz_size(price: float, dry_run: bool) -> float:
    """Calculate how much BTCZ to buy with available USD."""
    if dry_run:
        # For dry run, use a reasonable test amount
        order_info = calculate_order_size(BTCZ_PAIR, price, available_usd=100.0)
        return order_info['volume'] if order_info['can_afford'] else 0.0
    
    balances = get_balance()
    available_usd = float(balances.get(QUOTE, 0)) - RESERVE_USD
    
    if available_usd <= MIN_TRADE_USD:
        return 0.0
    
    order_info = calculate_order_size(BTCZ_PAIR, price, available_usd=available_usd)
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot afford BTCZ order')}")
        return 0.0
    
    return order_info['volume']

def get_btcz_sell_size(dry_run: bool) -> float:
    """Calculate how much BTCZ to sell from existing holdings."""
    if dry_run:
        # For dry run, simulate selling minimum amount
        min_info = calculate_order_size(BTCZ_PAIR, 10.0, available_asset=0.1)
        return min_info.get('volume', 0.1)
    
    balances = get_balance()
    available_btcz = float(balances.get(ASSET_BTCZ, 0))
    
    if available_btcz <= 0:
        return 0.0
    
    price = float(get_ticker(BTCZ_PAIR).get("c", [0])[0])
    order_info = calculate_order_size(BTCZ_PAIR, price, available_asset=available_btcz)
    
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot sell BTCZ')}")
        return 0.0
    
    return available_btcz

# ─────────────────────────────────────────────
# TRADING FUNCTIONS
# ─────────────────────────────────────────────
def execute_btcz_buy(analysis: dict, dry_run: bool = False) -> dict:
    """Execute BTCZ buy order based on bearish signals."""
    try:
        btcz_price = analysis["btcz_price"]
        signal_strength = analysis["signal_strength"]
        signals = analysis["signals"]
        
        # Only buy if we have strong bearish signals
        if signal_strength < 2:
            return {"success": False, "reason": f"Weak signals (strength: {signal_strength})"}
        
        # Calculate position size
        buy_volume = get_btcz_size(btcz_price, dry_run)
        
        if buy_volume <= 0:
            return {"success": False, "reason": "Insufficient USD or cannot afford order"}
        
        # Calculate profit and stop levels
        profit_target_price = btcz_price * (1 + PROFIT_PCT)  # 15% profit target
        stop_loss_price = btcz_price * (1 - STOP_PCT)     # 10% stop loss
        
        print(f"\n  📉 BEARISH BTC SIGNAL DETECTED!")
        print(f"  📊 BTC Price: ${analysis['btc_price']:,.2f}")
        print(f"  📉 BTCZ Price: ${btcz_price:,.4f}")
        print(f"  📈 Signal Strength: {signal_strength}/4")
        print(f"  🚨 Signals: {', '.join(signals)}")
        print(f"  💰 Buying {buy_volume:.4f} BTCZ @ ${btcz_price:,.4f}")
        print(f"  🎯 Profit Target: ${profit_target_price:,.4f} (+{PROFIT_PCT*100:.1f}%)")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:,.4f} (-{STOP_PCT*100:.1f}%)")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place BTCZ buy order")
            return {"success": True, "action": "DRY_RUN_BUY", "volume": buy_volume}
        
        # Place real order
        order = place_order(
            pair=BTCZ_PAIR,
            type="buy",
            ordertype="market",
            volume=buy_volume,
            validate=False
        )
        
        if order.get('error'):
            print(f"  ❌ BTCZ buy order failed: {order['error']}")
            return {"success": False, "error": order['error']}
        
        print(f"  ✅ BTCZ buy order placed: {order['descr']['order']}")
        
        # Send Telegram notification
        msg = f"📉 *BTCZ Buy Signal*\n\n"
        msg += f"📊 BTC: ${analysis['btc_price']:,.2f}\n"
        msg += f"📉 BTCZ: ${btcz_price:,.4f}\n"
        msg += f"🚨 Signals: {', '.join(signals)}\n"
        msg += f"💰 Volume: {buy_volume:.4f}\n"
        msg += f"🎯 Target: +{PROFIT_PCT*100:.1f}%\n"
        msg += f"🛡️ Stop: -{STOP_PCT*100:.1f}%\n"
        msg += f"🤖 BTCZ Inverse Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "BUY_BTCZ",
            "volume": buy_volume,
            "entry_price": btcz_price,
            "profit_target": profit_target_price,
            "stop_loss": stop_loss_price,
            "signals": signals
        }
        
    except Exception as e:
        print(f"  ❌ BTCZ buy execution error: {e}")
        return {"success": False, "error": str(e)}

def execute_btcz_sell(analysis: dict, dry_run: bool = False) -> dict:
    """Execute BTCZ sell order (profit taking or stop loss)."""
    try:
        btcz_price = analysis["btcz_price"]
        
        # Get current BTCZ position
        balances = get_balance()
        available_btcz = float(balances.get(ASSET_BTCZ, 0))
        
        if available_btcz <= 0:
            return {"success": False, "reason": "No BTCZ position to sell"}
        
        sell_volume = get_btcz_sell_size(dry_run)
        
        if sell_volume <= 0:
            return {"success": False, "reason": "Cannot sell BTCZ position"}
        
        print(f"\n  💰 SELLING BTCZ POSITION!")
        print(f"  📉 Current BTCZ Price: ${btcz_price:,.4f}")
        print(f"  💰 Selling {sell_volume:.4f} BTCZ @ market price")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place BTCZ sell order")
            return {"success": True, "action": "DRY_RUN_SELL", "volume": sell_volume}
        
        # Place real sell order
        order = place_order(
            pair=BTCZ_PAIR,
            type="sell",
            ordertype="market",
            volume=sell_volume,
            validate=False
        )
        
        if order.get('error'):
            print(f"  ❌ BTCZ sell order failed: {order['error']}")
            return {"success": False, "error": order['error']}
        
        print(f"  ✅ BTCZ sell order placed: {order['descr']['order']}")
        
        # Send Telegram notification
        msg = f"💰 *BTCZ Position Sold*\n\n"
        msg += f"📉 BTCZ Price: ${btcz_price:,.4f}\n"
        msg += f"💰 Volume: {sell_volume:.4f}\n"
        msg += f"🤖 BTCZ Inverse Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "SELL_BTCZ",
            "volume": sell_volume,
            "exit_price": btcz_price
        }
        
    except Exception as e:
        print(f"  ❌ BTCZ sell execution error: {e}")
        return {"success": False, "error": str(e)}

# ─────────────────────────────────────────────
# MAIN FUNCTIONS
# ─────────────────────────────────────────────
def scan_mode():
    """One-time scan for BTCZ opportunities."""
    print("📉 BTCZ Inverse Bot — Bearish Signal Scanner")
    print("=" * 60)
    
    analysis = get_btc_analysis()
    
    if "error" in analysis:
        print(f"  ❌ {analysis['error']}")
        return
    
    print(f"\n  📊 BITCOIN ANALYSIS:")
    print(f"  💰 BTC Price: ${analysis['btc_price']:,.2f}")
    print(f"  📉 BTCZ Price: ${analysis['btcz_price']:,.4f}")
    print(f"  📈 RSI: {analysis['rsi']:.1f}")
    print(f"  📊 Trend: {analysis['trend']}")
    print(f"  📊 1h Change: {analysis['btc_change_1h']*100:+.2f}%")
    print(f"  📉 BTCZ 1h Change: {analysis['btcz_change_1h']*100:+.2f}%")
    
    if analysis["signals"]:
        print(f"\n  🚨 BEARISH SIGNALS DETECTED:")
        for signal in analysis["signals"]:
            print(f"      📉 {signal}")
        print(f"  📈 Signal Strength: {analysis['signal_strength']}/4")
        
        # Recommend action
        if analysis["signal_strength"] >= 2:
            print(f"\n  💡 *RECOMMENDATION*: BUY BTCZ")
            print(f"      📉 Signal strength indicates Bitcoin will drop")
            print(f"      🎯 Target: +{PROFIT_PCT*100:.1f}% profit")
            print(f"      🛡️ Stop: -{STOP_PCT*100:.1f}% if BTC rises")
            
            # Execute buy if not dry run
            result = execute_btcz_buy(analysis, dry_run=False)
            if result["success"]:
                print(f"  ✅ BTCZ position opened successfully!")
        else:
            print(f"\n  📊 *WEAK SIGNALS*: Strength {analysis['signal_strength']}/4")
            print(f"      💡 Wait for stronger bearish signals")
    else:
        print(f"\n  📊 *NO BEARISH SIGNALS*: Bitcoin looks bullish")
        print(f"      💡 Hold off on BTCZ - wait for overbought conditions")

def monitor_mode():
    """Continuous monitoring mode with automatic trading."""
    print("📉 BTCZ Inverse Bot — Continuous Monitoring")
    print("=" * 60)
    print(f"  📊 Monitoring BTC every {CHECK_SECS}s")
    print(f"  🎯 Auto-buying BTCZ on bearish signals (strength >= 2)")
    print(f"  💰 Profit target: +{PROFIT_PCT*100:.1f}%")
    print(f"  🛡️ Stop loss: -{STOP_PCT*100:.1f}%")
    print(f"  💵 Minimum trade: ${MIN_TRADE_USD}")
    print("=" * 60)
    
    tg(f"📉 *BTCZ Inverse Bot started* - Monitoring mode")
    
    position = None  # Track active BTCZ position
    cycle = 0
    
    try:
        while True:
            cycle += 1
            analysis = get_btc_analysis()
            
            if "error" in analysis:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] ⚠️ No BTC data, waiting...")
                time.sleep(CHECK_SECS)
                continue
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Cycle {cycle}")
            print(f"  📊 BTC: ${analysis['btc_price']:,.2f} | BTCZ: ${analysis['btcz_price']:,.4f}")
            print(f"  📈 RSI: {analysis['rsi']:.1f} | Signals: {analysis['signal_strength']}")
            
            # Show current signals
            if analysis["signals"]:
                signal_str = ', '.join(analysis["signals"])
                print(f"  🚨 Bearish: {signal_str}")
            else:
                print(f"  📊 No bearish signals")
            
            # Handle existing position
            if position:
                # Check if we should sell (profit target or stop loss)
                current_price = analysis["btcz_price"]
                entry_price = position["entry_price"]
                
                profit_pct = (current_price - entry_price) / entry_price
                
                if profit_pct >= PROFIT_PCT:
                    print(f"  🎯 Profit target hit: +{profit_pct*100:.1f}%")
                    result = execute_btcz_sell(analysis, dry_run=False)
                    if result["success"]:
                        position = None
                        print(f"  ✅ BTCZ position closed with profit!")
                elif profit_pct <= -STOP_PCT:
                    print(f"  🛡️ Stop loss hit: {profit_pct*100:.1f}%")
                    result = execute_btcz_sell(analysis, dry_run=False)
                    if result["success"]:
                        position = None
                        print(f"  ⚠️ BTCZ position closed with loss")
                else:
                    # Show position progress
                    print(f"  📊 Position: {position['volume']:.4f} BTCZ @ ${entry_price:.4f}")
                    print(f"  📈 P&L: {profit_pct*100:+.1f}%")
            
            # No position - look for entry signals
            else:
                if analysis["signal_strength"] >= 2:
                    print(f"  🚨 Strong bearish signals - buying BTCZ")
                    result = execute_btcz_buy(analysis, dry_run=False)
                    if result["success"]:
                        position = {
                            "entry_price": result["entry_price"],
                            "volume": result["volume"],
                            "entry_time": datetime.now(),
                            "signals": result["signals"]
                        }
                        print(f"  ✅ BTCZ position opened!")
                else:
                    print(f"  📊 No entry signals (strength: {analysis['signal_strength']})")
            
            print(f"  ──────────────────────────────────")
            time.sleep(CHECK_SECS)
            
    except KeyboardInterrupt:
        print(f"\n  🛑 BTCZ Inverse Bot stopped by user")
        tg(f"🛑 *BTCZ Inverse Bot stopped*")
    except Exception as e:
        print(f"\n  ❌ BTCZ Inverse Bot error: {e}")
        tg(f"❌ *BTCZ Inverse Bot error*: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="BTCZ Inverse Bot — Kraken")
    parser.add_argument("--scan", action="store_true", help="One-time scan for BTCZ opportunities")
    parser.add_argument("--monitor", action="store_true", help="Continuous monitoring with automatic trading")
    parser.add_argument("--dry", action="store_true", help="Dry run (no real orders)")
    args = parser.parse_args()
    
    if args.scan:
        scan_mode()
    elif args.monitor:
        monitor_mode()
    elif args.dry:
        print("📉 BTCZ Inverse Bot — Dry Run Mode")
        print("=" * 60)
        analysis = get_btc_analysis()
        if "error" not in analysis:
            print(f"\n  📊 BITCOIN ANALYSIS:")
            print(f"  💰 BTC Price: ${analysis['btc_price']:,.2f}")
            print(f"  📉 BTCZ Price: ${analysis['btcz_price']:,.4f}")
            print(f"  📈 RSI: {analysis['rsi']:.1f}")
            print(f"  📊 Trend: {analysis['trend']}")
            
            if analysis["signals"]:
                print(f"\n  🚨 BEARISH SIGNALS DETECTED:")
                for signal in analysis["signals"]:
                    print(f"      📉 {signal}")
                print(f"  📈 Signal Strength: {analysis['signal_strength']}/4")
                print(f"\n  💡 *WOULD BUY BTCZ*")
                execute_btcz_buy(analysis, dry_run=True)
            else:
                print(f"\n  📊 *NO BEARISH SIGNALS*")
    else:
        print("📉 BTCZ Inverse Bot — Kraken")
        print("Usage:")
        print("  python3 btcz_inverse_bot.py --scan    # Scan for BTCZ opportunities")
        print("  python3 btcz_inverse_bot.py --monitor  # Continuous monitoring")
        print("  python3 btcz_inverse_bot.py --dry     # Dry run mode")
        print("\nStrategy: Buy BTCZ when BTC shows bearish signals")
        print("Profit target: 15% when BTC drops")
        print("Stop loss: 10% if BTC rises against position")

if __name__ == "__main__":
    main()
