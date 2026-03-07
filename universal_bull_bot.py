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
        
        # Check existing holdings first
        balances = get_balance()
        usd_balance = float(balances.get('ZUSD', 0))
        
        # Check if we already have this asset
        asset_balance = 0
        for asset, balance in balances.items():
            if asset.replace('X', '') == base:  # Handle XBT -> BTC, XETH -> ETH
                asset_balance = float(balance)
                break
        
        if asset_balance > 0:
            print(f"  ⚠️ Already holding {asset_balance:.6f} {base}")
            print(f"  💡 Skipping buy order - consider selling instead")
            return False
        
        # Check for existing OCO orders on this pair
        try:
            open_orders = get_open_orders()
            for order_id, order_data in open_orders.get('open', {}).items():
                if order_data.get('descr', {}).get('pair') == pair_data['pair']:
                    print(f"  ⚠️ Existing order found: {order_id}")
                    print(f"  💡 Skipping - order already active")
                    return False
        except Exception as e:
            print(f"  ⚠️ Could not check existing orders: {e}")
        
        if usd_balance < 5:  # Minimum $5 for trading
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

def scan_specific_asset(asset, timeframes=None, dry_run=False, sell_mode=False):
    """Scan a specific asset for bullish signals or sell existing positions."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    if sell_mode:
        print(f"  🔍 Checking {asset} for sell signals...")
        return check_sell_signals(asset, dry_run)
    
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

def detect_crypto_options(timeframes=None, dry_run=False):
    """Detect crypto options trading opportunities."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    print("  🔍 Scanning for crypto options opportunities...")
    
    # Crypto options available on Kraken
    crypto_options = {
        'BTC': {'name': 'Bitcoin', 'pair': 'XBTUSD', 'volatility_threshold': 0.03},
        'ETH': {'name': 'Ethereum', 'pair': 'XETHZUSD', 'volatility_threshold': 0.04},
        'SOL': {'name': 'Solana', 'pair': 'SOLUSD', 'volatility_threshold': 0.05},
        'ADA': {'name': 'Cardano', 'pair': 'ADAUSD', 'volatility_threshold': 0.04},
        'DOT': {'name': 'Polkadot', 'pair': 'DOTUSD', 'volatility_threshold': 0.06}
    }
    
    options_opportunities = []
    
    for crypto_symbol, crypto_info in crypto_options.items():
        try:
            print(f"  🔍 Analyzing {crypto_info['name']} options...")
            
            # Get OHLC data for volatility
            ohlc = get_ohlc(crypto_info['pair'], interval=15)
            if not ohlc or len(ohlc) < 20:
                continue
            
            # Extract price data
            closes = [float(candle[4]) for candle in ohlc]
            highs = [float(candle[2]) for candle in ohlc]
            lows = [float(candle[3]) for candle in ohlc]
            
            current_price = closes[-1]
            
            # Calculate volatility (standard deviation of returns)
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            volatility = (sum(r**2 for r in returns) / len(returns)) ** 0.5
            
            # Options signal detection
            # 1. High volatility (good for options)
            high_volatility = volatility > crypto_info['volatility_threshold']
            
            # 2. RSI extremes (good for options)
            rsi = calculate_rsi(closes)
            rsi_extreme = rsi < 30 or rsi > 70
            
            # 3. Price breakout (good for options)
            recent_high = max(highs[-10:])
            recent_low = min(lows[-10:])
            breakout_up = current_price > recent_high * 1.02
            breakout_down = current_price < recent_low * 0.98
            
            # Options signal scoring
            score = 0
            reasons = []
            
            if high_volatility:
                score += 3
                reasons.append(f"High vol {volatility:.3f}")
            
            if rsi_extreme:
                score += 2
                reasons.append(f"RSI {rsi:.0f}")
            
            if breakout_up or breakout_down:
                score += 2
                reasons.append("Breakout")
            
            # Strong options signal threshold
            strong_signal = score >= 5
            
            if score >= 4:  # Show signals score 4+
                options_opportunities.append({
                    'symbol': crypto_symbol,
                    'name': crypto_info['name'],
                    'pair': crypto_info['pair'],
                    'price': current_price,
                    'volatility': volatility,
                    'score': score,
                    'strong': strong_signal,
                    'reasons': reasons,
                    'high_volatility': high_volatility,
                    'rsi_extreme': rsi_extreme,
                    'breakout_up': breakout_up,
                    'breakout_down': breakout_down
                })
                
        except Exception as e:
            print(f"  ⚠️ Error analyzing {crypto_info['name']} options: {e}")
            continue
    
    # Sort by score (highest first)
    options_opportunities.sort(key=lambda x: x['score'], reverse=True)
    
    print(f"\n  📊 CRYPTO OPTIONS OPPORTUNITIES:")
    print(f"  " + "="*80)
    
    for i, options_data in enumerate(options_opportunities[:3], 1):  # Top 3
        symbol = options_data['symbol']
        score = options_data['score']
        price = options_data['price']
        volatility = options_data['volatility']
        strong = "🔥" if options_data['strong'] else "📊"
        
        print(f"  {i}. {strong} {symbol:<6} | ${price:<8.2f} | Score: {score:<2} | Vol: {volatility:.3f}")
        print(f"      📊 {', '.join(options_data['reasons'])}")
        
        # Suggest option strategies
        if options_data['breakout_up']:
            print(f"      💡 Consider CALL options (bullish)")
        elif options_data['breakout_down']:
            print(f"      💡 Consider PUT options (bearish)")
        elif options_data['high_volatility']:
            print(f"      💡 Consider STRADDLE (high volatility)")
        
        # Execute options trades if not dry run
        if not dry_run and score >= 5:  # Only trade strong signals
            print(f"\n  📊 Executing options strategy on {symbol}...")
            execute_options_trade(options_data, dry_run)
    
    return options_opportunities

