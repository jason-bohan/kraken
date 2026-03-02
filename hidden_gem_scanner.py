#!/usr/bin/env python3
"""
💎 Hidden Gem Scanner — Kraken
Scans for undervalued meme coins that haven't mooned yet.

Strategy:
- LOW MARKET CAP: Focus on coins under $50M market cap
- LARGE ORDERS: Detect whale accumulation (big buy orders)
- LOW VOLATILITY: Coins that haven't exploded yet (stable base)
- VOLUME BUILDUP: Increasing volume without price explosion
- TECHNICAL BOTTOMS: RSI oversold + support levels

Hidden Gems Criteria:
- Market Cap < $50M (undiscovered)
- Large buy orders > 100M USD
- Volume increasing 3x average
- Price stable (no recent pumps)
- RSI < 40 (oversold but not crashed)

These are the "before the moon" coins that could 100x!

Usage:
    python3 hidden_gem_scanner.py --scan          # Scan for hidden gems
    python3 hidden_gem_scanner.py --monitor       # Continuous monitoring
    python3 hidden_gem_scanner.py --asset COIN    # Analyze specific coin

Requires kraken_connection.py in same folder.
.env needs: KRAKEN_API_KEY, KRAKEN_API_SECRET
"""

import os
import time
import argparse
from datetime import datetime, timedelta
from kraken_connection import (
    get_balance, get_ticker, get_ohlc, get_orderbook, 
    get_open_orders, cancel_order, get_asset_pairs
)

# ─────────────────────────────────────────────
# HIDDEN GEM CONFIGURATION
# ─────────────────────────────────────────────
# Potential hidden gems (low cap, high potential)
HIDDEN_GEMS = {
    'FLOKI': {
        'name': 'Floki',
        'pair': 'FLOKIUSD',
        'description': 'Viking dog meme - still early',
        'max_market_cap': 50000000,  # $50M max
        'min_order_size': 100000000,   # $100M min whale orders
        'volume_multiplier': 3.0,      # 3x volume increase
        'max_price_change': 0.10,      # 10% max daily change (no pumps yet)
        'rsi_oversold': 40            # RSI < 40 (oversold but not crashed)
    },
    'BABYDOGE': {
        'name': 'Baby Doge',
        'pair': 'BABYDOGEUSD',
        'description': 'Baby version of DOGE - huge potential',
        'max_market_cap': 30000000,  # $30M max
        'min_order_size': 50000000,    # $50M min whale orders
        'volume_multiplier': 2.5,      # 2.5x volume increase
        'max_price_change': 0.08,      # 8% max daily change
        'rsi_oversold': 35
    },
    'KISHU': {
        'name': 'Kishu Inu',
        'pair': 'KISHUUSD',
        'description': 'Japanese dog meme - very early stage',
        'max_market_cap': 20000000,  # $20M max
        'min_order_size': 30000000,    # $30M min whale orders
        'volume_multiplier': 4.0,      # 4x volume increase
        'max_price_change': 0.05,      # 5% max daily change
        'rsi_oversold': 30
    },
    'HOKK': {
        'name': 'Hokkaidu',
        'pair': 'HOKKUSD',
        'description': 'Hokk dog meme - undiscovered',
        'max_market_cap': 15000000,  # $15M max
        'min_order_size': 25000000,    # $25M min whale orders
        'volume_multiplier': 3.5,      # 3.5x volume increase
        'max_price_change': 0.06,      # 6% max daily change
        'rsi_oversold': 32
    },
    'AKITA': {
        'name': 'Akita Inu',
        'pair': 'AKITAUSD',
        'description': 'Japanese dog breed meme - early',
        'max_market_cap': 25000000,  # $25M max
        'min_order_size': 40000000,    # $40M min whale orders
        'volume_multiplier': 2.8,      # 2.8x volume increase
        'max_price_change': 0.07,      # 7% max daily change
        'rsi_oversold': 38
    },
    'SAMO': {
        'name': 'Samoyed',
        'pair': 'SAMOUSD',
        'description': 'Fluffy dog meme - very early',
        'max_market_cap': 18000000,  # $18M max
        'min_order_size': 35000000,    # $35M min whale orders
        'volume_multiplier': 3.2,      # 3.2x volume increase
        'max_price_change': 0.04,      # 4% max daily change
        'rsi_oversold': 33
    }
}

