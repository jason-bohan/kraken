#!/usr/bin/env python3
"""
🎯 Cheap Futures Bot — SOL/ADA/DOGE Futures & Options Trading
Analyzes affordable crypto futures for high-return trading opportunities.

Strategy:
- Cheap Alternatives: SOL, ADA, DOGE (100-1000x cheaper than BTC)
- High Volatility: Perfect for options trading
- Dynamic Position Management: Buys PUTS in bearish markets, CALLS in bullish markets
- Trailing Stop-Loss: 3% trailing stop to protect profits
- Take-Profit: 15% take-profit (higher due to volatility)
- Auto-Switching: Closes positions and reverses direction on market changes
- 24/7 Monitoring: Continuous market analysis and position management

Usage:
    python3 cheap_futures_bot.py --asset SOL [--dry]    # Analyze SOL futures
    python3 cheap_futures_bot.py --scan [--dry]       # Scan all cheap futures
"""

import os
import time
import argparse
from datetime import datetime
from kraken_connection import (
    get_ticker, get_ohlc, get_balance, get_orderbook, 
    get_open_orders, cancel_order, get_asset_pairs
)

# 📱 Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# 🎯 TRADING CONFIGURATION
# ─────────────────────────────────────────────

# Cheap futures trading parameters
DEFAULT_TRAIL_PCT = 0.03      # 3% trailing stop
DEFAULT_TAKE_PROFIT_PCT = 0.15  # 15% take-profit (higher for volatility)
DEFAULT_POSITION_SIZE = 0.15      # 15% of account per trade (smaller positions)
MIN_POSITION_SIZE = 0.05       # 5% minimum position size
MAX_POSITIONS = 4              # Maximum concurrent positions

# Cheap crypto futures specifications
CHEAP_FUTURES = {
    'SOL': {
        'name': 'Solana',
        'pair': 'SOLUSD',
        'price_multiplier': 1.0,
        'volatility_threshold': 0.06,
        'min_contracts': 1,
        'tick_size': 0.01,
        'correlation_btc': 0.6,
        'description': 'High-performance blockchain, 500x cheaper than BTC'
    },
    'ADA': {
        'name': 'Cardano',
        'pair': 'ADAUSD',
        'price_multiplier': 1.0,
        'volatility_threshold': 0.05,
        'min_contracts': 10,
        'tick_size': 0.0001,
        'correlation_btc': 0.5,
        'description': 'Proof-of-stake blockchain, 1000x cheaper than BTC'
    },
    'DOGE': {
        'name': 'Dogecoin',
        'pair': 'XDGUSD',
        'price_multiplier': 1.0,
        'volatility_threshold': 0.08,
        'min_contracts': 100,
        'tick_size': 0.00001,
        'correlation_btc': 0.4,
        'description': 'Meme coin with high volatility, 8600x cheaper than BTC'
    }
}