def execute_options_trade(options_data, dry_run=False):
    """Execute a crypto options trade."""
    try:
        symbol = options_data['symbol']
        name = options_data['name']
        current_price = options_data['price']
        
        print(f"\n  📊 {name} Options Strategy Detected!")
        print(f"  💰 Current Price: ${current_price:.2f}")
        print(f"  📈 Signal Score: {options_data['score']}")
        print(f"  📝 Reasons: {', '.join(options_data['reasons'])}")
        print(f"  📊 Volatility: {options_data['volatility']:.3f}")
        
        # Suggest options strategy based on signal
        if options_data['breakout_up']:
            strategy = "CALL OPTIONS - Bullish breakout detected"
            direction = "upward"
        elif options_data['breakout_down']:
            strategy = "PUT OPTIONS - Bearish breakdown detected"
            direction = "downward"
        else:
            strategy = "STRADDLE - High volatility, play both directions"
            direction = "both directions"
        
        print(f"  🎯 Recommended Strategy: {strategy}")
        print(f"  📈 Expected Direction: {direction}")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would analyze options positions")
            print(f"  💡 Manual options trading required via Kraken web interface")
            return True
        
        # Note: Options trading requires manual setup via Kraken interface
        print(f"  ⚠️ Options trading requires manual setup via Kraken web interface")
        print(f"  💡 Go to Kraken > Derivatives > Options")
        print(f"  💡 Search for {name} options contracts")
        
        # Send notification with strategy recommendation
        msg = f"📊 *{name} Options Opportunity*\n"
        msg += f"Price: ${current_price:.2f}\n"
        msg += f"Volatility: {options_data['volatility']:.3f}\n"
        msg += f"Signal Score: {options_data['score']}\n"
        msg += f"Strategy: {strategy}\n"
        msg += f"Direction: {direction}\n"
        msg += f"Setup: Manual via Kraken Options interface"
        tg(msg)
        
        return True
            
    except Exception as e:
        print(f"  ❌ Options analysis error: {e}")
        return False

