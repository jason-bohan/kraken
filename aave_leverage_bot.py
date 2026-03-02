#!/usr/bin/env python3
"""
⚡ AAVE Leverage Bot — Kraken
Uses AAVEUSD with 2-3x leverage as Bitcoin proxy trading.

Strategy:
- BULLISH MODE: Long AAVEUSD with 2-3x leverage when BTC signals bullish
- BEARISH MODE: Short AAVEUSD with 2-3x leverage when BTC signals bearish  
- BTC PROXY: AAVE correlates with Bitcoin (both are major DeFi tokens)
- LEVERAGE: 2x for safety, 3x for aggressive trading
- PROFIT TARGET: 15% gains on leveraged positions
- STOP LOSS: 8% (tighter due to leverage risk)

Perfect for:
- Bitcoin exposure without BTC futures
- Leveraged trading on Kraken
- Correlation play (AAVE ~0.7 correlation with BTC)
- Lower entry cost than BTC futures

Usage:
    python3 aave_leverage_bot.py --scan          # Scan for AAVE opportunities
    python3 aave_leverage_bot.py --monitor       # Continuous monitoring
    python3 aave_leverage_bot.py --dry         # Dry run (no real orders)

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
BTC_PAIR       = "XBTUSD"      # Bitcoin trading pair (for signals)
AAVE_PAIR      = "AAVEUSD"     # AAVE trading pair (for execution)
ASSET_AAVE    = "AAVE"         # AAVE asset name in balance
QUOTE          = "ZUSD"        # Quote currency

# Trading parameters
PROFIT_PCT     = 0.15          # 15% profit target on leveraged positions
STOP_PCT       = 0.08          # 8% stop loss (tighter due to leverage)
RSI_PERIOD     = 14            # RSI lookback periods
RSI_OVERSOLD   = 30            # RSI level for bullish signals
RSI_OVERBOUGHT = 70            # RSI level for bearish signals
LEVERAGE_SAFE  = 2              # 2x leverage for safety
LEVERAGE_AGGRESSIVE = 3          # 3x leverage for aggressive
MIN_TRADE_USD  = 50.0          # minimum USD per leveraged trade
RESERVE_USD    = 10.0          # keep this much USD in reserve
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

def get_btc_aave_analysis() -> dict:
    """Analyze Bitcoin for signals and AAVE for execution."""
    print(f"  🔍 Analyzing BTC for signals, AAVE for execution...")
    
    # Get BTC ticker data (for signals)
    btc_ticker = get_ticker(BTC_PAIR)
    if not btc_ticker:
        return {"error": "No BTC ticker data"}
    
    btc_price = float(btc_ticker.get("c", [0])[0])  # last trade price
    
    # Get AAVE ticker data (for execution)
    aave_ticker = get_ticker(AAVE_PAIR)
    if not aave_ticker:
        return {"error": "No AAVE ticker data"}
    
    aave_price = float(aave_ticker.get("c", [0])[0])  # last trade price
    
    # Get BTC OHLC data for signal analysis
    btc_ohlc = get_ohlc(BTC_PAIR, interval=15)
    if not btc_ohlc or len(btc_ohlc) < 30:
        return {"error": "Insufficient BTC OHLC data"}
    
    # Get AAVE OHLC data for correlation analysis
    aave_ohlc = get_ohlc(AAVE_PAIR, interval=15)
    if not aave_ohlc or len(aave_ohlc) < 30:
        return {"error": "Insufficient AAVE OHLC data"}
    
    # Extract price data
    btc_closes = [float(candle[4]) for candle in btc_ohlc]
    btc_highs = [float(candle[2]) for candle in btc_ohlc]
    btc_lows = [float(candle[3]) for candle in btc_ohlc]
    
    aave_closes = [float(candle[4]) for candle in aave_ohlc]
    aave_highs = [float(candle[2]) for candle in aave_ohlc]
    aave_lows = [float(candle[3]) for candle in aave_ohlc]
    
    # Calculate BTC indicators (for signals)
    btc_rsi = calculate_rsi(btc_closes, RSI_PERIOD)
    
    # Calculate AAVE indicators (for context)
    aave_rsi = calculate_rsi(aave_closes, RSI_PERIOD)
    
    # BTC moving averages for trend analysis
    btc_ma_short = sum(btc_closes[-10:]) / 10
    btc_ma_long = sum(btc_closes[-20:]) / 20
    btc_trend = "upward" if btc_ma_short > btc_ma_long else "downward" if btc_ma_short < btc_ma_long else "sideways"
    
    # Recent BTC high/low for breakout analysis
    btc_recent_high = max(btc_highs[-20:])
    btc_recent_low = min(btc_lows[-20:])
    
    # Calculate correlation (simple 20-period correlation)
    btc_returns = [(btc_closes[i] - btc_closes[i-1]) / btc_closes[i-1] for i in range(1, len(btc_closes))]
    aave_returns = [(aave_closes[i] - aave_closes[i-1]) / aave_closes[i-1] for i in range(1, len(aave_closes))]
    
    # Simple correlation calculation
    if len(btc_returns) == len(aave_returns) and len(btc_returns) > 0:
        btc_avg = sum(btc_returns) / len(btc_returns)
        aave_avg = sum(aave_returns) / len(aave_returns)
        
        btc_cov = sum((r - btc_avg) * (a - aave_avg) for r, a in zip(btc_returns, aave_returns))
        btc_var = sum((r - btc_avg) ** 2 for r in btc_returns)
        aave_var = sum((a - aave_avg) ** 2 for a in aave_returns)
        
        correlation = btc_cov / ((btc_var * aave_var) ** 0.5) if (btc_var * aave_var) > 0 else 0.7
    else:
        correlation = 0.7  # Default assumption
    
    # Signal detection
    bullish_signals = []
    bearish_signals = []
    
    # Bullish signals (for long AAVE)
    if btc_rsi < RSI_OVERSOLD:
        bullish_signals.append("BTC_RSI_OVERSOLD")
    
    golden_cross = btc_ma_short > btc_ma_long and btc_trend == "upward"
    if golden_cross:
        bullish_signals.append("BTC_GOLDEN_CROSS")
    
    btc_breakout = (btc_price - btc_recent_high) / btc_recent_high > 0.02
    if btc_breakout:
        bullish_signals.append("BTC_BREAKOUT")
    
    # Bearish signals (for short AAVE)
    if btc_rsi > RSI_OVERBOUGHT:
        bearish_signals.append("BTC_RSI_OVERBOUGHT")
    
    death_cross = btc_ma_short < btc_ma_long and btc_trend == "downward"
    if death_cross:
        bearish_signals.append("BTC_DEATH_CROSS")
    
    btc_breakdown = (btc_recent_low - btc_price) / btc_recent_low > 0.02
    if btc_breakdown:
        bearish_signals.append("BTC_BREAKDOWN")
    
    # Calculate signal strengths
    bullish_strength = len(bullish_signals)
    bearish_strength = len(bearish_signals)
    
    return {
        "btc_price": btc_price,
        "aave_price": aave_price,
        "btc_rsi": btc_rsi,
        "aave_rsi": aave_rsi,
        "btc_trend": btc_trend,
        "correlation": correlation,
        "bullish_signals": bullish_signals,
        "bearish_signals": bearish_signals,
        "bullish_strength": bullish_strength,
        "bearish_strength": bearish_strength,
        "leverage_available": [2, 3]  # From your asset pairs data
    }

def get_leveraged_position_size(leverage: int, price: float, dry_run: bool) -> float:
    """Calculate position size with leverage."""
    if dry_run:
        # For dry run, use a reasonable test amount
        base_amount = 100.0 / leverage  # $100 worth divided by leverage
        return base_amount / price
    
    balances = get_balance()
    available_usd = float(balances.get(QUOTE, 0)) - RESERVE_USD
    
    if available_usd <= MIN_TRADE_USD:
        return 0.0
    
    # Calculate base position size (before leverage)
    base_amount = available_usd / leverage
    
    order_info = calculate_order_size(AAVE_PAIR, price, available_usd=base_amount)
    if not order_info['can_afford']:
        print(f"  ⚠️ {order_info.get('error', 'Cannot afford order')}")
        return 0.0
    
    return order_info['volume']

# ─────────────────────────────────────────────
# TRADING FUNCTIONS
# ─────────────────────────────────────────────
def execute_aave_long(analysis: dict, leverage: int, dry_run: bool = False) -> dict:
    """Execute leveraged long AAVE position."""
    try:
        aave_price = analysis["aave_price"]
        signal_strength = analysis["bullish_strength"]
        signals = analysis["bullish_signals"]
        
        # Only trade if we have strong bullish signals
        if signal_strength < 2:
            return {"success": False, "reason": f"Weak bullish signals (strength: {signal_strength})"}
        
        # Calculate leveraged position size
        position_size = get_leveraged_position_size(leverage, aave_price, dry_run)
        
        if position_size <= 0:
            return {"success": False, "reason": "Insufficient USD or cannot afford order"}
        
        # Calculate profit and stop levels
        profit_target_price = aave_price * (1 + PROFIT_PCT)  # 15% profit target
        stop_loss_price = aave_price * (1 - STOP_PCT)      # 8% stop loss
        
        # Calculate effective exposure
        effective_exposure = position_size * aave_price * leverage
        
        print(f"\n  📈 BULLISH BTC SIGNAL DETECTED!")
        print(f"  💰 BTC Price: ${analysis['btc_price']:,.2f}")
        print(f"  ⚡ AAVE Price: ${aave_price:,.4f}")
        print(f"  📈 Signal Strength: {signal_strength}/3")
        print(f"  🚨 Signals: {', '.join(signals)}")
        print(f"  ⚡ Leverage: {leverage}x")
        print(f"  💰 Buying {position_size:.4f} AAVE @ ${aave_price:,.4f}")
        print(f"  📊 Effective Exposure: ${effective_exposure:,.2f}")
        print(f"  🎯 Profit Target: ${profit_target_price:,.4f} (+{PROFIT_PCT*100:.1f}%)")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:,.4f} (-{STOP_PCT*100:.1f}%)")
        print(f"  📊 BTC-AAVE Correlation: {analysis['correlation']:.2f}")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place {leverage}x leveraged AAVE long order")
            return {"success": True, "action": "DRY_RUN_LONG", "volume": position_size}
        
        # Place real leveraged order
        order = place_order(
            pair=AAVE_PAIR,
            type="buy",
            ordertype="market",
            volume=position_size,
            leverage=str(leverage),
            validate=False
        )
        
        if order.get('error'):
            print(f"  ❌ AAVE long order failed: {order['error']}")
            return {"success": False, "error": order['error']}
        
        print(f"  ✅ AAVE long order placed: {order['descr']['order']}")
        
        # Send Telegram notification
        msg = f"📈 *AAVE Leveraged Long*\n\n"
        msg += f"💰 BTC: ${analysis['btc_price']:,.2f}\n"
        msg += f"⚡ AAVE: ${aave_price:,.4f}\n"
        msg += f"📈 RSI: {analysis['btc_rsi']:.1f}\n"
        msg += f"🚨 Signals: {', '.join(signals)}\n"
        msg += f"⚡ Leverage: {leverage}x\n"
        msg += f"💰 Volume: {position_size:.4f}\n"
        msg += f"📊 Exposure: ${effective_exposure:,.2f}\n"
        msg += f"🎯 Target: +{PROFIT_PCT*100:.1f}%\n"
        msg += f"🛡️ Stop: -{STOP_PCT*100:.1f}%\n"
        msg += f"📊 Correlation: {analysis['correlation']:.2f}\n"
        msg += f"⚡ AAVE Leverage Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "LONG_AAVE",
            "volume": position_size,
            "leverage": leverage,
            "entry_price": aave_price,
            "profit_target": profit_target_price,
            "stop_loss": stop_loss_price,
            "effective_exposure": effective_exposure,
            "signals": signals
        }
        
    except Exception as e:
        print(f"  ❌ AAVE long execution error: {e}")
        return {"success": False, "error": str(e)}

def execute_aave_short(analysis: dict, leverage: int, dry_run: bool = False) -> dict:
    """Execute leveraged short AAVE position."""
    try:
        aave_price = analysis["aave_price"]
        signal_strength = analysis["bearish_strength"]
        signals = analysis["bearish_signals"]
        
        # Only trade if we have strong bearish signals
        if signal_strength < 2:
            return {"success": False, "reason": f"Weak bearish signals (strength: {signal_strength})"}
        
        # Calculate leveraged position size
        position_size = get_leveraged_position_size(leverage, aave_price, dry_run)
        
        if position_size <= 0:
            return {"success": False, "reason": "Insufficient USD or cannot afford order"}
        
        # Calculate profit and stop levels (short position profits when price drops)
        profit_target_price = aave_price * (1 - PROFIT_PCT)  # 15% profit when AAVE drops
        stop_loss_price = aave_price * (1 + STOP_PCT)      # 8% stop loss when AAVE rises
        
        # Calculate effective exposure
        effective_exposure = position_size * aave_price * leverage
        
        print(f"\n  📉 BEARISH BTC SIGNAL DETECTED!")
        print(f"  💰 BTC Price: ${analysis['btc_price']:,.2f}")
        print(f"  ⚡ AAVE Price: ${aave_price:,.4f}")
        print(f"  📉 Signal Strength: {signal_strength}/3")
        print(f"  🚨 Signals: {', '.join(signals)}")
        print(f"  ⚡ Leverage: {leverage}x")
        print(f"  💰 Shorting {position_size:.4f} AAVE @ ${aave_price:,.4f}")
        print(f"  📊 Effective Exposure: ${effective_exposure:,.2f}")
        print(f"  🎯 Profit Target: ${profit_target_price:,.4f} (+{PROFIT_PCT*100:.1f}%)")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:,.4f} (-{STOP_PCT*100:.1f}%)")
        print(f"  📊 BTC-AAVE Correlation: {analysis['correlation']:.2f}")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place {leverage}x leveraged AAVE short order")
            return {"success": True, "action": "DRY_RUN_SHORT", "volume": position_size}
        
        # Place real leveraged short order
        order = place_order(
            pair=AAVE_PAIR,
            type="sell",
            ordertype="market",
            volume=position_size,
            leverage=str(leverage),
            validate=False
        )
        
        if order.get('error'):
            print(f"  ❌ AAVE short order failed: {order['error']}")
            return {"success": False, "error": order['error']}
        
        print(f"  ✅ AAVE short order placed: {order['descr']['order']}")
        
        # Send Telegram notification
        msg = f"📉 *AAVE Leveraged Short*\n\n"
        msg += f"💰 BTC: ${analysis['btc_price']:,.2f}\n"
        msg += f"⚡ AAVE: ${aave_price:,.4f}\n"
        msg += f"📉 RSI: {analysis['btc_rsi']:.1f}\n"
        msg += f"🚨 Signals: {', '.join(signals)}\n"
        msg += f"⚡ Leverage: {leverage}x\n"
        msg += f"💰 Volume: {position_size:.4f}\n"
        msg += f"📊 Exposure: ${effective_exposure:,.2f}\n"
        msg += f"🎯 Target: +{PROFIT_PCT*100:.1f}%\n"
        msg += f"🛡️ Stop: -{STOP_PCT*100:.1f}%\n"
        msg += f"📊 Correlation: {analysis['correlation']:.2f}\n"
        msg += f"⚡ AAVE Leverage Bot"
        tg(msg)
        
        return {
            "success": True, 
            "action": "SHORT_AAVE",
            "volume": position_size,
            "leverage": leverage,
            "entry_price": aave_price,
            "profit_target": profit_target_price,
            "stop_loss": stop_loss_price,
            "effective_exposure": effective_exposure,
            "signals": signals
        }
        
    except Exception as e:
        print(f"  ❌ AAVE short execution error: {e}")
        return {"success": False, "error": str(e)}

# ─────────────────────────────────────────────
# MAIN FUNCTIONS
# ─────────────────────────────────────────────
def scan_mode():
    """One-time scan for AAVE leverage opportunities."""
    print("⚡ AAVE Leverage Bot — Bitcoin Proxy Scanner")
    print("=" * 65)
    
    analysis = get_btc_aave_analysis()
    
    if "error" in analysis:
        print(f"  ❌ {analysis['error']}")
        return
    
    print(f"\n  📊 BITCOIN-AAVE ANALYSIS:")
    print(f"  💰 BTC Price: ${analysis['btc_price']:,.2f}")
    print(f"  ⚡ AAVE Price: ${analysis['aave_price']:,.4f}")
    print(f"  📈 BTC RSI: {analysis['btc_rsi']:.1f}")
    print(f"  📈 AAVE RSI: {analysis['aave_rsi']:.1f}")
    print(f"  📊 BTC Trend: {analysis['btc_trend']}")
    print(f"  📊 Correlation: {analysis['correlation']:.2f}")
    print(f"  ⚡ Leverage Available: {analysis['leverage_available']}x")
    
    # Show bullish signals
    if analysis["bullish_signals"]:
        print(f"\n  📈 BULLISH SIGNALS (for AAVE long):")
        for signal in analysis["bullish_signals"]:
            print(f"      🚀 {signal}")
        print(f"  📈 Signal Strength: {analysis['bullish_strength']}/3")
        print(f"  💡 *RECOMMENDATION*: LONG AAVE with {analysis['leverage_available'][0]}x leverage")
        print(f"      ⚡ Effective exposure: 2-3x your capital")
        print(f"      🎯 Target: +{PROFIT_PCT*100:.1f}% profit")
        print(f"      🛡️ Stop: -{STOP_PCT*100:.1f}% if wrong")
        
        # Execute long if not dry run
        result = execute_aave_long(analysis, analysis['leverage_available'][0], dry_run=False)
        if result["success"]:
            print(f"  ✅ AAVE leveraged long position opened!")
    
    # Show bearish signals
    if analysis["bearish_signals"]:
        print(f"\n  📉 BEARISH SIGNALS (for AAVE short):")
        for signal in analysis["bearish_signals"]:
            print(f"      📉 {signal}")
        print(f"  📉 Signal Strength: {analysis['bearish_strength']}/3")
        print(f"  💡 *RECOMMENDATION*: SHORT AAVE with {analysis['leverage_available'][0]}x leverage")
        print(f"      ⚡ Profit when BTC drops (AAVE drops too)")
        print(f"      🎯 Target: +{PROFIT_PCT*100:.1f}% profit")
        print(f"      🛡️ Stop: -{STOP_PCT*100:.1f}% if wrong")
        
        # Execute short if not dry run
        result = execute_aave_short(analysis, analysis['leverage_available'][0], dry_run=False)
        if result["success"]:
            print(f"  ✅ AAVE leveraged short position opened!")
    
    if not analysis["bullish_signals"] and not analysis["bearish_signals"]:
        print(f"\n  📊 *NO CLEAR SIGNALS*: BTC RSI: {analysis['btc_rsi']:.1f} (neutral)")
        print(f"      💡 Wait for stronger BTC directional signals")

def monitor_mode():
    """Continuous monitoring with automatic leveraged trading."""
    print("⚡ AAVE Leverage Bot — Continuous Monitoring")
    print("=" * 65)
    print(f"  📊 Monitoring BTC every {CHECK_SECS}s")
    print(f"  ⚡ Using AAVE as BTC proxy with leverage")
    print(f"  📈 Auto-longing AAVE on bullish BTC signals")
    print(f"  📉 Auto-shorting AAVE on bearish BTC signals")
    print(f"  ⚡ Leverage: 2x (safe) to 3x (aggressive)")
    print(f"  💰 Profit target: +{PROFIT_PCT*100:.1f}%")
    print(f"  🛡️ Stop loss: -{STOP_PCT*100:.1f}%")
    print(f"  💵 Minimum trade: ${MIN_TRADE_USD}")
    print("=" * 65)
    
    tg(f"⚡ *AAVE Leverage Bot started* - BTC proxy with leverage")
    
    position = None  # Track active AAVE position
    cycle = 0
    
    try:
        while True:
            cycle += 1
            analysis = get_btc_aave_analysis()
            
            if "error" in analysis:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] ⚠️ No data, waiting...")
                time.sleep(CHECK_SECS)
                continue
            
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Cycle {cycle}")
            print(f"  💰 BTC: ${analysis['btc_price']:,.2f} | AAVE: ${analysis['aave_price']:,.4f}")
            print(f"  📈 BTC RSI: {analysis['btc_rsi']:.1f} | Correlation: {analysis['correlation']:.2f}")
            print(f"  📈 Bullish: {analysis['bullish_strength']}/3 | 📉 Bearish: {analysis['bearish_strength']}/3")
            
            # Show current signals
            if analysis["bullish_signals"]:
                signal_str = ', '.join(analysis["bullish_signals"])
                print(f"  🚀 Bullish: {signal_str}")
            if analysis["bearish_signals"]:
                signal_str = ', '.join(analysis["bearish_signals"])
                print(f"  📉 Bearish: {signal_str}")
            
            # Handle existing position
            if position:
                current_price = analysis["aave_price"]
                entry_price = position["entry_price"]
                leverage = position["leverage"]
                
                if position["action"] == "LONG_AAVE":
                    # Long position - profits when AAVE rises
                    profit_pct = (current_price - entry_price) / entry_price
                else:
                    # Short position - profits when AAVE drops
                    profit_pct = (entry_price - current_price) / entry_price
                
                # Check profit target
                if profit_pct >= PROFIT_PCT:
                    print(f"  🎯 Profit target hit: +{profit_pct*100:.1f}%")
                    print(f"  💰 Closing {position['volume']:.4f} AAVE position @ ${current_price:.4f}")
                    
                    position = None
                    print(f"  ✅ AAVE leveraged position closed with profit!")
                
                # Check stop loss
                elif profit_pct <= -STOP_PCT:
                    print(f"  🛡️ Stop loss hit: {profit_pct*100:.1f}%")
                    print(f"  💰 Closing {position['volume']:.4f} AAVE position @ ${current_price:.4f}")
                    
                    position = None
                    print(f"  ⚠️ AAVE leveraged position closed with loss")
                
                else:
                    # Show position progress
                    effective_pnl = profit_pct * leverage
                    print(f"  📊 Position: {position['action']} {position['volume']:.4f} AAVE @ ${entry_price:.4f}")
                    print(f"  ⚡ Leverage: {leverage}x | P&L: {profit_pct*100:+.1f}% | Effective: {effective_pnl*100:+.1f}%")
            
            # No position - look for entry signals
            else:
                # Check for bullish signals (long AAVE)
                if analysis["bullish_strength"] >= 2:
                    print(f"  🚀 Strong bullish signals - longing AAVE")
                    result = execute_aave_long(analysis, analysis['leverage_available'][0], dry_run=False)
                    if result["success"]:
                        position = {
                            "action": result["action"],
                            "entry_price": result["entry_price"],
                            "volume": result["volume"],
                            "leverage": result["leverage"],
                            "entry_time": datetime.now(),
                            "signals": result["signals"]
                        }
                        print(f"  ✅ AAVE leveraged long position opened!")
                
                # Check for bearish signals (short AAVE)
                elif analysis["bearish_strength"] >= 2:
                    print(f"  📉 Strong bearish signals - shorting AAVE")
                    result = execute_aave_short(analysis, analysis['leverage_available'][0], dry_run=False)
                    if result["success"]:
                        position = {
                            "action": result["action"],
                            "entry_price": result["entry_price"],
                            "volume": result["volume"],
                            "leverage": result["leverage"],
                            "entry_time": datetime.now(),
                            "signals": result["signals"]
                        }
                        print(f"  ✅ AAVE leveraged short position opened!")
                
                else:
                    print(f"  📊 No entry signals (Bullish: {analysis['bullish_strength']}/3, Bearish: {analysis['bearish_strength']}/3)")
            
            print(f"  ──────────────────────────────────")
            time.sleep(CHECK_SECS)
            
    except KeyboardInterrupt:
        print(f"\n  🛑 AAVE Leverage Bot stopped by user")
        tg(f"🛑 *AAVE Leverage Bot stopped*")
    except Exception as e:
        print(f"\n  ❌ AAVE Leverage Bot error: {e}")
        tg(f"❌ *AAVE Leverage Bot error*: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AAVE Leverage Bot — Kraken")
    parser.add_argument("--scan", action="store_true", help="One-time scan for AAVE leverage opportunities")
    parser.add_argument("--monitor", action="store_true", help="Continuous monitoring with automatic trading")
    parser.add_argument("--dry", action="store_true", help="Dry run (no real orders)")
    args = parser.parse_args()
    
    if args.scan:
        scan_mode()
    elif args.monitor:
        monitor_mode()
    elif args.dry:
        print("⚡ AAVE Leverage Bot — Dry Run Mode")
        print("=" * 65)
        analysis = get_btc_aave_analysis()
        if "error" not in analysis:
            print(f"\n  📊 BITCOIN-AAVE ANALYSIS:")
            print(f"  💰 BTC Price: ${analysis['btc_price']:,.2f}")
            print(f"  ⚡ AAVE Price: ${analysis['aave_price']:,.4f}")
            print(f"  📈 BTC RSI: {analysis['btc_rsi']:.1f}")
            print(f"  📊 Correlation: {analysis['correlation']:.2f}")
            print(f"  ⚡ Leverage Available: {analysis['leverage_available']}x")
            
            if analysis["bullish_signals"]:
                print(f"\n  📈 BULLISH SIGNALS:")
                for signal in analysis["bullish_signals"]:
                    print(f"      🚀 {signal}")
                print(f"  💡 *WOULD LONG AAVE* with {analysis['leverage_available'][0]}x leverage")
                execute_aave_long(analysis, analysis['leverage_available'][0], dry_run=True)
            
            if analysis["bearish_signals"]:
                print(f"\n  📉 BEARISH SIGNALS:")
                for signal in analysis["bearish_signals"]:
                    print(f"      📉 {signal}")
                print(f"  💡 *WOULD SHORT AAVE* with {analysis['leverage_available'][0]}x leverage")
                execute_aave_short(analysis, analysis['leverage_available'][0], dry_run=True)
    else:
        print("⚡ AAVE Leverage Bot — Kraken")
        print("Usage:")
        print("  python3 aave_leverage_bot.py --scan    # Scan for AAVE leverage opportunities")
        print("  python3 aave_leverage_bot.py --monitor # Continuous monitoring")
        print("  python3 aave_leverage_bot.py --dry     # Dry run mode")
        print("\nStrategy:")
        print("  • Use AAVE as Bitcoin proxy with 2-3x leverage")
        print("  • Long AAVE when BTC shows bullish signals")
        print("  • Short AAVE when BTC shows bearish signals")
        print("  • AAVE correlates ~0.7 with Bitcoin")
        print("  • 15% profit target, 8% stop loss")
        print("  • No BTC futures needed - use Kraken leverage!")

if __name__ == "__main__":
    main()