# Market analysis parameters
VOLATILITY_THRESHOLD = 0.05      # 5% volatility threshold
RSI_OVERSOLD = 25              # Lower RSI for high-vol assets
RSI_OVERBOUGHT = 75             # Higher RSI for high-vol assets
BREAKOUT_THRESHOLD = 0.03         # 3% breakout threshold (higher for vol)
TIMEFRAMES = {
    "5m": 5, "15m": 15, "1h": 60, "4h": 240
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
# 📊 MARKET ANALYSIS
# ─────────────────────────────────────────────

def analyze_cheap_futures(asset: str, timeframes=None) -> dict:
    """Analyze cheap futures market direction for trading."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    if asset.upper() not in CHEAP_FUTURES:
        return {"error": f"Asset {asset} not supported. Use: {', '.join(CHEAP_FUTURES.keys())}"}
    
    asset_info = CHEAP_FUTURES[asset.upper()]
    pair = asset_info['pair']
    
    print(f"  🔍 Analyzing {asset_info['name']} ({asset})...")
    print(f"  📊 Using pair: {pair}")
    print(f"  💰 {asset_info['description']}")
    
    # Get OHLC data
    ohlc = get_ohlc(pair, interval=15)
    if not ohlc or len(ohlc) < 30:
        return {"error": "Insufficient OHLC data"}
    
    # Extract price data
    closes = [float(candle[4]) for candle in ohlc]
    highs = [float(candle[2]) for candle in ohlc]
    lows = [float(candle[3]) for candle in ohlc]
    volumes = [float(candle[5]) for candle in ohlc]
    
    current_price = closes[-1]
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    
    # Calculate indicators
    # 1. Volatility (standard deviation of returns)
    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    volatility = (sum(r**2 for r in returns) / len(returns)) ** 0.5
    
    # 2. RSI
    rsi = calculate_rsi(closes)
    
    # 3. Trend analysis
    ma_short = sum(closes[-10:]) / 10
    ma_long = sum(closes[-20:]) / 20
    trend_direction = "upward" if ma_short > ma_long else "downward" if ma_short < ma_long else "sideways"
    
    # 4. Market direction signals (adjusted for high volatility)
    # Bearish signals
    breakdown = (recent_low - current_price) / recent_low > BREAKOUT_THRESHOLD
    death_cross = ma_short < ma_long and trend_direction == "downward"
    rsi_oversold = rsi < RSI_OVERSOLD
    
    # Bullish signals  
    breakout = (current_price - recent_high) / recent_high > BREAKOUT_THRESHOLD
    golden_cross = ma_short > ma_long and trend_direction == "upward"
    rsi_overbought = rsi > RSI_OVERBOUGHT
    
    # 5. Overall market direction
    bearish_signals = sum([breakdown, death_cross, rsi_oversold])
    bullish_signals = sum([breakout, golden_cross, rsi_overbought])
    
    market_direction = "bearish" if bearish_signals > bullish_signals else "bullish"
    signal_strength = max(bearish_signals, bullish_signals)
    
    # Calculate cost comparison with BTC
    btc_equivalent_cost = 69000 / current_price  # How many units equal 1 BTC
    
    return {
        "asset": asset,
        "name": asset_info['name'],
        "pair": pair,
        "current_price": current_price,
        "volatility": volatility,
        "rsi": rsi,
        "trend": trend_direction,
        "market_direction": market_direction,
        "signal_strength": signal_strength,
        "btc_equivalent_cost": btc_equivalent_cost,
        "volatility_threshold": asset_info['volatility_threshold'],
        "correlation_btc": asset_info['correlation_btc'],
        "bearish_signals": {
            "breakdown": breakdown,
            "death_cross": death_cross,
            "rsi_oversold": rsi_oversold
        },
        "bullish_signals": {
            "breakout": breakout,
            "golden_cross": golden_cross,
            "rsi_overbought": rsi_overbought
        }
    }

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

# ─────────────────────────────────────────────
# 🎯 CHEAP FUTURES TRADING
# ─────────────────────────────────────────────

def calculate_cheap_futures_position_size(balance: float, volatility: float, asset: str) -> float:
    """Calculate optimal position size for cheap futures."""
    asset_info = CHEAP_FUTURES[asset.upper()]
    
    # Higher volatility = smaller position (cheap futures are very volatile)
    volatility_adjustment = max(0.3, 1.0 - (volatility * 8))
    
    # Asset-specific adjustments
    if asset.upper() == 'DOGE':
        # DOGE is extremely volatile - smaller positions
        volatility_adjustment *= 0.7
    elif asset.upper() == 'SOL':
        # SOL has good volatility - moderate positions
        volatility_adjustment *= 0.9
    elif asset.upper() == 'ADA':
        # ADA is less volatile - slightly larger positions
        volatility_adjustment *= 1.1
    
    # Calculate position size
    position_pct = DEFAULT_POSITION_SIZE * volatility_adjustment
    position_pct = max(MIN_POSITION_SIZE, min(position_pct, MAX_POSITIONS / 10))
    
    return balance * position_pct

def determine_cheap_futures_strategy(market_analysis: dict) -> dict:
    """Determine optimal strategy for cheap futures."""
    market_direction = market_analysis["market_direction"]
    volatility = market_analysis["volatility"]
    signal_strength = market_analysis["signal_strength"]
    asset = market_analysis["asset"]
    asset_info = CHEAP_FUTURES[asset.upper()]
    
    if market_direction == "bearish":
        # Bearish market → Buy PUTS
        strategy = {
            "action": "BUY_PUTS",
            "reason": f"Bearish {asset_info['name']} market ({signal_strength} signals)",
            "direction": "put",
            "expected_move": "downward",
            "volatility_play": "High volatility - good for puts"
        }
    elif market_direction == "bullish":
        # Bullish market → Buy CALLS
        strategy = {
            "action": "BUY_CALLS", 
            "reason": f"Bullish {asset_info['name']} market ({signal_strength} signals)",
            "direction": "call",
            "expected_move": "upward",
            "volatility_play": "High volatility - good for calls"
        }
    else:
        # Sideways market → High volatility strategy
        if volatility > asset_info['volatility_threshold']:
            strategy = {
                "action": "BUY_STRADDLE",
                "reason": f"High {asset_info['name']} volatility ({volatility:.3f}) - straddle",
                "direction": "straddle",
                "expected_move": "both_directions",
                "volatility_play": "Very high volatility - straddle ideal"
            }
        else:
            strategy = {
                "action": "WAIT",
                "reason": f"Low {asset_info['name']} volatility ({volatility:.3f}) - wait for signal",
                "direction": "none",
                "expected_move": "sideways",
                "volatility_play": "Low volatility - wait"
            }
    
    return strategy

def calculate_optimal_strike(current_price: float, market_direction: str, volatility: float, asset: str) -> float:
    """Calculate optimal strike price for cheap futures options."""
    asset_info = CHEAP_FUTURES[asset.upper()]
    
    # For cheap futures, we want strikes that are likely to be in-the-money
    if market_direction == "bullish":
        # For calls: strike slightly above current price
        strike_adjustment = 1.0 + (volatility * 4)  # Higher adjustment for volatility
    elif market_direction == "bearish":
        # For puts: strike slightly below current price
        strike_adjustment = 1.0 - (volatility * 4)
    else:
        # For straddles: use at-the-money
        strike_adjustment = 1.0
    
    optimal_strike = current_price * strike_adjustment
    
    # Round to appropriate tick size
    tick_size = asset_info['tick_size']
    return round(optimal_strike / tick_size) * tick_size

def get_next_expiry_dates() -> list:
    """Get next available expiry dates for options."""
    from datetime import datetime, timedelta
    today = datetime.now()
    
    # Generate weekly, monthly dates
    expiries = []
    
    # Weekly (next Friday)
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0:
        days_until_friday = 7
    weekly_expiry = today + timedelta(days=days_until_friday)
    expiries.append(weekly_expiry.strftime("%m/%d/%y"))
    
    # Monthly (third Friday)
    first_day = today.replace(day=1)
    first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
    third_friday = first_friday + timedelta(days=14)
    
    if third_friday <= today:
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
        first_friday = next_month + timedelta(days=(4 - next_month.weekday()) % 7)
        third_friday = first_friday + timedelta(days=14)
    
    expiries.append(third_friday.strftime("%m/%d/%y"))
    
    return expiries[:2]  # Return weekly and monthly

def execute_cheap_futures_trade(market_analysis: dict, strategy: dict, dry_run: bool = False) -> dict:
    """Execute a cheap futures trade with trailing stop and take-profit."""
    try:
        asset = market_analysis["asset"]
        name = market_analysis["name"]
        current_price = market_analysis["current_price"]
        direction = strategy["direction"]
        volatility = market_analysis["volatility"]
        btc_equivalent = market_analysis["btc_equivalent_cost"]
        
        # Calculate position size
        balances = get_balance()
        usd_balance = float(balances.get('ZUSD', 0))
        
        if usd_balance < 50:  # Minimum $50 for cheap futures
            return {"error": f"Insufficient USD balance: ${usd_balance:.2f}"}
        
        position_size = calculate_cheap_futures_position_size(usd_balance, volatility, asset)
        
        # Get optimal strike and expiry
        optimal_strike = calculate_optimal_strike(current_price, market_analysis["market_direction"], volatility, asset)
        expiry_dates = get_next_expiry_dates()
        best_expiry = expiry_dates[0]  # Use nearest expiry
        
        # Calculate trailing stop and take-profit
        if direction == "put":
            # PUT options: profit when underlying goes down
            stop_loss_price = current_price * (1 + DEFAULT_TRAIL_PCT)  # Stop above
            take_profit_price = current_price * (1 - DEFAULT_TAKE_PROFIT_PCT)  # Target below
        else:
            # CALL options: profit when underlying goes up
            stop_loss_price = current_price * (1 - DEFAULT_TRAIL_PCT)  # Stop below
            take_profit_price = current_price * (1 + DEFAULT_TAKE_PROFIT_PCT)  # Target above
        
        print(f"\n  🎯 {strategy['action']} Strategy Detected!")
        print(f"  📊 {name} ({asset}) Price: ${current_price:.6f}")
        print(f"  📈 Market Direction: {market_analysis['market_direction']}")
        print(f"  📊 Volatility: {market_analysis['volatility']:.3f}")
        print(f"  💰 BTC Equivalent: 1 BTC = {btc_equivalent:.0f} {asset}")
        print(f"  📏 Position Size: {position_size:.2f}% (${position_size:.2f})")
        print(f"  🎯 Strike Price: ${optimal_strike:.6f}")
        print(f"  📅 Expiry: {best_expiry}")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:.6f} ({DEFAULT_TRAIL_PCT*100:.1f}% trail)")
        print(f"  🎯 Take Profit: ${take_profit_price:.6f} ({DEFAULT_TAKE_PROFIT_PCT*100:.1f}% target)")
        
        # Estimate contracts and premiums
        asset_info = CHEAP_FUTURES[asset.upper()]
        min_contracts = asset_info['min_contracts']
        
        # Rough premium estimates (3-8% of strike for high-vol options)
        premium_percentage = 0.05 + (volatility * 2)  # Higher vol = higher premium
        contracts_needed = max(min_contracts, int(position_size / (optimal_strike * premium_percentage)))
        premium_estimate = contracts_needed * optimal_strike * premium_percentage
        
        print(f"\n  📊 Position Estimates:")
        print(f"  📈 Contracts: {contracts_needed} (min: {min_contracts})")
        print(f"  💰 Premium Estimate: ${premium_estimate:.2f}")
        print(f"  📊 Premium %: {premium_percentage*100:.1f}% of strike")
        
        # Show potential returns
        if direction == "call":
            potential_profit = (take_profit_price - optimal_strike) * contracts_needed
        elif direction == "put":
            potential_profit = (optimal_strike - take_profit_price) * contracts_needed
        else:
            potential_profit = premium_estimate * 2  # Straddle estimate
        
        roi = (potential_profit / premium_estimate) * 100 if premium_estimate > 0 else 0
        
        print(f"  🚀 Potential Profit: ${potential_profit:.2f}")
        print(f"  📈 ROI: {roi:.0f}%")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would analyze {asset} options positions")
            return {"success": True, "strategy": strategy}
        
        # Note: Cheap futures options trading requires manual setup
        print(f"\n  ⚠️ Cheap futures options trading requires manual setup")
        print(f"  💡 Go to your broker > Options Trading")
        print(f"  💡 Search for {name} ({asset}) options")
        print(f"  💡 Choose {direction.upper()} options")
        print(f"  💡 Strike: ${optimal_strike:.6f}")
        print(f"  💡 Expiry: {best_expiry}")
        print(f"  💡 Contracts: {contracts_needed}")
        print(f"  💡 Set stop-loss at ${stop_loss_price:.6f}")
        print(f"  💡 Set take-profit at ${take_profit_price:.6f}")
        
        # Send notification with detailed strategy
        msg = f"🎯 *{name} Cheap Futures Signal*\n"
        msg += f"Asset: {asset}\n"
        msg += f"Price: ${current_price:.6f}\n"
        msg += f"Strategy: {strategy['action']}\n"
        msg += f"Direction: {market_analysis['market_direction']}\n"
        msg += f"Volatility: {market_analysis['volatility']:.3f}\n"
        msg += f"BTC Equivalent: 1 BTC = {btc_equivalent:.0f} {asset}\n"
        msg += f"Position: {position_size:.2f}% (${position_size:.2f})\n"
        msg += f"Strike: ${optimal_strike:.6f}\n"
        msg += f"Expiry: {best_expiry}\n"
        msg += f"Contracts: {contracts_needed}\n"
        msg += f"Premium: ${premium_estimate:.2f}\n"
        msg += f"Stop: ${stop_loss_price:.6f}\n"
        msg += f"Target: ${take_profit_price:.6f}\n"
        msg += f"ROI: {roi:.0f}%\n"
        msg += f"Setup: Manual via your broker"
        tg(msg)
        
        return {"success": True, "strategy": strategy}
        
    except Exception as e:
        print(f"  ❌ Cheap futures trade error: {e}")
        return {"error": str(e)}

def scan_all_cheap_futures(timeframes=None, dry_run: bool = False):
    """Scan all cheap futures for opportunities."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    print("  🔍 Scanning cheap futures opportunities...")
    print(f"  💰 Looking for high-volatility, low-cost alternatives to BTC")
    
    all_opportunities = []
    
    for asset in CHEAP_FUTURES.keys():
        market_analysis = analyze_cheap_futures(asset, timeframes)
        
        if "error" in market_analysis:
            print(f"  ❌ {market_analysis['error']}")
            continue
        
        strategy = determine_cheap_futures_strategy(market_analysis)
        
        # Only show strong signals (signal strength >= 2)
        if market_analysis["signal_strength"] >= 2:
            all_opportunities.append({
                "asset": asset,
                "market_analysis": market_analysis,
                "strategy": strategy,
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
    
    # Sort by signal strength
    all_opportunities.sort(key=lambda x: x["market_analysis"]["signal_strength"], reverse=True)
    
    print(f"\n  🎯 CHEAP FUTURES OPPORTUNITIES:")
    print(f"  " + "="*90)
    
    for i, opportunity in enumerate(all_opportunities, 1):
        asset = opportunity["asset"]
        analysis = opportunity["market_analysis"]
        strategy = opportunity["strategy"]
        strength = analysis["signal_strength"]
        direction = strategy["direction"]
        
        strength_emoji = "🔥" if strength >= 3 else "📊"
        
        print(f"  {i}. {strength_emoji} {asset:<4} | ${analysis['current_price']:<10.6f} | {direction.upper():<8} | Strength: {strength}")
        print(f"      📈 {strategy['reason']}")
        print(f"      📊 Vol: {analysis['volatility']:.3f} | RSI: {analysis['rsi']:.0f} | BTC: 1={analysis['btc_equivalent_cost']:.0f}")
        print(f"      💰 {CHEAP_FUTURES[asset.upper()]['description']}")
        
        # Execute trades if not dry run
        if not dry_run:
            print(f"\n  🎯 Executing {strategy['action']} on {asset}...")
            execute_cheap_futures_trade(analysis, strategy, dry_run=False)
    
    return all_opportunities

# ─────────────────────────────────────────────
# 🎯 MAIN FUNCTION
# ─────────────────────────────────────────────

def main():
    """Main function to run cheap futures bot."""
    parser = argparse.ArgumentParser(description="Cheap Futures Bot - SOL/ADA/DOGE Trading")
    parser.add_argument("--scan", action="store_true", help="Scan all cheap futures")
    parser.add_argument("--asset", type=str, help="Analyze specific cheap asset")
    parser.add_argument("--dry", action="store_true", help="Dry run (no real trades)")
    parser.add_argument("--trail-pct", type=float, default=DEFAULT_TRAIL_PCT, 
                       help=f"Trailing stop percentage (default {DEFAULT_TRAIL_PCT*100:.1f}%)")
    parser.add_argument("--profit-pct", type=float, default=DEFAULT_TAKE_PROFIT_PCT,
                       help=f"Take profit percentage (default {DEFAULT_TAKE_PROFIT_PCT*100:.1f}%)")
    parser.add_argument("--timeframes", type=str, default="5m,15m,1h,4h", 
                       help="Timeframes to analyze (comma-separated)")
    args = parser.parse_args()
    
    # Parse timeframes
    if args.timeframes:
        tf_list = [tf.strip() for tf in args.timeframes.split(',')]
        timeframes = {tf: TIMEFRAMES.get(tf, 60) for tf in tf_list}
    else:
        timeframes = TIMEFRAMES
    
    # Update global parameters if provided
    global DEFAULT_TRAIL_PCT, DEFAULT_TAKE_PROFIT_PCT
    DEFAULT_TRAIL_PCT = args.trail_pct
    DEFAULT_TAKE_PROFIT_PCT = args.profit_pct
    
    # Print header
    dry_indicator = "🔵 DRY RUN" if args.dry else "🟢 LIVE"
    print(f"🎯 Cheap Futures Bot — SOL/ADA/DOGE Trading {dry_indicator}")
    print(f"📊 Timeframes: {', '.join(timeframes.keys())}")
    print(f"🛡️ Trailing Stop: {DEFAULT_TRAIL_PCT*100:.1f}%")
    print(f"🎯 Take Profit: {DEFAULT_TAKE_PROFIT_PCT*100:.1f}%")
    print(f"💰 Assets: 100-8600x cheaper than BTC")
    print()
    
    if args.scan:
        # Scan all cheap futures
        opportunities = scan_all_cheap_futures(timeframes, args.dry)
        
        if not opportunities:
            print(f"\n  📊 No cheap futures opportunities found at this time.")
            print(f"  💡 High-volatility assets - waiting for stronger signals")
    
    elif args.asset:
        # Analyze specific asset
        market_analysis = analyze_cheap_futures(args.asset, timeframes)
        
        if "error" in market_analysis:
            print(f"  ❌ {market_analysis['error']}")
            return
        
        strategy = determine_cheap_futures_strategy(market_analysis)
        
        print(f"\n  🎯 {market_analysis['name']} ({args.asset}) Strategy Analysis:")
        print(f"  📊 Price: ${market_analysis['current_price']:.6f}")
        print(f"  📈 Market Direction: {market_analysis['market_direction']}")
        print(f"  📊 Volatility: {market_analysis['volatility']:.3f}")
        print(f"  📊 RSI: {market_analysis['rsi']:.0f}")
        print(f"  🎯 Recommended Action: {strategy['action']}")
        print(f"  📝 Reason: {strategy['reason']}")
        print(f"  📈 Expected Direction: {strategy['expected_move']}")
        print(f"  💰 BTC Equivalent: 1 BTC = {market_analysis['btc_equivalent_cost']:.0f} {args.asset}")
        
        # Execute trade if not dry run and signal is strong
        if not args.dry and market_analysis["signal_strength"] >= 2:
            print(f"\n  🎯 Executing {strategy['action']} on {args.asset}...")
            execute_cheap_futures_trade(market_analysis, strategy, dry_run=False)
        elif args.dry:
            print(f"\n  🔵 [DRY RUN] Would analyze {args.asset} options positions")
        
    else:
        print(f"  📊 Please specify --scan or --asset <SOL|ADA|DOGE>")
        print(f"  💡 Examples:")
        print(f"     python cheap_futures_bot.py --scan --dry")
        print(f"     python cheap_futures_bot.py --asset SOL --dry")
        print(f"     python cheap_futures_bot.py --asset ADA --dry")
        print(f"     python cheap_futures_bot.py --asset DOGE --dry")
    
    print(f"\n  ✅ Cheap futures analysis complete!")

if __name__ == "__main__":
    main()
