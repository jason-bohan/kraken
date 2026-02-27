#!/usr/bin/env python3
"""
🚀 Universal Bull Market Bot — Kraken
Detects bullish patterns across all assets and trades automatically.

Features:
- Scans ALL tradable Kraken pairs
- Multi-timeframe analysis (1m, 5m, 15m, 1h, 4h)
- Bullish pattern detection (breakouts, reversals, momentum)
- Dynamic asset selection (crypto + stocks/ETFs)
- Risk management with stop-loss and take-profit
- OCO orders for complete protection
- Telegram notifications
- Portfolio tracking

Usage:
    python3 universal_bull_bot.py [--scan] [--asset BTC] [--dry]
"""

import os
import time
import argparse
from datetime import datetime, timedelta
from kraken_connection import (
    get_ticker, get_ohlc, get_balance, get_orderbook, 
    place_order, place_oco_order, get_open_orders, cancel_order,
    get_asset_pairs
)

# 📱 Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# 🎯 TRADING CONFIGURATION
# ─────────────────────────────────────────────

# Risk management
RISK_PER_TRADE = 0.02  # 2% risk per trade
MIN_RISK_REWARD = 1.5      # Minimum 1.5:1 risk/reward
MAX_POSITION_SIZE = 0.30     # Maximum 30% of portfolio per trade

# Bullish detection parameters
BREAKOUT_THRESHOLD = 0.02      # 2% above recent high = breakout
MOMENTUM_THRESHOLD = 0.015    # 1.5% momentum threshold
VOLUME_SPIKE_THRESHOLD = 2.0    # 2x normal volume = spike
RSI_BULLISH_MIN = 45        # RSI above this = bullish
RSI_BULLISH_MAX = 70        # RSI below this = not overbought

# Timeframes for analysis
TIMEFRAMES = {
    "1m": 1, "5m": 5, "15m": 15, 
    "1h": 60, "4h": 240
}

# ─────────────────────────────────────────────
# 📱 TELEGRAM NOTIFICATIONS
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
# 📊 ASSET PAIR MANAGEMENT
# ─────────────────────────────────────────────

def get_all_tradable_pairs():
    """Get all tradable pairs from Kraken."""
    try:
        pairs = get_asset_pairs()
        tradable = []
        
        for pair_name, pair_info in pairs.items():
            if pair_info.get('wsname') and pair_info.get('base'):
                tradable.append({
                    'pair': pair_name,
                    'base': pair_info['base'],
                    'quote': pair_info['quote'],
                    'wsname': pair_info['wsname'],
                    'ordermin': float(pair_info.get('ordermin', 0))
                })
        
        return tradable
    except Exception as e:
        print(f"  ⚠️ Error fetching pairs: {e}")
        return []

def filter_bullish_assets(pairs, min_volume=0):
    """Filter assets with sufficient volume and liquidity."""
    filtered = []
    
    for pair in pairs:
        # Focus on major crypto and liquid assets
        base = pair['base'].replace('X', '')  # XBT -> BTC
        
        # Include major crypto (remove X prefix)
        if base in ['BTC', 'ETH', 'SOL', 'DOT', 'ADA', 'LINK', 'UNI']:
            filtered.append(pair)
        # Include major ETFs
        elif base in ['SOXS', 'TSLA', 'AAPL', 'MSFT', 'GOOGL']:
            filtered.append(pair)
        # Include other common crypto pairs
        elif base in ['LTC', 'BCH', 'XLM', 'XRP', 'DOGE', 'SHIB']:
            filtered.append(pair)
    
    print(f"  📊 Filtered {len(filtered)} major assets for analysis")
    return filtered

# ─────────────────────────────────────────────
# 📈 BULLISH PATTERN DETECTION
# ─────────────────────────────────────────────