# Analysis parameters
RSI_PERIOD = 14
VOLUME_BUILDUP_DAYS = 7          # Check volume over 7 days
PRICE_STABILITY_DAYS = 14         # Check price stability over 2 weeks
LARGE_ORDER_THRESHOLD = 0.80       # 80% of orderbook depth
SCAN_INTERVAL = 120               # seconds between scans

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

def estimate_market_cap(price: float, symbol: str) -> float:
    """Estimate market cap (rough approximation)."""
    # These are rough circulating supply estimates
    supply_estimates = {
        'FLOKI': 967000000000,      # 967B
        'BABYDOGE': 420000000000000, # 420T
        'KISHU': 100000000000000,    # 100T
        'HOKK': 100000000000000,     # 100T
        'AKITA': 100000000000000,     # 100T
        'SAMO': 100000000000000      # 100T
    }
    
    supply = supply_estimates.get(symbol.upper(), 100000000000)  # Default 100B
    return price * supply

def analyze_large_orders(pair: str) -> dict:
    """Analyze orderbook for large whale orders."""
    try:
        orderbook = get_orderbook(pair)
        if not orderbook:
            return {"error": "No orderbook data"}
        
        # Get buy orders (bids)
        bids = orderbook.get('bids', [])
        if not bids:
            return {"error": "No buy orders"}
        
        # Calculate total buy order value
        total_buy_value = 0.0
        large_orders = []
        
        for bid in bids[:20]:  # Top 20 buy orders
            price = float(bid[0])
            volume = float(bid[1])
            order_value = price * volume
            total_buy_value += order_value
            
            if order_value > 1000000:  # Orders > $1M
                large_orders.append({
                    'price': price,
                    'volume': volume,
                    'value': order_value
                })
        
        # Get sell orders (asks) for context
        asks = orderbook.get('asks', [])
        total_sell_value = sum(float(ask[0]) * float(ask[1]) for ask in asks[:20])
        
        return {
            'total_buy_value': total_buy_value,
            'total_sell_value': total_sell_value,
            'large_orders': large_orders,
            'buy_sell_ratio': total_buy_value / (total_sell_value + 1),
            'order_depth': len(bids) + len(asks)
        }
        
    except Exception as e:
        return {"error": f"Orderbook analysis error: {e}"}

def analyze_hidden_gem(symbol: str, gem_info: dict) -> dict:
    """Analyze a specific hidden gem for moon potential."""
    pair = gem_info['pair']
    
    try:
        # Get current ticker data
        ticker = get_ticker(pair)
        if not ticker:
            return {"error": f"No ticker data for {symbol}"}
        
        current_price = float(ticker.get("c", [0])[0])
        volume_24h = float(ticker.get("v", [0])[0])
        
        # Get OHLC data for analysis
        ohlc = get_ohlc(pair, interval=60)  # 1-hour candles
        if not ohlc or len(ohlc) < 50:
            return {"error": f"Insufficient OHLC data for {symbol}"}
        
        # Extract price and volume data
        closes = [float(candle[4]) for candle in ohlc]
        highs = [float(candle[2]) for candle in ohlc]
        lows = [float(candle[3]) for candle in ohlc]
        volumes = [float(candle[6]) for candle in ohlc]
        
        # Calculate indicators
        rsi = calculate_rsi(closes, RSI_PERIOD)
        
        # Price stability analysis (no recent pumps)
        recent_closes = closes[-PRICE_STABILITY_DAYS:]
        price_volatility = (max(recent_closes) - min(recent_closes)) / min(recent_closes)
        max_daily_change = max(abs((closes[i] - closes[i-1]) / closes[i-1]) for i in range(1, len(closes)))
        
        # Volume buildup analysis
        recent_volumes = volumes[-VOLUME_BUILDUP_DAYS:]
        avg_volume = sum(recent_volumes) / len(recent_volumes)
        current_volume = volumes[-1]
        volume_increase = current_volume / avg_volume if avg_volume > 0 else 1
        
        # Market cap estimation
        market_cap = estimate_market_cap(current_price, symbol)
        
        # Large order analysis
        order_analysis = analyze_large_orders(pair)
        
        # Hidden gem criteria checks
        criteria_met = []
        
        # 1. Low market cap
        if market_cap <= gem_info['max_market_cap']:
            criteria_met.append("LOW_MARKET_CAP")
        
        # 2. Large whale orders
        if not order_analysis.get("error") and order_analysis['total_buy_value'] >= gem_info['min_order_size']:
            criteria_met.append("LARGE_ORDERS")
        
        # 3. Volume buildup
        if volume_increase >= gem_info['volume_multiplier']:
            criteria_met.append("VOLUME_BUILDUP")
        
        # 4. Price stability (no pumps yet)
        if max_daily_change <= gem_info['max_price_change']:
            criteria_met.append("PRICE_STABILITY")
        
        # 5. Oversold but not crashed
        if rsi <= gem_info['rsi_oversold']:
            criteria_met.append("RSI_OVERSOLD")
        
        # Calculate hidden gem score (0-10)
        gem_score = 0
        if "LOW_MARKET_CAP" in criteria_met:
            gem_score += 2
        if "LARGE_ORDERS" in criteria_met:
            gem_score += 3  # Most important
        if "VOLUME_BUILDUP" in criteria_met:
            gem_score += 2
        if "PRICE_STABILITY" in criteria_met:
            gem_score += 2
        if "RSI_OVERSOLD" in criteria_met:
            gem_score += 1
        
        return {
            "symbol": symbol,
            "name": gem_info['name'],
            "pair": pair,
            "current_price": current_price,
            "market_cap": market_cap,
            "rsi": rsi,
            "volume_24h": volume_24h,
            "volume_increase": volume_increase,
            "price_volatility": price_volatility,
            "max_daily_change": max_daily_change,
            "order_analysis": order_analysis,
            "criteria_met": criteria_met,
            "gem_score": gem_score,
            "description": gem_info['description']
        }
        
    except Exception as e:
        return {"error": f"Analysis error for {symbol}: {e}"}

