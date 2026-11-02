#!/usr/bin/env python3
"""
🎯 Meme Coin Scanner — Kraken
Scans for high-volatility meme coins with breakout potential.

Strategy:
- High Volatility Detection: RSI extremes + volume spikes
- Breakout Scanning: Price breaks recent ranges
- Momentum Analysis: Rapid price movements
- Pump Detection: Unusual trading patterns

Meme Coins Tracked:
- DOGE: Dogecoin - The original meme coin
- SHIB: Shiba Inu - Doge killer
- PEPE: Pepe - Frog meme phenomenon
- WIF: Dogwifhat - Solana meme king
- BONK: Bonk - Solana community meme
- FLOKI: Floki - Viking dog meme

Alerts when:
- RSI < 20 (extreme oversold - bounce potential)
- RSI > 80 (extreme overbought - pump potential)
- Volume > 5x average (unusual activity)
- Price breaks 24h high (breakout confirmed)
- 15min gain > 10% (rapid pump)

Usage:
    python3 meme_coin_scanner.py --scan          # scan all meme coins
    python3 meme_coin_scanner.py --asset DOGE   # scan specific meme
    python3 meme_coin_scanner.py --alert        # continuous monitoring mode

Requires kraken_connection.py in same folder.
.env needs: KRAKEN_API_KEY, KRAKEN_API_SECRET
"""

import os
import time
import argparse
from datetime import datetime, timedelta
from kraken_connection import (
    get_ticker, get_ohlc, get_balance, get_orderbook, 
    get_open_orders, cancel_order, get_asset_pairs
)

# ─────────────────────────────────────────────
# MEME COIN CONFIGURATION
# ─────────────────────────────────────────────
MEME_COINS = {
    'DOGE': {
        'name': 'Dogecoin',
        'pair': 'XDGUSD',
        'description': 'The original meme coin',
        'volatility_threshold': 0.08,
        'volume_multiplier': 3.0,
        'pump_threshold': 0.15  # 15% in 15min
    },
    'SHIB': {
        'name': 'Shiba Inu',
        'pair': 'SHIBUSD',
        'description': 'Doge killer meme',
        'volatility_threshold': 0.10,
        'volume_multiplier': 4.0,
        'pump_threshold': 0.20  # 20% in 15min
    },
    'PEPE': {
        'name': 'Pepe',
        'pair': 'PEPEUSD',
        'description': 'Frog meme phenomenon',
        'volatility_threshold': 0.12,
        'volume_multiplier': 5.0,
        'pump_threshold': 0.25  # 25% in 15min
    },
    'WIF': {
        'name': 'Dogwifhat',
        'pair': 'WIFUSD',
        'description': 'Solana meme king',
        'volatility_threshold': 0.15,
        'volume_multiplier': 6.0,
        'pump_threshold': 0.30  # 30% in 15min
    },
    'BONK': {
        'name': 'Bonk',
        'pair': 'BONKUSD',
        'description': 'Solana community meme',
        'volatility_threshold': 0.14,
        'volume_multiplier': 5.0,
        'pump_threshold': 0.25  # 25% in 15min
    },
    'FLOKI': {
        'name': 'Floki',
        'pair': 'FLOKIUSD',
        'description': 'Viking dog meme',
        'volatility_threshold': 0.09,
        'volume_multiplier': 3.5,
        'pump_threshold': 0.18  # 18% in 15min
    }
}

# Analysis parameters
RSI_PERIOD = 14
RSI_OVERSOLD_EXTREME = 20    # Extreme oversold for meme coins
RSI_OVERBOUGHT_EXTREME = 80   # Extreme overbought for meme coins
VOLUME_SPIKE_MULTIPLIER = 5.0     # 5x average volume = unusual
BREAKOUT_THRESHOLD = 0.05           # 5% above 24h high = breakout
PUMP_TIMEFRAME = 15                # 15 minutes for rapid pump detection
SCAN_INTERVAL = 60                  # seconds between scans

# Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

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