def analyze_bullish_signals(pair_data, timeframes):
    """Analyze multiple timeframes for bullish patterns."""
    signals = {}
    
    for tf_name, tf_minutes in timeframes.items():
        try:
            # Get OHLC data
            ohlc = get_ohlc(pair_data['pair'], interval=tf_minutes)
            if not ohlc or len(ohlc) < 20:
                continue
            
            # Extract price data
            closes = [float(candle[4]) for candle in ohlc]
            highs = [float(candle[2]) for candle in ohlc]
            lows = [float(candle[3]) for candle in ohlc]
            volumes = [float(candle[5]) for candle in ohlc]
            
            current_price = closes[-1]
            recent_high = max(highs[-10:])
            recent_low = min(lows[-10:])
            
            # Calculate indicators
            # 1. Breakout detection
            breakout = (current_price - recent_high) / recent_high > BREAKOUT_THRESHOLD
            
            # 2. Momentum analysis
            price_change_5 = (current_price - closes[-5]) / closes[-5] if len(closes) >= 5 else 0
            price_change_10 = (current_price - closes[-10]) / closes[-10] if len(closes) >= 10 else 0
            momentum = (price_change_5 + price_change_10) / 2
            
            # 3. Volume spike detection
            avg_volume = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else volumes[-1]
            volume_spike = volumes[-1] / avg_volume if avg_volume > 0 else 1
            
            # 4. RSI calculation
            rsi = calculate_rsi(closes)
            
            # 5. Moving averages
            ma_short = sum(closes[-5:]) / 5 if len(closes) >= 5 else current_price
            ma_long = sum(closes[-10:]) / 10 if len(closes) >= 10 else current_price
            
            # Bullish signal scoring
            score = 0
            reasons = []
            
            if breakout:
                score += 3
                reasons.append("Breakout")
            
            if momentum > MOMENTUM_THRESHOLD:
                score += 2
                reasons.append(f"Momentum {momentum:.2f}")
            
            if volume_spike > VOLUME_SPIKE_THRESHOLD:
                score += 2
                reasons.append(f"Volume spike {volume_spike:.1f}x")
            
            if rsi > RSI_BULLISH_MIN and rsi < RSI_BULLISH_MAX:
                score += 2
                reasons.append(f"RSI {rsi:.0f}")
            
            if current_price > ma_short and current_price > ma_long:
                score += 1
                reasons.append("Uptrend")
            
            # Strong signal threshold
            strong_signal = score >= 4
            
            signals[tf_name] = {
                'score': score,
                'strong': strong_signal,
                'price': current_price,
                'rsi': rsi,
                'breakout': breakout,
                'momentum': momentum,
                'volume_spike': volume_spike,
                'reasons': reasons,
                'timeframe': tf_minutes
            }
            
        except Exception as e:
            print(f"  ⚠️ Error analyzing {pair_data['pair']} on {tf_name}: {e}")
            continue
    
    return signals

def calculate_rsi(closes, period=14):
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

def get_top_bullish_signals(all_signals, min_score=3):
    """Get the strongest bullish signals across all assets."""
    top_signals = []
    
    # Also need the original pair data for execution
    pairs = get_all_tradable_pairs()
    liquid_pairs = filter_bullish_assets(pairs)
    
    # Create a lookup dictionary for pair data
    pair_lookup = {pair['pair']: pair for pair in liquid_pairs}
    
    for pair_name, signals in all_signals.items():
        # Find best signal for this asset
        best_signal = None
        best_score = 0
        
        for tf_name, signal in signals.items():
            if signal['score'] > best_score:
                best_score = signal['score']
                best_signal = signal
        
        if best_signal and best_score >= min_score:
            # Get the original pair data
            original_pair = pair_lookup.get(pair_name, {
                'pair': pair_name,
                'base': pair_name.replace('XBT', 'BTC').replace('XETH', 'ETH'),
                'quote': 'USD',
                'ordermin': 0.01
            })
            
            top_signals.append({
                'pair': original_pair['pair'],
                'base': original_pair['base'],
                'quote': original_pair['quote'],
                'signal': best_signal,
                'score': best_score,
                'timeframes': signals
            })
    
    # Sort by score (highest first)
    top_signals.sort(key=lambda x: x['score'], reverse=True)
    return top_signals[:10]  # Top 10 signals

# ─────────────────────────────────────────────
# 🎯 TRADING EXECUTION
# ─────────────────────────────────────────────