def scan_hidden_gems() -> list:
    """Scan all hidden gems for moon potential."""
    print("  💎 Scanning hidden gems for moon potential...")
    
    results = []
    
    for symbol, gem_info in HIDDEN_GEMS.items():
        print(f"  🔍 Analyzing {gem_info['name']} ({symbol})...")
        
        analysis = analyze_hidden_gem(symbol, gem_info)
        
        if "error" in analysis:
            print(f"  ❌ {analysis['error']}")
            continue
        
        # Only include gems with score >= 5
        if analysis["gem_score"] >= 5:
            results.append(analysis)
    
    # Sort by gem score
    results.sort(key=lambda x: x["gem_score"], reverse=True)
    
    return results

def print_hidden_gem_results(results: list):
    """Print hidden gem scan results."""
    if not results:
        print("  💎 No hidden gems found - all coins have already mooned!")
        return
    
    print(f"\n  💎 HIDDEN GEMS DETECTED:")
    print(f"  " + "="*100)
    
    for i, result in enumerate(results, 1):
        symbol = result["symbol"]
        name = result["name"]
        price = result["current_price"]
        market_cap = result["market_cap"]
        gem_score = result["gem_score"]
        criteria = result["criteria_met"]
        
        # Gem score emoji
        if gem_score >= 8:
            score_emoji = "💎💎💎"
        elif gem_score >= 6:
            score_emoji = "💎💎"
        else:
            score_emoji = "💎"
        
        print(f"  {i}. {score_emoji} {symbol:<8} | ${price:<10.8f} | MC: ${market_cap/1000000:.1f}M | Score: {gem_score}/10")
        print(f"      🚀 {name}")
        print(f"      📝 {result['description']}")
        print(f"      📊 Volume: {result['volume_24h']:,.0f} | RSI: {result['rsi']:.0f}")
        print(f"      📈 Volume Increase: {result['volume_increase']:.1f}x")
        print(f"      📊 Price Volatility: {result['price_volatility']*100:.1f}%")
        
        # Show criteria met
        criteria_str = ', '.join(criteria).replace('_', ' ')
        print(f"      ✅ Criteria: {criteria_str}")
        
        # Show large orders if available
        if not result["order_analysis"].get("error"):
            order_analysis = result["order_analysis"]
            large_orders = order_analysis.get("large_orders", [])
            if large_orders:
                print(f"      🐋 Large Orders: {len(large_orders)} whale orders detected")
                for order in large_orders[:3]:  # Top 3 largest
                    print(f"         💰 ${order['value']:,.0f} @ ${order['price']:.8f}")
        
        # Moon potential assessment
        if gem_score >= 8:
            print(f"      🚀 *EXTREME MOON POTENTIAL*: This coin could 100x!")
        elif gem_score >= 6:
            print(f"      🌙 *HIGH MOON POTENTIAL*: Strong 50x potential!")
        else:
            print(f"      🌕 *MODERATE MOON POTENTIAL*: Good 10-20x potential!")
        
        print(f"      💡 *RECOMMENDATION*: Accumulate now before moon!")
        print()