def analyze_meme_coin(symbol: str, coin_info: dict) -> dict:
    """Analyze a specific meme coin for pump/dump signals."""
    pair = coin_info['pair']
    
    try:
        # Get current ticker data
        ticker = get_ticker(pair)
        if not ticker:
            return {"error": f"No ticker data for {symbol}"}
        
        current_price = float(ticker.get("c", [0])[0])  # last trade price
        volume_24h = float(ticker.get("v", [0])[0])  # 24h volume
        
        # Get OHLC data for analysis
        ohlc = get_ohlc(pair, interval=15)  # 15-minute candles
        if not ohlc or len(ohlc) < 20:
            return {"error": f"Insufficient OHLC data for {symbol}"}
        
        # Extract price and volume data
        closes = [float(candle[4]) for candle in ohlc]
        highs = [float(candle[2]) for candle in ohlc]
        lows = [float(candle[3]) for candle in ohlc]
        volumes = [float(candle[6]) for candle in ohlc]
        
        # Calculate indicators
        rsi = calculate_rsi(closes, RSI_PERIOD)
        recent_high = max(highs[-16:])  # 4-hour high
        recent_low = min(lows[-16:])   # 4-hour low
        avg_volume = sum(volumes[-20:]) / 20  # 5-hour average volume
        current_volume = volumes[-1]  # Current 15min volume
        
        # Calculate recent price changes
        price_1h_ago = closes[-4] if len(closes) >= 4 else closes[0]
        price_15min_ago = closes[-2] if len(closes) >= 2 else closes[0]
        
        change_1h = (current_price - price_1h_ago) / price_1h_ago if price_1h_ago > 0 else 0
        change_15min = (current_price - price_15min_ago) / price_15min_ago if price_15min_ago > 0 else 0
        
        # Detect signals
        signals = []
        
        # 1. Extreme RSI signals
        if rsi < RSI_OVERSOLD_EXTREME:
            signals.append("EXTREME_OVERSOLD")
        elif rsi > RSI_OVERBOUGHT_EXTREME:
            signals.append("EXTREME_OVERBOUGHT")
        
        # 2. Volume spike detection
        volume_spike = current_volume > (avg_volume * VOLUME_SPIKE_MULTIPLIER)
        if volume_spike:
            signals.append("VOLUME_SPIKE")
        
        # 3. Breakout detection
        breakout = current_price > (recent_high * (1 + BREAKOUT_THRESHOLD))
        if breakout:
            signals.append("BREAKOUT")
        
        # 4. Rapid pump detection (15min gain)
        rapid_pump = change_15min > coin_info['pump_threshold']
        if rapid_pump:
            signals.append("RAPID_PUMP")
        
        # 5. High volatility detection
        volatility = (max(closes[-20:]) - min(closes[-20:])) / recent_high
        high_volatility = volatility > coin_info['volatility_threshold']
        if high_volatility:
            signals.append("HIGH_VOLATILITY")
        
        # Calculate signal strength (0-10)
        signal_strength = 0
        if "EXTREME_OVERSOLD" in signals:
            signal_strength += 3
        if "VOLUME_SPIKE" in signals:
            signal_strength += 2
        if "BREAKOUT" in signals:
            signal_strength += 2
        if "RAPID_PUMP" in signals:
            signal_strength += 3
        if "HIGH_VOLATILITY" in signals:
            signal_strength += 1
        
        return {
            "symbol": symbol,
            "name": coin_info['name'],
            "pair": pair,
            "current_price": current_price,
            "rsi": rsi,
            "volume_24h": volume_24h,
            "current_volume": current_volume,
            "avg_volume": avg_volume,
            "change_1h": change_1h,
            "change_15min": change_15min,
            "volatility": volatility,
            "signals": signals,
            "signal_strength": signal_strength,
            "description": coin_info['description']
        }
        
    except Exception as e:
        return {"error": f"Analysis error for {symbol}: {e}"}

def scan_all_meme_coins() -> list:
    """Scan all configured meme coins."""
    print("  🔍 Scanning meme coins for pump/dump signals...")
    
    results = []
    
    for symbol, coin_info in MEME_COINS.items():
        print(f"  🔍 Analyzing {coin_info['name']} ({symbol})...")
        
        analysis = analyze_meme_coin(symbol, coin_info)
        
        if "error" in analysis:
            print(f"  ❌ {analysis['error']}")
            continue
        
        # Only include coins with signals
        if analysis["signal_strength"] > 0:
            results.append(analysis)
    
    # Sort by signal strength
    results.sort(key=lambda x: x["signal_strength"], reverse=True)
    
    return results

