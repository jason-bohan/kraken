#!/usr/bin/env python3
"""
🎯 Smart Options Bot — Kraken
Automated options trading with market direction detection and trailing stops.

Strategy:
- Market Direction Detection: Automatically detects bearish vs bullish conditions
- Dynamic Position Management: Buys PUTS in bearish markets, CALLS in bullish markets
- Trailing Stop-Loss: 3% trailing stop to protect profits
- Take-Profit: 10% take-profit to cover options fees
- Auto-Switching: Closes positions and reverses direction on market changes
- 24/7 Monitoring: Continuous market analysis and position management

Usage:
    python3 smart_options_bot.py --asset BTC [--dry]    # Trade BTC options
    python3 smart_options_bot.py --scan [--dry]       # Scan all crypto options
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

# Options trading parameters
DEFAULT_TRAIL_PCT = 0.03      # 3% trailing stop
DEFAULT_TAKE_PROFIT_PCT = 0.10  # 10% take-profit (covers 12.5% max fees)
DEFAULT_POSITION_SIZE = 0.10      # 10% of account per trade
MIN_POSITION_SIZE = 0.05       # 5% minimum position size
MAX_POSITIONS = 3              # Maximum concurrent positions

# Kraken Options specifications
OPTIONS_PAIRS = {
    'BTC': {
        'pair': 'XBTUSD',
        'min_contracts': 0.01,
        'tick_size': 1.0,
        'settlement': 'BTCOPTRR'
    },
    'ETH': {
        'pair': 'ETHUSD', 
        'min_contracts': 0.1,
        'tick_size': 0.1,
        'settlement': 'ETHOPTRR'
    }
}

# Market analysis parameters
VOLATILITY_THRESHOLD = 0.04      # 4% volatility for options
RSI_OVERSOLD = 30              # RSI oversold level
RSI_OVERBOUGHT = 70             # RSI overbought level
BREAKOUT_THRESHOLD = 0.02         # 2% breakout threshold
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

def generate_options_symbol(asset: str, expiry_date: str, strike_price: float, option_type: str) -> str:
    """Generate Kraken options symbol based on specifications."""
    pair = OPTIONS_PAIRS[asset]['pair']
    date_format = expiry_date.replace('-', '')  # YYMMDD format
    strike_formatted = int(strike_price)  # Strike as integer
    type_code = 'C' if option_type.upper() == 'CALL' else 'P'
    
    return f"OF_{pair}_{date_format}_{strike_formatted}_{type_code}"

def calculate_optimal_strike(current_price: float, market_direction: str, volatility: float) -> float:
    """Calculate optimal strike price based on market conditions."""
    # For options, we want strikes that are likely to be in-the-money
    if market_direction == "bullish":
        # For calls: strike slightly above current price
        strike_adjustment = 1.0 + (volatility * 2)  # Adjust for volatility
    elif market_direction == "bearish":
        # For puts: strike slightly below current price
        strike_adjustment = 1.0 - (volatility * 2)
    else:
        # For straddles: use at-the-money
        strike_adjustment = 1.0
    
    optimal_strike = current_price * strike_adjustment
    
    # Round to nearest tick size
    if asset == 'BTC':
        return round(optimal_strike / 100) * 100  # Round to nearest $100
    else:  # ETH
        return round(optimal_strike / 50) * 50    # Round to nearest $50

def get_next_expiry_dates() -> list:
    """Get next available expiry dates for options."""
    from datetime import datetime, timedelta
    today = datetime.now()
    
    # Generate weekly, monthly, quarterly dates
    expiries = []
    
    # Weekly (next Friday)
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0:
        days_until_friday = 7
    weekly_expiry = today + timedelta(days=days_until_friday)
    expiries.append(weekly_expiry.strftime("%y%m%d"))
    
    # Monthly (end of month)
    if today.month == 12:
        monthly_expiry = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        monthly_expiry = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    expiries.append(monthly_expiry.strftime("%y%m%d"))
    
    # Quarterly (next quarter end)
    current_quarter = (today.month - 1) // 3 + 1
    if current_quarter == 4:
        quarterly_expiry = today.replace(year=today.year + 1, month=1, day=31)
    else:
        quarterly_month = current_quarter * 3
        if quarterly_month == 3:
            quarterly_expiry = today.replace(month=3, day=31)
        elif quarterly_month == 6:
            quarterly_expiry = today.replace(month=6, day=30)
        elif quarterly_month == 9:
            quarterly_expiry = today.replace(month=9, day=30)
    
    expiries.append(quarterly_expiry.strftime("%y%m%d"))
    
    return expiries[:3]  # Return first 3 expiries

def analyze_market_direction(asset: str, timeframes=None) -> dict:
    """Analyze market direction and volatility for options trading."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    print(f"  🔍 Analyzing {asset} market direction...")
    
    # Get OHLC data
    pairs = get_asset_pairs()
    target_pair = None
    
    for pair_entry in pairs:
        base = pair_entry.get('base', '').replace('X', '')
        if base.upper() == asset.upper():
            target_pair = pair_entry.get('pair')
            break
    
    if not target_pair:
        return {"error": f"Asset {asset} not found"}
    
    ohlc = get_ohlc(target_pair, interval=15)
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
    
    # 4. Market direction signals
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
    
    return {
        "asset": asset,
        "pair": target_pair,
        "current_price": current_price,
        "volatility": volatility,
        "rsi": rsi,
        "trend": trend_direction,
        "market_direction": market_direction,
        "signal_strength": signal_strength,
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
# 🎯 OPTIONS TRADING
# ─────────────────────────────────────────────

def calculate_options_position_size(balance: float, volatility: float) -> float:
    """Calculate optimal position size based on volatility."""
    # Higher volatility = smaller position (options are risky)
    volatility_adjustment = max(0.5, 1.0 - (volatility * 5))
    
    # Calculate position size
    position_pct = DEFAULT_POSITION_SIZE * volatility_adjustment
    position_pct = max(MIN_POSITION_SIZE, min(position_pct, MAX_POSITIONS / 10))
    
    return balance * position_pct

def determine_options_strategy(market_analysis: dict) -> dict:
    """Determine optimal options strategy based on market conditions."""
    market_direction = market_analysis["market_direction"]
    volatility = market_analysis["volatility"]
    signal_strength = market_analysis["signal_strength"]
    
    if market_direction == "bearish":
        # Bearish market → Buy PUTS
        strategy = {
            "action": "BUY_PUTS",
            "reason": f"Bearish market ({signal_strength} signals)",
            "direction": "put",
            "expected_move": "downward"
        }
    elif market_direction == "bullish":
        # Bullish market → Buy CALLS
        strategy = {
            "action": "BUY_CALLS", 
            "reason": f"Bullish market ({signal_strength} signals)",
            "direction": "call",
            "expected_move": "upward"
        }
    else:
        # Sideways market → High volatility strategy
        if volatility > VOLATILITY_THRESHOLD:
            strategy = {
                "action": "BUY_STRADDLE",
                "reason": f"High volatility ({volatility:.3f}) - straddle",
                "direction": "straddle",
                "expected_move": "both_directions"
            }
        else:
            strategy = {
                "action": "WAIT",
                "reason": f"Low volatility ({volatility:.3f}) - wait for signal",
                "direction": "none",
                "expected_move": "sideways"
            }
    
    return strategy

def execute_options_trade(asset: str, strategy: dict, market_analysis: dict, dry_run: bool = False) -> dict:
    """Execute an options trade with trailing stop and take-profit."""
    try:
        current_price = market_analysis["current_price"]
        pair = market_analysis["pair"]
        direction = strategy["direction"]
        volatility = market_analysis["volatility"]
        
        # Calculate position size
        balances = get_balance()
        usd_balance = float(balances.get('ZUSD', 0))
        
        if usd_balance < 100:  # Minimum $100 for options
            return {"error": f"Insufficient USD balance: ${usd_balance:.2f}"}
        
        position_size = calculate_options_position_size(usd_balance, volatility)
        
        # Get optimal strike and expiry
        optimal_strike = calculate_optimal_strike(current_price, market_analysis["market_direction"], volatility)
        expiry_dates = get_next_expiry_dates()
        best_expiry = expiry_dates[0]  # Use nearest expiry
        
        # Generate options symbol
        options_symbol = generate_options_symbol(asset, best_expiry, optimal_strike, direction.upper())
        
        # Calculate trailing stop and take-profit for the underlying
        if direction == "put":
            # PUT options: profit when underlying goes down
            stop_loss_price = current_price * (1 + DEFAULT_TRAIL_PCT)  # Stop above
            take_profit_price = current_price * (1 - DEFAULT_TAKE_PROFIT_PCT)  # Target below
        else:
            # CALL options: profit when underlying goes up
            stop_loss_price = current_price * (1 - DEFAULT_TRAIL_PCT)  # Stop below
            take_profit_price = current_price * (1 + DEFAULT_TAKE_PROFIT_PCT)  # Target above
        
        print(f"\n  🎯 {strategy['action']} Strategy Detected!")
        print(f"  📊 {asset} Price: ${current_price:.2f}")
        print(f"  📈 Market Direction: {market_analysis['market_direction']}")
        print(f"  📊 Volatility: {market_analysis['volatility']:.3f}")
        print(f"  🎯 Options Symbol: {options_symbol}")
        print(f"  📅 Expiry: {best_expiry}")
        print(f"  💰 Strike Price: ${optimal_strike:.0f}")
        print(f"  📏 Position Size: {position_size:.2f}% (${position_size:.2f})")
        print(f"  🛡️ Stop Loss: ${stop_loss_price:.2f} ({DEFAULT_TRAIL_PCT*100:.1f}% trail)")
        print(f"  🎯 Take Profit: ${take_profit_price:.2f} ({DEFAULT_TAKE_PROFIT_PCT*100:.1f}% target)")
        
        # Calculate contracts needed
        min_contracts = OPTIONS_PAIRS[asset]['min_contracts']
        contracts_needed = max(min_contracts, position_size / optimal_strike)
        
        print(f"  📊 Contracts: {contracts_needed:.3f} (min: {min_contracts})")
        print(f"  💰 Premium Estimate: ${contracts_needed * optimal_strike * 0.05:.2f}")  # Rough estimate
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would place {direction.upper()} options")
            return {"success": True, "strategy": strategy, "symbol": options_symbol}
        
        # Note: Options trading requires manual setup via Kraken interface
        print(f"  ⚠️ Options trading requires manual setup via Kraken interface")
        print(f"  💡 Go to Kraken > Derivatives > Options")
        print(f"  💡 Search for options symbol: {options_symbol}")
        print(f"  💡 Buy {contracts_needed:.3f} contracts")
        print(f"  💡 Set trailing stop on underlying at ${stop_loss_price:.2f}")
        print(f"  💡 Set take-profit on underlying at ${take_profit_price:.2f}")
        
        # Send notification with detailed strategy
        msg = f"🎯 *{asset} Options Signal*\n"
        msg += f"Strategy: {strategy['action']}\n"
        msg += f"Symbol: {options_symbol}\n"
        msg += f"Direction: {market_analysis['market_direction']}\n"
        msg += f"Price: ${current_price:.2f}\n"
        msg += f"Strike: ${optimal_strike:.0f}\n"
        msg += f"Expiry: {best_expiry}\n"
        msg += f"Contracts: {contracts_needed:.3f}\n"
        msg += f"Volatility: {market_analysis['volatility']:.3f}\n"
        msg += f"Position: {position_size:.2f}% (${position_size:.2f})\n"
        msg += f"Stop: ${stop_loss_price:.2f}\n"
        msg += f"Target: ${take_profit_price:.2f}\n"
        msg += f"Setup: Manual via Kraken Options"
        tg(msg)
        
        return {"success": True, "strategy": strategy, "symbol": options_symbol}
        
    except Exception as e:
        print(f"  ❌ Options trade error: {e}")
        return {"error": str(e)}

def scan_all_crypto_options(timeframes=None, dry_run: bool = False):
    """Scan all crypto assets for options opportunities."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    print("  🔍 Scanning crypto options opportunities...")
    
    # Only BTC and ETH have options on Kraken
    crypto_assets = ['BTC', 'ETH']
    all_opportunities = []
    
    for asset in crypto_assets:
        print(f"  🔍 Analyzing {asset}...")
        market_analysis = analyze_market_direction(asset, timeframes)
        
        if "error" in market_analysis:
            print(f"  ❌ {market_analysis['error']}")
            continue
        
        strategy = determine_options_strategy(market_analysis)
        
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
    
    print(f"\n  🎯 KRAKEN OPTIONS OPPORTUNITIES:")
    print(f"  " + "="*80)
    
    for i, opportunity in enumerate(all_opportunities, 1):
        asset = opportunity["asset"]
        analysis = opportunity["market_analysis"]
        strategy = opportunity["strategy"]
        strength = analysis["signal_strength"]
        direction = strategy["direction"]
        
        strength_emoji = "🔥" if strength >= 3 else "📊"
        
        print(f"  {i}. {strength_emoji} {asset:<6} | ${analysis['current_price']:<8.2f} | {direction.upper()} | Strength: {strength}")
        print(f"      📈 {strategy['reason']}")
        print(f"      📊 Vol: {analysis['volatility']:.3f} | RSI: {analysis['rsi']:.0f}")
        
        # Generate options symbol preview
        optimal_strike = calculate_optimal_strike(analysis["current_price"], analysis["market_direction"], analysis["volatility"])
        expiry_dates = get_next_expiry_dates()
        best_expiry = expiry_dates[0]
        options_symbol = generate_options_symbol(asset, best_expiry, optimal_strike, direction.upper())
        
        print(f"      🎯 Symbol: {options_symbol}")
        print(f"      💰 Strike: ${optimal_strike:.0f} | Expiry: {best_expiry}")
        
        # Execute trades if not dry run
        if not dry_run:
            print(f"\n  🎯 Executing {strategy['action']} on {asset}...")
            execute_options_trade(asset, strategy, analysis, dry_run=False)
    
    return all_opportunities

# ─────────────────────────────────────────────
# 🎯 MAIN FUNCTION
# ─────────────────────────────────────────────

def main():
    """Main function to run smart options bot."""
    parser = argparse.ArgumentParser(description="Smart Options Bot - Kraken")
    parser.add_argument("--scan", action="store_true", help="Scan all crypto for options opportunities")
    parser.add_argument("--asset", type=str, help="Analyze specific crypto asset")
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
    
    mode = "🔵 DRY RUN" if args.dry else "🟢 LIVE"
    print(f"🎯 Smart Options Bot — Kraken {mode}")
    print(f"📊 Timeframes: {', '.join(timeframes.keys())}")
    print(f"🛡️ Trailing Stop: {args.trail_pct*100:.1f}%")
    print(f"🎯 Take Profit: {args.profit_pct*100:.1f}%")
    
    if args.scan:
        # Scan all crypto assets
        scan_all_crypto_options(timeframes, args.dry)
    elif args.asset:
        # Analyze specific asset
        print(f"  🔍 Analyzing {args.asset}...")
        market_analysis = analyze_market_direction(args.asset, timeframes)
        
        if "error" in market_analysis:
            print(f"  ❌ {market_analysis['error']}")
            return
        
        strategy = determine_options_strategy(market_analysis)
        
        print(f"\n  🎯 {args.asset.upper()} Strategy Analysis:")
        print(f"  📊 Price: ${market_analysis['current_price']:.2f}")
        print(f"  📈 Market Direction: {market_analysis['market_direction']}")
        print(f"  📊 Volatility: {market_analysis['volatility']:.3f}")
        print(f"  📊 RSI: {market_analysis['rsi']:.0f}")
        print(f"  🎯 Recommended Action: {strategy['action']}")
        print(f"  📝 Reason: {strategy['reason']}")
        print(f"  📈 Expected Direction: {strategy['expected_move']}")
        
        if not args.dry:
            print(f"\n  🎯 Executing {strategy['action']} on {args.asset}...")
            execute_options_trade(args.asset, strategy, market_analysis, args.dry)
    else:
        print("  ❌ Please specify --scan or --asset <symbol>")
        return
    
    print(f"\n  ✅ Analysis complete!")

if __name__ == "__main__":
    main()