def send_hidden_gem_alerts(results: list):
    """Send Telegram alerts for high-potential hidden gems."""
    if not results:
        return
    
    # Only alert for gems with score >= 7
    high_potential = [r for r in results if r["gem_score"] >= 7]
    
    if not high_potential:
        return
    
    alert_msg = f"💎 *Hidden Gem Alert*\n\n"
    
    for result in high_potential[:3]:  # Top 3 gems
        symbol = result["symbol"]
        name = result["name"]
        price = result["current_price"]
        gem_score = result["gem_score"]
        market_cap = result["market_cap"]
        
        alert_msg += f"💎 {symbol} ({name})\n"
        alert_msg += f"💰 ${price:.8f}\n"
        alert_msg += f"📊 MC: ${market_cap/1000000:.1f}M\n"
        alert_msg += f"🌙 Score: {gem_score}/10\n"
        alert_msg += f"🚨 {', '.join(result['criteria_met'])}\n\n"
    
    alert_msg += f"🤖 *Hidden Gem Scanner* | {datetime.now().strftime('%H:%M')}"
    
    tg(alert_msg)

# ─────────────────────────────────────────────
# MAIN FUNCTIONS
# ─────────────────────────────────────────────
def scan_mode():
    """Run one-time scan of all hidden gems."""
    print("💎 Hidden Gem Scanner — One-Time Scan")
    print("=" * 60)
    
    results = scan_hidden_gems()
    print_hidden_gem_results(results)
    
    if results:
        send_hidden_gem_alerts(results)
    
    print(f"\n  ✅ Hidden gem scan complete! Found {len(results)} potential mooners")

def monitor_mode():
    """Run continuous monitoring mode."""
    print("💎 Hidden Gem Scanner — Continuous Monitoring")
    print("=" * 60)
    print(f"  📊 Monitoring {len(HIDDEN_GEMS)} hidden gems every {SCAN_INTERVAL}s")
    print(f"  🚨 Alerting on gems with score >= 7")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    
    tg(f"💎 *Hidden Gem Scanner started* - Monitoring {len(HIDDEN_GEMS)} coins")
    
    try:
        while True:
            results = scan_hidden_gems()
            
            # Only show results if we have high-potential gems
            high_potential = [r for r in results if r["gem_score"] >= 7]
            
            if high_potential:
                timestamp = datetime.now().strftime('%H:%M:%S')
                print(f"\n  [{timestamp}] 💎 HIDDEN GEM ALERTS!")
                print_hidden_gem_results(high_potential)
                send_hidden_gem_alerts(high_potential)
            else:
                timestamp = datetime.now().strftime('%H:%M:%S')
                print(f"  [{timestamp}] 📊 No hidden gems found - monitoring...")
            
            time.sleep(SCAN_INTERVAL)
            
    except KeyboardInterrupt:
        print(f"\n  🛑 Hidden Gem Scanner stopped by user")
        tg(f"🛑 *Hidden Gem Scanner stopped*")