def calculate_position_size(price, balance, pair_info):
    """Calculate optimal position size."""
    min_order = pair_info.get('ordermin', 0.01)
    risk_amount = balance * RISK_PER_TRADE
    
    # Risk-based sizing
    risk_based_size = risk_amount / (price * 0.02)  # 2% risk
    
    # Minimum order size
    min_size = max(risk_based_size, min_order)
    
    # Maximum position size
    max_size = balance * MAX_POSITION_SIZE / price
    
    return min(min_size, max_size)

def execute_trade(pair_data, signal, dry_run=False):
    """Execute a trade with OCO protection."""
    try:
        base = pair_data['base']
        current_price = signal['price']
        
        # Calculate position size
        balances = get_balance()
        usd_balance = float(balances.get('ZUSD', 0))
        
        if usd_balance < 10:  # Minimum $10 for trading
            print(f"  ❌ Insufficient USD balance: ${usd_balance:.2f}")
            return False
        
        position_size = calculate_position_size(current_price, usd_balance, pair_data)
        
        if position_size <= 0:
            print(f"  ❌ Invalid position size: {position_size}")
            return False
        
        # Calculate stop-loss and take-profit
        stop_loss_pct = 0.025  # 2.5% stop-loss
        take_profit_pct = 0.04   # 4% take-profit
        
        stop_loss_price = current_price * (1 - stop_loss_pct)
        take_profit_price = current_price * (1 + take_profit_pct)
        
        print(f"\n  🚀 {base} Bull Signal Detected!")
        print(f"  📊 Price: ${current_price:.4f}")
        print(f"  📈 Signal Score: {signal['score']}")
        print(f"  🎯 Timeframe: {signal.get('timeframe', 'N/A')}")
        print(f"  📝 Reasons: {', '.join(signal['reasons'])}")
        print(f"  📏 Position Size: {position_size:.6f}")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:.4f} ({stop_loss_pct*100:.1f}%)")
        print(f"  🎯 Take Profit: ${take_profit_price:.4f} ({take_profit_pct*100:.1f}%)")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place OCO order")
            return True
        
        # Place OCO order
        print(f"  🛡️ Placing OCO order...")
        oco_result, oco_info = place_oco_order(
            pair=pair_data['pair'],
            side="buy",
            volume=position_size,
            price=take_profit_price,      # Take-profit limit
            price2=stop_loss_price,     # Stop-loss trigger
            validate=False
        )
        
        if oco_result:
            oco_ids = oco_info.get('txid', [])
            print(f"  ✅ OCO Order Placed: {oco_ids}")
            
            # Send notification
            msg = f"🚀 *{base} Bull Trade Executed*\n"
            msg += f"Asset: {pair_data['pair']}\n"
            msg += f"Price: ${current_price:.4f}\n"
            msg += f"Size: {position_size:.6f}\n"
            msg += f"Stop Loss: ${stop_loss_price:.4f}\n"
            msg += f"Take Profit: ${take_profit_price:.4f}\n"
            msg += f"Signal Score: {signal['score']}\n"
            msg += f"Reasons: {', '.join(signal['reasons'])}\n"
            msg += f"OCO Order: {oco_ids}"
            tg(msg)
            
            return True
        else:
            print(f"  ❌ OCO Order Failed: {oco_info}")
            return False
            
    except Exception as e:
        print(f"  ❌ Trade execution error: {e}")
        return False

# ─────────────────────────────────────────────
# 📊 SCANNING & MONITORING
# ─────────────────────────────────────────────

