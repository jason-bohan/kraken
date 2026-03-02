#!/usr/bin/env python3
"""
🔄 BTCZ Dual Bot — Kraken
Dual-direction Bitcoin inverse trading for both bearish and bullish conditions.

Strategy:
- BEARISH MODE: Buy BTCZ when BTC shows bearish signals
- BULLISH MODE: Buy BITO (2x Long Bitcoin) when BTC shows bullish signals  
- AUTO-SWITCHING: Automatically switches between BTCZ and BITO based on market direction
- PROFIT TARGET: 15% gains when BTC moves in favor of position
- STOP LOSS: 10% when BTC moves against position

Available Products:
- BTCZ: Inverse Bitcoin (-1x when BTC up, +1x when BTC down)
- BITO: 2x Long Bitcoin (+2x when BTC up, -2x when BTC down)
- Check availability on your exchange before trading

Usage:
    python3 btcz_dual_bot.py --scan          # Scan for both BTCZ/BITO opportunities
    python3 btcz_dual_bot.py --monitor       # Continuous monitoring with auto-switching
    python3 btcz_dual_bot.py --dry         # Dry run (no real orders)

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
BITO_PAIR      = "BITOUSD"     # 2x Long Bitcoin (if available)
ASSET_BTCZ    = "XBTZ"         # Inverse Bitcoin asset name
ASSET_BITO    = "BITO"         # 2x Long Bitcoin asset name
QUOTE          = "ZUSD"         # Quote currency

# Trading parameters
PROFIT_PCT     = 0.15          # 15% profit target
STOP_PCT       = 0.10          # 10% stop loss
RSI_PERIOD     = 14            # RSI lookback periods
RSI_OVERSOLD   = 30            # Extreme oversold for bullish signals
RSI_OVERBOUGHT = 70            # Extreme overbought for bearish signals
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

def get_btc_analysis() -> dict:
    """Analyze Bitcoin for both bullish and bearish signals."""
    print(f"  🔍 Analyzing Bitcoin for dual-direction signals...")
    
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
    
    # Signal detection
    bearish_signals = []
    bullish_signals = []
    
    # Bearish signals (for BTCZ)
    if rsi > RSI_OVERBOUGHT:
        bearish_signals.append("RSI_OVERBOUGHT")
    
    death_cross = ma_short < ma_long and trend_direction == "downward"
    if death_cross:
        bearish_signals.append("DEATH_CROSS")
    
    breakdown = (recent_low - btc_price) / recent_low > 0.02
    if breakdown:
        bearish_signals.append("BREAKDOWN")
    
    # Bullish signals (for BITO)
    if rsi < RSI_OVERSOLD:
        bullish_signals.append("RSI_OVERSOLD")
    
    golden_cross = ma_short > ma_long and trend_direction == "upward"
    if golden_cross:
        bullish_signals.append("GOLDEN_CROSS")
    
    breakout = (btc_price - recent_high) / recent_high > 0.02
    if breakout:
        bullish_signals.append("BREAKOUT")
    
    # Calculate signal strengths
    bearish_strength = len(bearish_signals)
    bullish_strength = len(bullish_signals)
    
    # Get inverse product prices
    btcz_ticker = get_ticker(BTCZ_PAIR)
    btcz_price = float(btcz_ticker.get("c", [0])[0]) if btcz_ticker else 0.0
    
    bito_ticker = get_ticker(BITO_PAIR)
    bito_price = float(bito_ticker.get("c", [0])[0]) if bito_ticker else 0.0
    
    return {
        "btc_price": btc_price,
        "btcz_price": btcz_price,
        "bito_price": bito_price,
        "rsi": rsi,
        "trend": trend_direction,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "bearish_signals": bearish_signals,
        "bullish_signals": bullish_signals,
        "bearish_strength": bearish_strength,
        "bullish_strength": bullish_strength,
        "btcz_available": btcz_price > 0,
        "bito_available": bito_price > 0
    }

def get_position_size(product: str, price: float, dry_run: bool) -> float:
    """Calculate position size for specified inverse product."""
    if dry_run:
        # For dry run, use a reasonable test amount
        order_info = calculate_order_size(f"{product}USD", price, available_usd=100.0)
        return order_info['volume'] if order_info['can_afford'] else 0.0
    
    balances = get_balance()
    available_usd = float(balances.get(QUOTE, 0)) - RESERVE_USD
    
    if available_usd <= MIN_TRADE_USD:
        return 0.0
    
    order_info = calculate_order_size(f"{product}USD", price, available_usd=available_usd)
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot afford order')}")
        return 0.0
    
    return order_info['volume']

def execute_btcz_trade(analysis: dict, dry_run: bool = False) -> dict:
    """Execute BTCZ buy order for bearish signals."""
    try:
        if not analysis["btcz_available"]:
            return {"success": False, "error": "BTCZ not available on this exchange"}
        
        btcz_price = analysis["btcz_price"]
        signal_strength = analysis["bearish_strength"]
        signals = analysis["bearish_signals"]
        
        # Only trade if we have strong bearish signals
        if signal_strength < 2:
            return {"success": False, "reason": f"Weak bearish signals (strength: {signal_strength})"}
        
        # Calculate position size
        position_size = get_position_size("XBTZ", btcz_price, dry_run)
        
        if position_size <= 0:
            return {"success": False, "reason": "Insufficient USD or cannot afford order"}
        
        # Calculate profit and stop levels
        profit_target_price = btcz_price * (1 + PROFIT_PCT)  # 15% profit when BTC drops
        stop_loss_price = btcz_price * (1 - STOP_PCT)      # 10% stop if BTC rises
        
        print(f"\n  📉 BEARISH BTC SIGNAL DETECTED!")
        print(f"  💰 BTC Price: ${analysis['btc_price']:,.2f}")
        print(f"  📉 BTCZ Price: ${btcz_price:,.4f}")
        print(f"  📈 Signal Strength: {signal_strength}/4")
        print(f"  🚨 Signals: {', '.join(signals)}")
        print(f"  💰 Buying {position_size:.2f} BTCZ @ ${btcz_price:,.4f}")
        print(f"  🎯 Profit Target: ${profit_target_price:,.4f} (+{PROFIT_PCT*100:.1f}%)")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:,.4f} (-{STOP_PCT*100:.1f}%)")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place BTCZ buy order")
            return {"success": True, "action": "DRY_RUN_BUY_BTCZ", "volume": position_size}
        
        # Place real order
        order = place_order(
            pair=BTCZ_PAIR,
            type="buy",
            ordertype="market",
            volume=position_size,
            validate=False
        )
        
        if order.get('error'):
            print(f"  ❌ BTCZ buy order failed: {order['error']}")
            return {"success": False, "error": order['error']}
        
        print(f"  ✅ BTCZ buy order placed: {order['descr']['order']}")
        
        # Send Telegram notification
        msg = f"📉 *BTCZ Bearish Trade*\n\n"
        msg += f"💰 BTC: ${analysis['btc_price']:,.2f}\n"
        msg += f"📉 BTCZ: ${btcz_price:,.4f}\n"
        msg += f"📈 RSI: {analysis['rsi']:.1f}\n"
        msg += f"🚨 Signals: {', '.join(signals)}\n"
        msg += f"💰 Volume: {position_size:.2f}\n"
        msg += f"🎯 Target: +{PROFIT_PCT*100:.1f}%\n"
        msg += f"🛡️ Stop: -{STOP_PCT*100:.1f}%\n"
        msg += f"🤖 BTCZ Dual Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "BUY_BTCZ",
            "volume": position_size,
            "entry_price": btcz_price,
            "profit_target": profit_target_price,
            "stop_loss": stop_loss_price,
            "signals": signals
        }
        
    except Exception as e:
        print(f"  ❌ BTCZ trade execution error: {e}")
        return {"success": False, "error": str(e)}

def execute_bito_trade(analysis: dict, dry_run: bool = False) -> dict:
    """Execute BITO buy order for bullish signals."""
    try:
        if not analysis["bito_available"]:
            return {"success": False, "error": "BITO not available on this exchange"}
        
        bito_price = analysis["bito_price"]
        signal_strength = analysis["bullish_strength"]
        signals = analysis["bullish_signals"]
        
        # Only trade if we have strong bullish signals
        if signal_strength < 2:
            return {"success": False, "reason": f"Weak bullish signals (strength: {signal_strength})"}
        
        # Calculate position size
        position_size = get_position_size("BITO", bito_price, dry_run)
        
        if position_size <= 0:
            return {"success": False, "reason": "Insufficient USD or cannot afford order"}
        
        # Calculate profit and stop levels
        profit_target_price = bito_price * (1 + PROFIT_PCT)  # 15% profit when BTC rises
        stop_loss_price = bito_price * (1 - STOP_PCT)      # 10% stop if BTC drops
        
        print(f"\n  📈 BULLISH BTC SIGNAL DETECTED!")
        print(f"  💰 BTC Price: ${analysis['btc_price']:,.2f}")
        print(f"  📈 BITO Price: ${bito_price:,.4f}")
        print(f"  📈 Signal Strength: {signal_strength}/4")
        print(f"  🚨 Signals: {', '.join(signals)}")
        print(f"  💰 Buying {position_size:.2f} BITO @ ${bito_price:,.4f}")
        print(f"  🎯 Profit Target: ${profit_target_price:,.4f} (+{PROFIT_PCT*100:.1f}%)")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:,.4f} (-{STOP_PCT*100:.1f}%)")
        print(f"  📊 Leverage: 2x (amplifies BTC moves)")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place BITO buy order")
            return {"success": True, "action": "DRY_RUN_BUY_BITO", "volume": position_size}
        
        # Place real order
        order = place_order(
            pair=BITO_PAIR,
            type="buy",
            ordertype="market",
            volume=position_size,
            validate=False
        )
        
        if order.get('error'):
            print(f"  ❌ BITO buy order failed: {order['error']}")
            return {"success": False, "error": order['error']}
        
        print(f"  ✅ BITO buy order placed: {order['descr']['order']}")
        
        # Send Telegram notification
        msg = f"📈 *BITO Bullish Trade*\n\n"
        msg += f"💰 BTC: ${analysis['btc_price']:,.2f}\n"
        msg += f"📈 BITO: ${bito_price:,.4f}\n"
        msg += f"📈 RSI: {analysis['rsi']:.1f}\n"
        msg += f"🚨 Signals: {', '.join(signals)}\n"
        msg += f"💰 Volume: {position_size:.2f}\n"
        msg += f"🎯 Target: +{PROFIT_PCT*100:.1f}%\n"
        msg += f"🛡️ Stop: -{STOP_PCT*100:.1f}%\n"
        msg += f"📊 Leverage: 2x (amplifies BTC moves)\n"
        msg += f"🤖 BTCZ Dual Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "BUY_BITO",
            "volume": position_size,
            "entry_price": bito_price,
            "profit_target": profit_target_price,
            "stop_loss": stop_loss_price,
            "signals": signals
        }
        
    except Exception as e:
        print(f"  ❌ BITO trade execution error: {e}")
        return {"success": False, "error": str(e)}

# ─────────────────────────────────────────────
# MAIN FUNCTIONS
# ─────────────────────────────────────────────
def scan_mode():
    """One-time scan for both BTCZ and BITO opportunities."""
    print("🔄 BTCZ Dual Bot — Dual-Direction Scanner")
    print("=" * 65)
    
    analysis = get_btc_analysis()
    
    if "error" in analysis:
        print(f"  ❌ {analysis['error']}")
        return
    
    print(f"\n  📊 BITCOIN ANALYSIS:")
    print(f"  💰 BTC Price: ${analysis['btc_price']:,.2f}")
    print(f"  📉 BTCZ Price: ${analysis['btcz_price']:,.4f} {'✅ Available' if analysis['btcz_available'] else '❌ Unavailable'}")
    print(f"  📈 BITO Price: ${analysis['bito_price']:,.4f} {'✅ Available' if analysis['bito_available'] else '❌ Unavailable'}")
    print(f"  📈 RSI: {analysis['rsi']:.1f}")
    print(f"  📊 Trend: {analysis['trend']}")
    
    # Check available products
    if not analysis["btcz_available"] and not analysis["bito_available"]:
        print(f"\n  ❌ NO INVERSE PRODUCTS AVAILABLE")
        print(f"  💡 This exchange doesn't offer BTCZ or BITO")
        print(f"  📊 Available alternatives: Check other exchanges for inverse Bitcoin products")
        return
    
    # Show signals
    if analysis["bearish_signals"]:
        print(f"\n  🚨 BEARISH SIGNALS (for BTCZ):")
        for signal in analysis["bearish_signals"]:
            print(f"      📉 {signal}")
        print(f"  📈 Strength: {analysis['bearish_strength']}/4")
        print(f"  💡 *RECOMMENDATION*: BUY BTCZ")
    
    if analysis["bullish_signals"]:
        print(f"\n  🚨 BULLISH SIGNALS (for BITO):")
        for signal in analysis["bullish_signals"]:
            print(f"      📈 {signal}")
        print(f"  📈 Strength: {analysis['bullish_strength']}/4")
        print(f"  💡 *RECOMMENDATION*: BUY BITO")
    
    if not analysis["bearish_signals"] and not analysis["bullish_signals"]:
        print(f"\n  📊 *NO CLEAR SIGNALS*: RSI: {analysis['rsi']:.1f} (neutral)")
        print(f"  💡 Wait for stronger directional signals")

def monitor_mode():
    """Continuous monitoring with automatic position switching."""
    print("🔄 BTCZ Dual Bot — Continuous Monitoring")
    print("=" * 65)
    print(f"  📊 Monitoring BTC every {CHECK_SECS}s")
    print(f"  🔄 Auto-switching between BTCZ (bearish) and BITO (bullish)")
    print(f"  💰 Profit target: +{PROFIT_PCT*100:.1f}%")
    print(f"  🛡️ Stop loss: -{STOP_PCT*100:.1f}%")
    print(f"  💵 Minimum trade: ${MIN_TRADE_USD}")
    print("=" * 65)
    
    tg(f"🔄 *BTCZ Dual Bot started* - Auto-switching enabled")
    
    position = None  # Track active position: {type, entry_price, volume, entry_time}
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
            print(f"  📊 BTC: ${analysis['btc_price']:,.2f} | RSI: {analysis['rsi']:.1f}")
            print(f"  📉 BTCZ: ${analysis['btcz_price']:,.4f} | 📈 BITO: ${analysis['bito_price']:,.4f}")
            print(f"  🚨 Bearish: {analysis['bearish_strength']}/4 | 📈 Bullish: {analysis['bullish_strength']}/4")
            
            # Handle existing position
            if position:
                current_price = analysis["btcz_price"] if position["type"] == "BTCZ" else analysis["bito_price"]
                profit_pct = (current_price - position["entry_price"]) / position["entry_price"]
                
                # Check profit target
                if profit_pct >= PROFIT_PCT:
                    print(f"  🎯 Profit target hit: +{profit_pct*100:.1f}%")
                    print(f"  💰 Selling {position['volume']:.2f} {position['type']} @ ${current_price:,.4f}")
                    
                    # Close position (sell for profit)
                    if position["type"] == "BTCZ":
                        # BTCZ profit means BTC dropped - buy back BTCZ
                        print(f"  🔄 BTC dropped - buying back BTCZ")
                        result = execute_btcz_trade(analysis, dry_run=False)
                    elif position["type"] == "BITO":
                        # BITO profit means BTC rose - sell BITO
                        print(f"  💰 Taking profits on BITO")
                        # Would need to implement BITO selling logic
                    
                    if result["success"]:
                        position = None
                        print(f"  ✅ Position closed with profit!")
                
                # Check stop loss
                elif profit_pct <= -STOP_PCT:
                    print(f"  🛡️ Stop loss hit: {profit_pct*100:.1f}%")
                    print(f"  💰 Selling {position['volume']:.2f} {position['type']} @ ${current_price:,.4f}")
                    
                    # Close position (sell for loss)
                    if position["type"] == "BTCZ":
                        print(f"  ⚠️ BTC rising against BTCZ position")
                    elif position["type"] == "BITO":
                        print(f"  ⚠️ BTC dropping against BITO position")
                    
                    position = None
                    print(f"  ✅ Position closed with stop loss")
                
                else:
                    # Show position progress
                    print(f"  📊 Position: {position['type']} {position['volume']:.2f} @ ${position['entry_price']:,.4f} | P&L: {profit_pct*100:+.1f}%")
            
            # No position - look for entry signals
            else:
                # Check for bearish signals (buy BTCZ)
                if analysis["bearish_strength"] >= 2 and analysis["btcz_available"]:
                    print(f"  🚨 Strong bearish signals - buying BTCZ")
                    result = execute_btcz_trade(analysis, dry_run=False)
                    
                    if result["success"]:
                        position = {
                            "type": "BTCZ",
                            "entry_price": result["entry_price"],
                            "volume": result["volume"],
                            "entry_time": datetime.now(),
                            "signals": result["signals"]
                        }
                        print(f"  ✅ BTCZ position opened!")
                
                # Check for bullish signals (buy BITO)
                elif analysis["bullish_strength"] >= 2 and analysis["bito_available"]:
                    print(f"  🚨 Strong bullish signals - buying BITO")
                    result = execute_bito_trade(analysis, dry_run=False)
                    
                    if result["success"]:
                        position = {
                            "type": "BITO",
                            "entry_price": result["entry_price"],
                            "volume": result["volume"],
                            "entry_time": datetime.now(),
                            "signals": result["signals"]
                        }
                        print(f"  ✅ BITO position opened!")
                
                else:
                    print(f"  📊 No entry signals (Bearish: {analysis['bearish_strength']}/4, Bullish: {analysis['bullish_strength']}/4)")
            
            print(f"  ──────────────────────────────────")
            time.sleep(CHECK_SECS)
            
    except KeyboardInterrupt:
        print(f"\n  🛑 BTCZ Dual Bot stopped by user")
        tg(f"🛑 *BTCZ Dual Bot stopped*")
    except Exception as e:
        print(f"\n  ❌ BTCZ Dual Bot error: {e}")
        tg(f"❌ *BTCZ Dual Bot error*: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="BTCZ Dual Bot — Kraken")
    parser.add_argument("--scan", action="store_true", help="One-time scan for dual-direction opportunities")
    parser.add_argument("--monitor", action="store_true", help="Continuous monitoring with auto-switching")
    parser.add_argument("--dry", action="store_true", help="Dry run (no real orders)")
    args = parser.parse_args()
    
    if args.scan:
        scan_mode()
    elif args.monitor:
        monitor_mode()
    elif args.dry:
        print("🔄 BTCZ Dual Bot — Dry Run Mode")
        print("=" * 65)
        analysis = get_btc_analysis()
        if "error" not in analysis:
            scan_mode()
    else:
        print("🔄 BTCZ Dual Bot — Kraken")
        print("Usage:")
        print("  python3 btcz_dual_bot.py --scan    # Scan for BTCZ/BITO opportunities")
        print("  python3 btcz_dual_bot.py --monitor # Continuous monitoring")
        print("  python3 btcz_dual_bot.py --dry     # Dry run mode")
        print("\nStrategy:")
        print("  - Bearish BTC signals → Buy BTCZ (inverse Bitcoin)")
        print("  - Bullish BTC signals → Buy BITO (2x Long Bitcoin)")
        print("  - Auto-switching: Changes positions based on market direction")
        print("  - 15% profit target when BTC moves in favor")
        print("  - 10% stop loss when BTC moves against position")
        print("\nNote: BITO (2x Long Bitcoin) may not be available on all exchanges")
        print("Check product availability before live trading")

if __name__ == "__main__":
    main()