def print_meme_results(results: list):
    """Print meme coin scan results in a nice format."""
    if not results:
        print("  📊 No meme coin signals detected")
        return
    
    print(f"\n  🎯 MEME COIN SIGNALS:")
    print(f"  " + "="*90)
    
    for i, result in enumerate(results, 1):
        symbol = result["symbol"]
        name = result["name"]
        price = result["current_price"]
        rsi = result["rsi"]
        strength = result["signal_strength"]
        signals = result["signals"]
        
        # Signal strength emoji
        if strength >= 7:
            strength_emoji = "🔥🔥"
        elif strength >= 5:
            strength_emoji = "🔥"
        elif strength >= 3:
            strength_emoji = "📈"
        else:
            strength_emoji = "📊"
        
        print(f"  {i}. {strength_emoji} {symbol:<4} | ${price:<10.6f} | RSI: {rsi:<3.0f} | Strength: {strength}")
        print(f"      🚀 {name}")
        print(f"      📝 {result['description']}")
        print(f"      📊 1h: {result['change_1h']*100:+.1f}% | 15min: {result['change_15min']*100:+.1f}%")
        print(f"      📈 Vol: {result['volatility']:.3f} | Vol 24h: {result['volume_24h']:,.0f}")
        print(f"      🎯 Signals: {', '.join(signals)}")
        
        # Trading recommendation
        if "EXTREME_OVERSOLD" in signals:
            print(f"      💡 *BUY OPPORTUNITY*: Extreme oversold - potential bounce!")
        elif "RAPID_PUMP" in signals:
            print(f"      🚀 *PUMP DETECTED*: Rapid price increase - consider taking profits!")
        elif "BREAKOUT" in signals:
            print(f"      📈 *BREAKOUT*: Price broke resistance - continuation likely!")
        elif "VOLUME_SPIKE" in signals:
            print(f"      📊 *VOLUME SPIKE*: Unusual trading activity - big move coming!")

def send_meme_alerts(results: list):
    """Send Telegram alerts for strong meme coin signals."""
    if not results:
        return
    
    # Only alert for strong signals (strength >= 5)
    strong_signals = [r for r in results if r["signal_strength"] >= 5]
    
    if not strong_signals:
        return
    
    alert_msg = f"🚀 *Meme Coin Alerts*\n\n"
    
    for result in strong_signals[:3]:  # Top 3 signals
        symbol = result["symbol"]
        name = result["name"]
        price = result["current_price"]
        strength = result["signal_strength"]
        signals = result["signals"]
        
        alert_msg += f"🔥 {symbol} ({name})\n"
        alert_msg += f"💰 ${price:.6f}\n"
        alert_msg += f"📊 Strength: {strength}/10\n"
        alert_msg += f"🎯 {', '.join(signals)}\n\n"
    
    alert_msg += f"🤖 *Meme Scanner* | {datetime.now().strftime('%H:%M')}"
    
    tg(alert_msg)

# ─────────────────────────────────────────────
# MAIN FUNCTIONS
# ─────────────────────────────────────────────
def scan_mode():
    """Run one-time scan of all meme coins."""
    print("🎯 Meme Coin Scanner — One-Time Scan")
    print("=" * 50)
    
    results = scan_all_meme_coins()
    print_meme_results(results)
    
    if results:
        send_meme_alerts(results)
    
    print(f"\n  ✅ Meme coin scan complete! Found {len(results)} signals")

def alert_mode():
    """Run continuous monitoring mode."""
    print("🎯 Meme Coin Scanner — Continuous Monitoring")
    print("=" * 50)
    print(f"  📊 Monitoring {len(MEME_COINS)} meme coins every {SCAN_INTERVAL}s")
    print(f"  🚀 Alerting on signals strength >= 5")
    print("  Press Ctrl+C to stop")
    print("=" * 50)
    
    tg(f"🚀 *Meme Scanner started* - Monitoring {len(MEME_COINS)} coins")
    
    try:
        while True:
            results = scan_all_meme_coins()
            
            # Only show results if there are signals
            if results:
                timestamp = datetime.now().strftime('%H:%M:%S')
                print(f"\n  [{timestamp}] 🚨 MEME COIN ALERTS!")
                print_meme_results(results)
                send_meme_alerts(results)
            else:
                timestamp = datetime.now().strftime('%H:%M:%S')
                print(f"  [{timestamp}] 📊 No signals - monitoring...")
            
            time.sleep(SCAN_INTERVAL)
            
    except KeyboardInterrupt:
        print(f"\n  🛑 Meme Scanner stopped by user")
        tg(f"🛑 *Meme Scanner stopped*")