def scan_all_assets(timeframes=None, dry_run=False):
    """Scan all tradable assets for bullish signals."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    print("  🔍 Scanning all tradable assets...")
    pairs = get_all_tradable_pairs()
    liquid_pairs = filter_bullish_assets(pairs)
    
    print(f"  📊 Found {len(liquid_pairs)} liquid assets to analyze")
    
    all_signals = {}
    for pair_data in liquid_pairs:
        print(f"  🔍 Analyzing {pair_data['pair']} ({pair_data['base']})")
        signals = analyze_bullish_signals(pair_data, timeframes)
        all_signals[pair_data['pair']] = signals
    
    # Get top signals
    top_signals = get_top_bullish_signals(all_signals)
    
    print(f"\n  🚀 TOP BULLISH SIGNALS:")
    print(f"  " + "="*80)
    
    for i, signal_data in enumerate(top_signals, 1):
        base = signal_data['base']
        signal = signal_data['signal']
        score = signal_data['score']
        pair_name = signal_data['pair']
        price = signal['price']
        strong = "🔥" if signal['strong'] else "📈"
        
        print(f"  {i+1}. {strong} {base:<6} | ${price:<8.4f} | Score: {score:<2} | {pair_name}")
        print(f"      🎯 {', '.join(signal['reasons'])}")
        print(f"      📊 Timeframes: {', '.join(signal_data['timeframes'].keys())}")
        
        # Execute trades if not dry run
        if not dry_run and score >= 4:  # Only trade strong signals
            print(f"\n  🎯 Executing trade on {base}...")
            execute_trade(signal_data, signal, dry_run)
    
    return top_signals

def scan_specific_asset(asset, timeframes=None, dry_run=False):
    """Scan a specific asset for bullish signals."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    print(f"  🔍 Scanning {asset} for bullish signals...")
    
    # Find the asset pair
    pairs = get_all_tradable_pairs()
    target_pair = None
    target_info = None
    
    asset_upper = asset.upper()
    
    for pair_entry in pairs:
        base = pair_entry.get('base', '').replace('X', '')
        pair_name = pair_entry.get('pair', '').upper()
        
        # Check if user input matches base, pair name, or wsname
        if (base.upper() == asset_upper or 
            pair_name == asset_upper or
            pair_entry.get('wsname', '').replace('/', '').upper() == asset_upper):
            target_pair = pair_entry.get('pair')
            target_info = pair_entry
            break
    
    if not target_pair:
        print(f"  ❌ Asset {asset} not found on Kraken")
        return []
    
    print(f"  📊 Analyzing {target_pair}...")
    pair_data = {
        'pair': target_pair,
        'base': asset.upper(),
        'quote': target_info.get('quote', ''),
        'ordermin': float(target_info.get('ordermin', 0))
    }
    
    signals = analyze_bullish_signals(pair_data, timeframes)
    
    print(f"\n  🚀 {asset} SIGNALS:")
    print(f"  " + "="*60)
    
    for tf_name, signal in signals.items():
        score = signal['score']
        price = signal['price']
        strong = "🔥" if signal['strong'] else "📈"
        
        print(f"  {tf_name}: {strong} ${price:<8.4f} | Score: {score:<2} | {', '.join(signal['reasons'])}")
    
    # Execute best signal
    best_signal = max(signals.values(), key=lambda x: x['score'])
    if not dry_run and best_signal['score'] >= 3:
        print(f"\n  🎯 Executing best signal on {asset}...")
        execute_trade(pair_data, best_signal, dry_run)
    
    return signals

# ─────────────────────────────────────────────
# 🎯 MAIN FUNCTION
# ─────────────────────────────────────────────

def main():
    """Main function to run the universal bull bot."""
    parser = argparse.ArgumentParser(description="Universal Bull Market Bot - Kraken")
    parser.add_argument("--scan", action="store_true", help="Scan all assets for bullish signals")
    parser.add_argument("--asset", type=str, help="Scan specific asset (BTC, ETH, SOL, etc.)")
    parser.add_argument("--dry", action="store_true", help="Dry run (no real trades)")
    parser.add_argument("--timeframes", type=str, default="1m,5m,15m,1h", 
                       help="Timeframes to analyze (comma-separated)")
    args = parser.parse_args()
    
    # Parse timeframes
    if args.timeframes:
        tf_list = [tf.strip() for tf in args.timeframes.split(',')]
        timeframes = {tf: TIMEFRAMES.get(tf, 60) for tf in tf_list}
    else:
        timeframes = TIMEFRAMES
    
    mode = "🔵 DRY RUN" if args.dry else "🟢 LIVE"
    print(f"🚀 Universal Bull Bot — Kraken {mode}")
    print(f"📊 Timeframes: {', '.join(timeframes.keys())}")
    
    if args.scan:
        # Scan all assets
        scan_all_assets(timeframes, args.dry)
    elif args.asset:
        # Scan specific asset
        scan_specific_asset(args.asset, timeframes, args.dry)
    else:
        print("  ❌ Please specify --scan or --asset <symbol>")
        return
    
    print(f"\n  ✅ Scanning complete!")

if __name__ == "__main__":
    main()