def detect_short_opportunities(timeframes=None, dry_run=False):
    """Detect short selling opportunities using inverse ETFs and bearish patterns."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    print("  🔍 Scanning for short selling opportunities...")
    
    # Inverse ETFs that act like shorts
    inverse_etfs = {
        'SOXS': {'name': 'SOXS', 'pair': 'SOXSUSD', 'multiplier': 3.0, 'description': '3x Short S&P 500'},
        'SQQQ': {'name': 'SQQQ', 'pair': 'SQQQUSD', 'multiplier': 3.0, 'description': '3x Short NASDAQ-100'},
        'DOGZ': {'name': 'DOGZ', 'pair': 'DOGZUSD', 'multiplier': 1.0, 'description': '1x Short Dow Jones'},
        'BERZ': {'name': 'BERZ', 'pair': 'BERZUSD', 'multiplier': 1.0, 'description': '1x Short Nasdaq'}
    }
    
    short_opportunities = []
    
    for etf_symbol, etf_info in inverse_etfs.items():
        try:
            print(f"  🔍 Analyzing {etf_info['name']} ({etf_info['pair']})")
            
            # Get OHLC data
            ohlc = get_ohlc(etf_info['pair'], interval=15)
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
            
            # Bearish signal detection
            # 1. Breakout below recent low
            breakdown = (recent_low - current_price) / recent_low > 0.02  # 2% below low
            
            # 2. Volume spike on down days
            avg_volume = sum(volumes[-10:]) / 10
            volume_spike = volumes[-1] / avg_volume if avg_volume > 0 else 1
            
            # 3. RSI showing weakness
            rsi = calculate_rsi(closes)
            rsi_weak = rsi < 40  # Oversold
            
            # 4. Moving averages crossing down
            ma_short = sum(closes[-5:]) / 5 if len(closes) >= 5 else current_price
            ma_long = sum(closes[-10:]) / 10 if len(closes) >= 10 else current_price
            death_cross = current_price < ma_short and ma_short < ma_long
            
            # Bearish signal scoring
            score = 0
            reasons = []
            
            if breakdown:
                score += 3
                reasons.append("Breakdown")
            
            if volume_spike > 2.0:
                score += 2
                reasons.append(f"Volume spike {volume_spike:.1f}x")
            
            if rsi_weak:
                score += 2
                reasons.append(f"RSI {rsi:.0f}")
            
            if death_cross:
                score += 1
                reasons.append("Death cross")
            
            # Strong short signal threshold
            strong_signal = score >= 4
            
            if score >= 3:  # Show signals score 3+
                short_opportunities.append({
                    'symbol': etf_info['name'],
                    'pair': etf_info['pair'],
                    'multiplier': etf_info['multiplier'],
                    'description': etf_info['description'],
                    'price': current_price,
                    'score': score,
                    'strong': strong_signal,
                    'reasons': reasons,
                    'breakdown': breakdown,
                    'volume_spike': volume_spike,
                    'rsi': rsi,
                    'death_cross': death_cross
                })
                
        except Exception as e:
            print(f"  ⚠️ Error analyzing {etf_info['name']}: {e}")
            continue
    
    # Sort by score (highest first)
    short_opportunities.sort(key=lambda x: x['score'], reverse=True)
    
    print(f"\n  📉 SHORT SELLING OPPORTUNITIES:")
    print(f"  " + "="*80)
    
    for i, short_data in enumerate(short_opportunities[:5], 1):  # Top 5
        symbol = short_data['symbol']
        score = short_data['score']
        price = short_data['price']
        multiplier = short_data['multiplier']
        strong = "🔥" if short_data['strong'] else "📉"
        
        print(f"  {i}. {strong} {symbol:<6} | ${price:<8.2f} | Score: {score:<2} | {multiplier}x")
        print(f"      📉 {', '.join(short_data['reasons'])}")
        print(f"      📊 {short_data['description']}")
        
        # Execute short trades if not dry run
        if not dry_run and score >= 4:  # Only trade strong signals
            print(f"\n  📉 Executing short on {symbol}...")
            execute_short_trade(short_data, dry_run)
    
    return short_opportunities

def execute_short_trade(short_data, dry_run=False):
    """Execute a short trade using inverse ETF."""
    try:
        symbol = short_data['symbol']
        pair = short_data['pair']
        current_price = short_data['price']
        multiplier = short_data['multiplier']
        
        # Calculate position size
        balances = get_balance()
        usd_balance = float(balances.get('ZUSD', 0))
        
        if usd_balance < 10:  # Minimum $10 for trading
            print(f"  ❌ Insufficient USD balance: ${usd_balance:.2f}")
            return False
        
        # For shorting, we want smaller position (higher risk)
        position_size = (usd_balance * 0.1) / current_price  # 10% of balance
        
        if position_size <= 0:
            print(f"  ❌ Invalid position size: {position_size}")
            return False
        
        # Calculate stop-loss and take-profit (inverse logic)
        # For shorting, stop-loss is ABOVE current price
        stop_loss_pct = 0.03  # 3% stop-loss (above for short)
        take_profit_pct = 0.05   # 5% take-profit (below for short)
        
        stop_loss_price = current_price * (1 + stop_loss_pct)  # Stop above
        take_profit_price = current_price * (1 - take_profit_pct)  # Take profit below
        
        print(f"\n  📉 {symbol} Short Signal Detected!")
        print(f"  📊 Price: ${current_price:.4f}")
        print(f"  📈 Signal Score: {short_data['score']}")
        print(f"  📝 Reasons: {', '.join(short_data['reasons'])}")
        print(f"  📏 Position Size: {position_size:.6f}")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:.4f} ({stop_loss_pct*100:.1f}% above)")
        print(f"  🎯 Take Profit: ${take_profit_price:.4f} ({take_profit_pct*100:.1f}% below)")
        print(f"  📊 Leverage: {multiplier}x via inverse ETF")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place short position")
            return True
        
        # Place OCO order for short position
        print(f"  📉 Placing short OCO order...")
        oco_result, oco_info = place_oco_order(
            pair=pair,
            side="buy",  # Buy inverse ETF to short market
            order_type="oco",
            volume=position_size,
            price=take_profit_price,      # Take-profit limit (below)
            price2=stop_loss_price,     # Stop-loss trigger (above)
            validate=False
        )
        
        if oco_result:
            oco_ids = oco_info.get('txid', [])
            print(f"  ✅ Short OCO Order Placed: {oco_ids}")
            
            # Send notification
            msg = f"📉 *{symbol} Short Trade Executed*\n"
            msg += f"Asset: {pair}\n"
            msg += f"Price: ${current_price:.4f}\n"
            msg += f"Size: {position_size:.6f}\n"
            msg += f"Stop Loss: ${stop_loss_price:.4f}\n"
            msg += f"Take Profit: ${take_profit_price:.4f}\n"
            msg += f"Leverage: {multiplier}x\n"
            msg += f"Signal Score: {short_data['score']}\n"
            msg += f"Reasons: {', '.join(short_data['reasons'])}\n"
            msg += f"OCO Order: {oco_ids}"
            tg(msg)
            
            return True
        else:
            print(f"  ❌ Short OCO Order Failed: {oco_info}")
            return False
            
    except Exception as e:
        print(f"  ❌ Short trade execution error: {e}")
        return False

def format_price_for_pair(price: float, pair: str) -> float:
    """Format price according to pair precision requirements."""
    # Define precision for different pairs
    precision_map = {
        'DOTETH': 6,    # DOT/ETH limited to 6 decimals
        'DOTBTC': 8,    # DOT/BTC more precision
        'DOTUSD': 4,    # DOT/USD standard
        'DOTUSDC': 4,   # DOT/USDC standard
        'DOTUSDT': 4,   # DOT/USDT standard
    }
    
    precision = precision_map.get(pair, 6)  # Default to 6 decimals
    
    # Round to appropriate precision
    return round(price, precision)

def place_trailing_stop_loss(pair: str, volume: str, trail_percentage: float = 0.02, dry_run: bool = False):
    """Place a trailing stop-loss order."""
    try:
        print(f"  🎯 Placing trailing stop-loss at {trail_percentage*100:.1f}% trail...")
        
        # Get current price
        ticker = get_ticker(pair)
        if not ticker:
            print(f"  ❌ Could not get current price for {pair}")
            return False, None
        
        current_price = float(ticker.get("c", [0])[0])
        print(f"  💰 Current price: ${current_price:.6f}")
        
        # Calculate trailing stop price (2% below current)
        stop_price = current_price * (1 - trail_percentage)
        
        # Format price according to pair precision
        formatted_stop_price = format_price_for_pair(stop_price, pair)
        formatted_current_price = format_price_for_pair(current_price, pair)
        
        print(f"  💰 Current price: ${formatted_current_price:.6f}")
        print(f"  🛡️ Initial stop: ${formatted_stop_price:.6f}")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place trailing stop-loss")
            return True, {"stop_price": formatted_stop_price}
        
        # Try trailing-stop-limit order first (like GUI)
        print(f"  🎯 Trying trailing-stop-limit order...")
        result, info = place_order(
            pair=pair,
            side="sell",
            order_type="trailing-stop-limit",  # Use trailing-stop-limit like GUI
            volume=volume,
            price=formatted_stop_price,
            validate=False
        ,
            userref=9
        )
        
        # If trailing-stop-limit fails, try regular trailing-stop
        if not result:
            print(f"  ⚠️ Trailing-stop-limit failed, trying regular trailing-stop...")
            result, info = place_order(
                pair=pair,
                side="sell",
                order_type="trailing-stop",  # Fallback to trailing-stop
                volume=volume,
                price=formatted_stop_price,
                validate=False
            ,
            userref=9
        )
        
        # If both fail, try regular stop-loss
        if not result:
            print(f"  ⚠️ Trailing-stop failed, trying regular stop-loss...")
            result, info = place_order(
                pair=pair,
                side="sell",
                order_type="stop-loss",  # Fallback to stop-loss
                volume=volume,
                price=formatted_stop_price,
                validate=False
            ,
            userref=9
        )
        
        if result:
            order_id = info.get('txid', [None])[0]
            print(f"  ✅ Trailing stop-loss placed: {order_id}")
            print(f"  🛡️ Stop: ${formatted_stop_price:.6f} (trails {trail_percentage*100:.1f}% below price)")
            return True, info
        else:
            print(f"  ❌ Trailing stop failed: {info}")
            return False, info
            
    except Exception as e:
        print(f"  ❌ Trailing stop error: {e}")
        return False, str(e)

def update_trailing_stop(pair: str, order_id: str, new_stop_price: float, dry_run: bool = False):
    """Update trailing stop-loss order."""
    try:
        print(f"  🔄 Updating trailing stop to ${new_stop_price:.6f}")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would update trailing stop")
            return True
        
        # Cancel old order
        cancel_result = cancel_order(order_id)
        if cancel_result:
            print(f"  ✅ Old trailing stop canceled")
        else:
            print(f"  ❌ Failed to cancel old stop")
            return False
        
        # Place new trailing stop
        balances = get_balance()
        asset_balance = 0
        base = pair.replace('USD', '').replace('USDT', '').replace('USDC', '')
        
        for asset, balance in balances.items():
            if asset.replace('X', '') == base:
                asset_balance = float(balance)
                break
        
        if asset_balance <= 0:
            print(f"  ❌ No {base} holdings to protect")
            return False
        
        result, info = place_order(
            pair=pair,
            side="sell", 
            order_type="stop-loss",
            volume=asset_balance,
            price=new_stop_price,
            validate=False
        ,
            userref=9
        )
        
        if result:
            new_order_id = info.get('txid', [None])[0]
            print(f"  ✅ New trailing stop placed: {new_order_id}")
            return True, info
        else:
            print(f"  ❌ New trailing stop failed: {info}")
            return False
            
    except Exception as e:
        print(f"  ❌ Update trailing stop error: {e}")
        return False

def check_sell_signals(asset, dry_run=False):
    """Check if existing position should be sold."""
    try:
        # Get current holdings
        balances = get_balance()
        asset_balance = 0
        
        # Find the asset balance
        for balance_asset, balance in balances.items():
            if balance_asset.replace('X', '') == asset.upper():
                asset_balance = float(balance)
                break
        
        if asset_balance <= 0:
            print(f"  ℹ️ No {asset} holdings found")
            return False
        
        print(f"  📊 Current {asset} holdings: {asset_balance:.6f}")
        
        # Get current price
        pairs = get_all_tradable_pairs()
        target_pair = None
        
        for pair_entry in pairs:
            base = pair_entry.get('base', '').replace('X', '')
            if base.upper() == asset.upper():
                target_pair = pair_entry.get('pair')
                break
        
        if not target_pair:
            print(f"  ❌ Could not find {asset} trading pair")
            return False
        
        ticker = get_ticker(target_pair)
        if not ticker:
            print(f"  ❌ Could not get {asset} price")
            return False
        
        current_price = float(ticker.get("c", [0])[0])
        print(f"  💰 Current price: ${current_price:.6f}")
        
        # Simple sell logic: sell if we have any holdings
        print(f"\n  🎯 Sell Signal Detected!")
        print(f"  📊 Selling {asset_balance:.6f} {asset} at ${current_price:.6f}")
        print(f"  💰 Total value: ${asset_balance * current_price:.2f}")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place sell order")
            return True
        
        # Place sell order
        sell_result, sell_info = place_order(
            pair=target_pair,
            side="sell",
            order_type="market",
            volume=asset_balance,
            validate=False
        ,
            userref=9
        )
        
        if sell_result:
            print(f"  ✅ Sell order placed: {sell_info}")
            return True
        else:
            print(f"  ❌ Sell order failed: {sell_info}")
            return False
            
    except Exception as e:
        print(f"  ❌ Sell signal error: {e}")
        return False

def place_trailing_stop_on_asset(asset, trail_percentage, dry_run=False):
    """Place trailing stop-loss on existing asset position."""
    try:
        print(f"  🎯 Setting up trailing stop-loss for {asset}...")
        
        # Get current holdings
        balances = get_balance()
        asset_balance = 0
        
        # Find the asset balance
        for balance_asset, balance in balances.items():
            if balance_asset.replace('X', '') == asset.upper():
                asset_balance = float(balance)
                break
        
        if asset_balance <= 0:
            print(f"  ❌ No {asset} holdings found")
            return False
        
        print(f"  📊 Current {asset} holdings: {asset_balance:.6f}")
        
        # Find the trading pair
        pairs = get_all_tradable_pairs()
        target_pair = None
        
        for pair_entry in pairs:
            base = pair_entry.get('base', '').replace('X', '')
            if base.upper() == asset.upper():
                target_pair = pair_entry.get('pair')
                break
        
        if not target_pair:
            print(f"  ❌ Could not find {asset} trading pair")
            return False
        
        # Check for existing orders
        try:
            open_orders = get_open_orders()
            for order_id, order_data in open_orders.get('open', {}).items():
                if order_data.get('descr', {}).get('pair') == target_pair:
                    print(f"  ⚠️ Existing order found: {order_id}")
                    print(f"  💡 Order details: {order_data.get('descr', {})}")
                    print(f"  💡 Canceling existing order first...")
                    cancel_result = cancel_order(order_id)
                    if cancel_result:
                        print(f"  ✅ Existing order canceled")
                    else:
                        print(f"  ❌ Failed to cancel existing order")
                        return False
        except Exception as e:
            print(f"  ⚠️ Could not check existing orders: {e}")
        
        # Debug: Show balance details
        print(f"  🔍 Debug - Balance details:")
        for asset, balance in balances.items():
            if 'DOT' in asset.upper() or asset.replace('X', '') == 'DOT':
                print(f"     {asset}: {balance}")
        
        # Debug: Show pair details
        print(f"  🔍 Debug - Pair details: {target_pair}")
        print(f"  🔍 Debug - Volume: {asset_balance}")
        print(f"  🔍 Debug - Formatted stop: ${formatted_stop_price:.6f}")
        
        # Place trailing stop-loss
        result, info = place_trailing_stop_loss(
            pair=target_pair,
            volume=str(asset_balance),
            trail_percentage=trail_percentage,
            dry_run=dry_run
        )
        
        if result:
            print(f"  ✅ Trailing stop-loss setup complete!")
            print(f"  🛡️ Protecting {asset_balance:.6f} {asset} with {trail_percentage*100:.1f}% trail")
            
            # Send notification
            msg = f"🛡️ *{asset} Trailing Stop-Loss Set*\n"
            msg += f"Holdings: {asset_balance:.6f}\n"
            msg += f"Trail: {trail_percentage*100:.1f}%\n"
            msg += f"Pair: {target_pair}"
            tg(msg)
            
            return True
        else:
            print(f"  ❌ Failed to set trailing stop-loss")
            return False
            
    except Exception as e:
        print(f"  ❌ Trailing stop setup error: {e}")
        return False

# ─────────────────────────────────────────────
# 🎯 MAIN FUNCTION
# ─────────────────────────────────────────────

def main():
    """Main function to run the universal bull bot."""
    parser = argparse.ArgumentParser(description="Universal Bull Market Bot - Kraken")
    parser.add_argument("--scan", action="store_true", help="Scan all assets for bullish signals")
    parser.add_argument("--asset", type=str, help="Scan specific asset (BTC, ETH, SOL, etc.)")
    parser.add_argument("--sell", action="store_true", help="Sell existing position for asset")
    parser.add_argument("--trail", action="store_true", help="Place trailing stop-loss on existing position")
    parser.add_argument("--short", action="store_true", help="Scan for short selling opportunities")
    parser.add_argument("--options", action="store_true", help="Scan for crypto options opportunities")
    parser.add_argument("--dry", action="store_true", help="Dry run (no real trades)")
    parser.add_argument("--trail-pct", type=float, default=0.02, help="Trailing stop percentage (default 2%)")
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
    elif args.short:
        # Scan for short selling opportunities
        detect_short_opportunities(timeframes, args.dry)
    elif args.options:
        # Scan for crypto options opportunities
        detect_crypto_options(timeframes, args.dry)
    elif args.asset:
        if args.trail:
            # Place trailing stop-loss
            place_trailing_stop_on_asset(args.asset, args.trail_pct, args.dry)
        elif args.sell:
            # Sell existing position
            scan_specific_asset(args.asset, timeframes, args.dry, sell_mode=True)
        else:
            # Scan specific asset for buy signals
            scan_specific_asset(args.asset, timeframes, args.dry)
    else:
        print("  ❌ Please specify --scan, --short, --options, --asset <symbol>, --asset <symbol> --sell, or --asset <symbol> --trail")
        return
    
    print(f"\n  ✅ Scanning complete!")

if __name__ == "__main__":
    main()