def asset_mode(asset: str):
    """Analyze specific hidden gem."""
    if asset.upper() not in HIDDEN_GEMS:
        print(f"  ❌ Asset {asset} not found. Available: {', '.join(HIDDEN_GEMS.keys())}")
        return
    
    gem_info = HIDDEN_GEMS[asset.upper()]
    print(f"💎 Hidden Gem Scanner — {gem_info['name']} ({asset})")
    print("=" * 60)
    
    print(f"  📝 {gem_info['description']}")
    print(f"  📊 Pair: {gem_info['pair']}")
    print(f"  💰 Max Market Cap: ${gem_info['max_market_cap']/1000000:.1f}M")
    print(f"  🐋 Min Whale Orders: ${gem_info['min_order_size']/1000000:.1f}M")
    print(f"  📊 Volume Multiplier: {gem_info['volume_multiplier']}x")
    print(f"  📈 Max Daily Change: {gem_info['max_price_change']*100:.1f}%")
    print(f"  📊 RSI Oversold: {gem_info['rsi_oversold']}")
    print("=" * 60)
    
    analysis = analyze_hidden_gem(asset.upper(), gem_info)
    
    if "error" in analysis:
        print(f"  ❌ {analysis['error']}")
        return
    
    # Print detailed analysis
    print(f"\n  📊 {gem_info['name']} Analysis:")
    print(f"  💰 Current Price: ${analysis['current_price']:.8f}")
    print(f"  📊 Market Cap: ${analysis['market_cap']/1000000:.1f}M")
    print(f"  📈 RSI: {analysis['rsi']:.1f}")
    print(f"  📊 Volume 24h: {analysis['volume_24h']:,.0f}")
    print(f"  📈 Volume Increase: {analysis['volume_increase']:.1f}x")
    print(f"  📊 Price Volatility: {analysis['price_volatility']*100:.1f}%")
    print(f"  📈 Max Daily Change: {analysis['max_daily_change']*100:.1f}%")
    
    # Order analysis
    if not analysis["order_analysis"].get("error"):
        order_analysis = analysis["order_analysis"]
        print(f"\n  🐋 Whale Order Analysis:")
        print(f"  💰 Total Buy Orders: ${order_analysis['total_buy_value']:,.0f}")
        print(f"  💰 Total Sell Orders: ${order_analysis['total_sell_value']:,.0f}")
        print(f"  📊 Buy/Sell Ratio: {order_analysis['buy_sell_ratio']:.2f}")
        
        large_orders = order_analysis.get("large_orders", [])
        if large_orders:
            print(f"  🐋 Large Orders Found: {len(large_orders)}")
            for order in large_orders[:5]:
                print(f"      💰 ${order['value']:,.0f} @ ${order['price']:.8f}")
    
    # Criteria analysis
    if analysis["criteria_met"]:
        print(f"\n  ✅ Hidden Gem Criteria Met:")
        for criterion in analysis["criteria_met"]:
            print(f"      🎯 {criterion.replace('_', ' ')}")
        print(f"\n  🌙 Hidden Gem Score: {analysis['gem_score']}/10")
        
        # Moon potential
        if analysis["gem_score"] >= 8:
            print(f"\n  🚀 *EXTREME MOON POTENTIAL*: This could 100x!")
            print(f"      💡 *RECOMMENDATION*: BUY NOW - Before moon!")
        elif analysis["gem_score"] >= 6:
            print(f"\n  🌙 *HIGH MOON POTENTIAL*: Strong 50x potential!")
            print(f"      💡 *RECOMMENDATION*: Accumulate position")
        else:
            print(f"\n  🌕 *MODERATE POTENTIAL*: Good 10-20x potential")
            print(f"      💡 *RECOMMENDATION*: Small test position")
    else:
        print(f"\n  📊 *NO HIDDEN GEM CRITERIA MET*")
        print(f"      💡 This coin may have already mooned")
    
    # Send alert if high potential
    if analysis["gem_score"] >= 7:
        alert_msg = f"💎 *Hidden Gem Alert*\n\n"
        alert_msg += f"💎 {asset} ({gem_info['name']})\n"
        alert_msg += f"💰 ${analysis['current_price']:.8f}\n"
        alert_msg += f"📊 MC: ${analysis['market_cap']/1000000:.1f}M\n"
        alert_msg += f"🌙 Score: {analysis['gem_score']}/10\n"
        alert_msg += f"🚨 {', '.join(analysis['criteria_met'])}\n"
        alert_msg += f"🤖 Hidden Gem Scanner | {datetime.now().strftime('%H:%M')}"
        tg(alert_msg)
    
    print(f"\n  ✅ {asset} analysis complete!")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Hidden Gem Scanner — Kraken")
    parser.add_argument("--scan", action="store_true", help="One-time scan of all hidden gems")
    parser.add_argument("--asset", type=str, help="Analyze specific hidden gem")
    parser.add_argument("--monitor", action="store_true", help="Continuous monitoring mode")
    args = parser.parse_args()
    
    if args.scan:
        scan_mode()
    elif args.asset:
        asset_mode(args.asset)
    elif args.monitor:
        monitor_mode()
    else:
        print("💎 Hidden Gem Scanner — Kraken")
        print("Usage:")
        print("  python3 hidden_gem_scanner.py --scan      # Scan all hidden gems")
        print("  python3 hidden_gem_scanner.py --asset FLOKI # Analyze FLOKI")
        print("  python3 hidden_gem_scanner.py --monitor   # Continuous monitoring")
        print("\nHidden Gems Criteria:")
        print("  • Market Cap < $50M (undiscovered)")
        print("  • Large whale orders > $25M")
        print("  • Volume increasing 2-4x average")
        print("  • Price stable (no recent pumps)")
        print("  • RSI oversold but not crashed")
        print("\nThese are the 'before the moon' coins!")

if __name__ == "__main__":
    main()