def asset_mode(asset: str):
    """Scan specific meme coin."""
    if asset.upper() not in MEME_COINS:
        print(f"  ❌ Asset {asset} not found. Available: {', '.join(MEME_COINS.keys())}")
        return
    
    coin_info = MEME_COINS[asset.upper()]
    print(f"🎯 Meme Coin Scanner — {coin_info['name']} ({asset})")
    print("=" * 50)
    
    print(f"  📝 {coin_info['description']}")
    print(f"  📊 Pair: {coin_info['pair']}")
    print(f"  🎯 Volatility Threshold: {coin_info['volatility_threshold']*100:.1f}%")
    print(f"  🚀 Pump Threshold: {coin_info['pump_threshold']*100:.1f}% in 15min")
    print("=" * 50)
    
    analysis = analyze_meme_coin(asset.upper(), coin_info)
    
    if "error" in analysis:
        print(f"  ❌ {analysis['error']}")
        return
    
    # Print detailed analysis
    print(f"\n  📊 {coin_info['name']} Analysis:")
    print(f"  💰 Current Price: ${analysis['current_price']:.6f}")
    print(f"  📈 RSI: {analysis['rsi']:.1f}")
    print(f"  📊 1h Change: {analysis['change_1h']*100:+.1f}%")
    print(f"  📊 15min Change: {analysis['change_15min']*100:+.1f}%")
    print(f"  📊 Volatility: {analysis['volatility']:.3f}")
    print(f"  📊 Volume 24h: {analysis['volume_24h']:,.0f}")
    print(f"  📊 Current Volume: {analysis['current_volume']:,.0f}")
    print(f"  📊 Avg Volume: {analysis['avg_volume']:,.0f}")
    
    if analysis["signals"]:
        print(f"\n  🎯 Signals Detected:")
        for signal in analysis["signals"]:
            print(f"      🚨 {signal}")
        print(f"\n  📊 Signal Strength: {analysis['signal_strength']}/10")
        
        # Trading recommendation
        if "EXTREME_OVERSOLD" in analysis["signals"]:
            print(f"\n  💡 *RECOMMENDATION*: BUY - Extreme oversold condition!")
            print(f"      🎯 Entry: Current price ${analysis['current_price']:.6f}")
            print(f"      🛡️ Stop Loss: -15% from entry")
            print(f"      🎯 Take Profit: +20% from entry")
        elif "RAPID_PUMP" in analysis["signals"]:
            print(f"\n  💡 *RECOMMENDATION*: SELL - Rapid pump detected!")
            print(f"      🎯 Take profits now before dump!")
        elif "BREAKOUT" in analysis["signals"]:
            print(f"\n  💡 *RECOMMENDATION*: BUY - Breakout confirmed!")
            print(f"      🎯 Entry: Current price ${analysis['current_price']:.6f}")
            print(f"      🛡️ Stop Loss: -10% from entry")
            print(f"      🎯 Take Profit: +25% from entry")
    else:
        print(f"\n  📊 No signals detected (strength: 0)")
    
    # Send alert if strong signal
    if analysis["signal_strength"] >= 5:
        alert_msg = f"🚨 *{asset} Alert*\n\n"
        alert_msg += f"📊 {coin_info['name']}\n"
        alert_msg += f"💰 ${analysis['current_price']:.6f}\n"
        alert_msg += f"🎯 Strength: {analysis['signal_strength']}/10\n"
        alert_msg += f"🚨 {', '.join(analysis['signals'])}\n"
        alert_msg += f"🤖 Meme Scanner | {datetime.now().strftime('%H:%M')}"
        tg(alert_msg)
    
    print(f"\n  ✅ {asset} analysis complete!")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Meme Coin Scanner — Kraken")
    parser.add_argument("--scan", action="store_true", help="One-time scan of all meme coins")
    parser.add_argument("--asset", type=str, help="Analyze specific meme coin")
    parser.add_argument("--alert", action="store_true", help="Continuous monitoring mode with alerts")
    args = parser.parse_args()
    
    if args.scan:
        scan_mode()
    elif args.asset:
        asset_mode(args.asset)
    elif args.alert:
        alert_mode()
    else:
        print("🎯 Meme Coin Scanner — Kraken")
        print("Usage:")
        print("  python3 meme_coin_scanner.py --scan          # Scan all meme coins")
        print("  python3 meme_coin_scanner.py --asset DOGE   # Analyze DOGE")
        print("  python3 meme_coin_scanner.py --alert        # Continuous monitoring")
        print("\nAvailable meme coins:", ", ".join(MEME_COINS.keys()))

if __name__ == "__main__":
    main()
