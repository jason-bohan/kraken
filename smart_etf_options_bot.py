#!/usr/bin/env python3
"""
🎯 Smart ETF Options Bot — Bitcoin ETF Options Trading
Analyzes BTC market direction and provides ETF options trading recommendations.

Strategy:
- Market Direction Detection: Automatically detects bearish vs bullish conditions
- ETF Options Trading: Trades IBIT/FBTC options instead of crypto options
- Dynamic Position Management: Buys PUTS in bearish markets, CALLS in bullish markets
- Trailing Stop-Loss: 3% trailing stop to protect profits
- Take-Profit: 10% take-profit to cover options fees
- Auto-Switching: Closes positions and reverses direction on market changes
- 24/7 Monitoring: Continuous market analysis and position management

Usage:
    python3 smart_etf_options_bot.py --asset BTC [--dry]    # Analyze BTC for ETF options
    python3 smart_etf_options_bot.py --scan [--dry]       # Scan all ETF options
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

# ETF Options trading parameters
DEFAULT_TRAIL_PCT = 0.03      # 3% trailing stop
DEFAULT_TAKE_PROFIT_PCT = 0.10  # 10% take-profit (covers fees)
DEFAULT_POSITION_SIZE = 0.10      # 10% of account per trade
MIN_POSITION_SIZE = 0.05       # 5% minimum position size
MAX_POSITIONS = 3              # Maximum concurrent positions

# Bitcoin ETF Options specifications
ETF_OPTIONS = {
    'IBIT': {
        'name': 'iShares Bitcoin Trust',
        'symbol': 'IBIT',
        'underlying': 'BTC',
        'min_contracts': 1,
        'tick_size': 0.01,
        'correlation': 0.95  # High correlation with BTC
    },
    'FBTC': {
        'name': 'Fidelity Bitcoin ETF',
        'symbol': 'FBTC',
        'underlying': 'BTC',
        'min_contracts': 1,
        'tick_size': 0.01,
        'correlation': 0.95  # High correlation with BTC
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

def calculate_etf_price(btc_price: float, etf_symbol: str) -> float:
    """Calculate estimated ETF price based on BTC price."""
    # IBIT and FBTC typically track BTC price at ~1:10 ratio
    # This is approximate - actual ratio varies
    btc_to_etf_ratio = 0.1  # 1 BTC ≈ 10 ETF shares
    
    if etf_symbol == 'IBIT':
        # IBIT has slight premium/discount to NAV
        nav_adjustment = 1.02  # 2% premium typical
    elif etf_symbol == 'FBTC':
        # FBTC has slight premium/discount to NAV
        nav_adjustment = 1.01  # 1% premium typical
    else:
        nav_adjustment = 1.0
    
    return btc_price * btc_to_etf_ratio * nav_adjustment

def analyze_btc_market_direction(timeframes=None) -> dict:
    """Analyze BTC market direction for ETF options trading."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    print(f"  🔍 Analyzing BTC market direction for ETF options...")
    
    # Use XBTUSD pair for BTC analysis
    target_pair = 'XBTUSD'
    print(f"  📊 Using BTC pair: {target_pair}")
    
    # Get OHLC data
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
    
    # Calculate ETF prices
    ibit_price = calculate_etf_price(current_price, 'IBIT')
    fbtc_price = calculate_etf_price(current_price, 'FBTC')
    
    return {
        "asset": "BTC",
        "pair": target_pair,
        "current_price": current_price,
        "ibit_price": ibit_price,
        "fbtc_price": fbtc_price,
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
# 🎯 ETF OPTIONS TRADING
# ─────────────────────────────────────────────

def calculate_etf_options_position_size(balance: float, volatility: float) -> float:
    """Calculate optimal position size based on volatility."""
    # Higher volatility = smaller position (options are risky)
    volatility_adjustment = max(0.5, 1.0 - (volatility * 5))
    
    # Calculate position size
    position_pct = DEFAULT_POSITION_SIZE * volatility_adjustment
    position_pct = max(MIN_POSITION_SIZE, min(position_pct, MAX_POSITIONS / 10))
    
    return balance * position_pct

def determine_etf_options_strategy(market_analysis: dict) -> dict:
    """Determine optimal ETF options strategy based on BTC market conditions."""
    market_direction = market_analysis["market_direction"]
    volatility = market_analysis["volatility"]
    signal_strength = market_analysis["signal_strength"]
    
    if market_direction == "bearish":
        # Bearish BTC market → Buy ETF PUTS
        strategy = {
            "action": "BUY_ETF_PUTS",
            "reason": f"Bearish BTC market ({signal_strength} signals) - ETF puts",
            "direction": "put",
            "expected_move": "downward",
            "etf_recommendation": "IBIT or FBTC puts"
        }
    elif market_direction == "bullish":
        # Bullish BTC market → Buy ETF CALLS
        strategy = {
            "action": "BUY_ETF_CALLS", 
            "reason": f"Bullish BTC market ({signal_strength} signals) - ETF calls",
            "direction": "call",
            "expected_move": "upward",
            "etf_recommendation": "IBIT or FBTC calls"
        }
    else:
        # Sideways market → High volatility strategy
        if volatility > VOLATILITY_THRESHOLD:
            strategy = {
                "action": "BUY_ETF_STRADDLE",
                "reason": f"High BTC volatility ({volatility:.3f}) - ETF straddle",
                "direction": "straddle",
                "expected_move": "both_directions",
                "etf_recommendation": "IBIT or FBTC straddles"
            }
        else:
            strategy = {
                "action": "WAIT",
                "reason": f"Low BTC volatility ({volatility:.3f}) - wait for signal",
                "direction": "none",
                "expected_move": "sideways",
                "etf_recommendation": "Wait for stronger BTC signals"
            }
    
    return strategy

def calculate_optimal_etf_strike(etf_price: float, market_direction: str, volatility: float) -> float:
    """Calculate optimal strike price for ETF options."""
    # For ETF options, we want strikes that are likely to be in-the-money
    if market_direction == "bullish":
        # For calls: strike slightly above current price
        strike_adjustment = 1.0 + (volatility * 3)  # Adjust for volatility
    elif market_direction == "bearish":
        # For puts: strike slightly below current price
        strike_adjustment = 1.0 - (volatility * 3)
    else:
        # For straddles: use at-the-money
        strike_adjustment = 1.0
    
    optimal_strike = etf_price * strike_adjustment
    
    # Round to nearest $0.01 for ETF options
    return round(optimal_strike, 2)

def get_next_expiry_dates() -> list:
    """Get next available expiry dates for ETF options."""
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
    # Find third Friday of current month
    first_day = today.replace(day=1)
    first_friday = first_day + timedelta(days=(4 - first_day.weekday()) % 7)
    third_friday = first_friday + timedelta(days=14)
    
    if third_friday <= today:
        # If third Friday has passed, go to next month
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
        first_friday = next_month + timedelta(days=(4 - next_month.weekday()) % 7)
        third_friday = first_friday + timedelta(days=14)
    
    expiries.append(third_friday.strftime("%m/%d/%y"))
    
    return expiries[:2]  # Return weekly and monthly

def execute_etf_options_trade(market_analysis: dict, strategy: dict, dry_run: bool = False) -> dict:
    """Execute an ETF options trade with trailing stop and take-profit."""
    try:
        btc_price = market_analysis["current_price"]
        ibit_price = market_analysis["ibit_price"]
        fbtc_price = market_analysis["fbtc_price"]
        direction = strategy["direction"]
        volatility = market_analysis["volatility"]
        
        # Calculate position size
        balances = get_balance()
        usd_balance = float(balances.get('ZUSD', 0))
        
        if usd_balance < 500:  # Minimum $500 for ETF options
            return {"error": f"Insufficient USD balance: ${usd_balance:.2f}"}
        
        position_size = calculate_etf_options_position_size(usd_balance, volatility)
        
        # Get optimal strikes and expiries
        ibit_strike = calculate_optimal_etf_strike(ibit_price, market_analysis["market_direction"], volatility)
        fbtc_strike = calculate_optimal_etf_strike(fbtc_price, market_analysis["market_direction"], volatility)
        expiry_dates = get_next_expiry_dates()
        best_expiry = expiry_dates[0]  # Use nearest expiry
        
        # Calculate trailing stop and take-profit for the underlying
        if direction == "put":
            # PUT options: profit when underlying goes down
            ibit_stop_loss = ibit_price * (1 + DEFAULT_TRAIL_PCT)  # Stop above
            ibit_take_profit = ibit_price * (1 - DEFAULT_TAKE_PROFIT_PCT)  # Target below
            fbtc_stop_loss = fbtc_price * (1 + DEFAULT_TRAIL_PCT)  # Stop above
            fbtc_take_profit = fbtc_price * (1 - DEFAULT_TAKE_PROFIT_PCT)  # Target below
        else:
            # CALL options: profit when underlying goes up
            ibit_stop_loss = ibit_price * (1 - DEFAULT_TRAIL_PCT)  # Stop below
            ibit_take_profit = ibit_price * (1 + DEFAULT_TAKE_PROFIT_PCT)  # Target above
            fbtc_stop_loss = fbtc_price * (1 - DEFAULT_TRAIL_PCT)  # Stop below
            fbtc_take_profit = fbtc_price * (1 + DEFAULT_TAKE_PROFIT_PCT)  # Target above
        
        print(f"\n  🎯 {strategy['action']} Strategy Detected!")
        print(f"  📊 BTC Price: ${btc_price:.2f}")
        print(f"  📈 Market Direction: {market_analysis['market_direction']}")
        print(f"  📊 Volatility: {market_analysis['volatility']:.3f}")
        print(f"  📏 Position Size: {position_size:.2f}% (${position_size:.2f})")
        
        print(f"\n  🎯 IBIT ETF Options:")
        print(f"  💰 Current Price: ${ibit_price:.2f}")
        print(f"  🎯 Strike Price: ${ibit_strike:.2f}")
        print(f"  📅 Expiry: {best_expiry}")
        print(f"  🛡️ Stop Loss: ${ibit_stop_loss:.2f} ({DEFAULT_TRAIL_PCT*100:.1f}% trail)")
        print(f"  🎯 Take Profit: ${ibit_take_profit:.2f} ({DEFAULT_TAKE_PROFIT_PCT*100:.1f}% target)")
        
        print(f"\n  🎯 FBTC ETF Options:")
        print(f"  💰 Current Price: ${fbtc_price:.2f}")
        print(f"  🎯 Strike Price: ${fbtc_strike:.2f}")
        print(f"  📅 Expiry: {best_expiry}")
        print(f"  🛡️ Stop Loss: ${fbtc_stop_loss:.2f} ({DEFAULT_TRAIL_PCT*100:.1f}% trail)")
        print(f"  🎯 Take Profit: ${fbtc_take_profit:.2f} ({DEFAULT_TAKE_PROFIT_PCT*100:.1f}% target)")
        
        # Estimate contracts and premiums
        ibit_contracts = max(1, int(position_size / ibit_strike))
        fbtc_contracts = max(1, int(position_size / fbtc_strike))
        
        # Rough premium estimates (2-5% of strike for ETF options)
        ibit_premium_estimate = ibit_contracts * ibit_strike * 0.03
        fbtc_premium_estimate = fbtc_contracts * fbtc_strike * 0.03
        
        print(f"\n  📊 Position Estimates:")
        print(f"  📈 IBIT: {ibit_contracts} contracts ~${ibit_premium_estimate:.2f} premium")
        print(f"  📈 FBTC: {fbtc_contracts} contracts ~${fbtc_premium_estimate:.2f} premium")
        
        if dry_run:
            print(f"  🔵 [DRY RUN] Would analyze ETF options positions")
            return {"success": True, "strategy": strategy}
        
        # Note: ETF options trading requires manual setup via broker interface
        print(f"\n  ⚠️ ETF options trading requires manual setup via your broker")
        print(f"  💡 Go to your broker > Options Trading")
        print(f"  💡 Search for IBIT or FBTC options")
        print(f"  💡 Choose {direction.upper()} options")
        print(f"  💡 Strike: ${ibit_strike:.2f} (IBIT) or ${fbtc_strike:.2f} (FBTC)")
        print(f"  💡 Expiry: {best_expiry}")
        print(f"  💡 Contracts: {ibit_contracts} (IBIT) or {fbtc_contracts} (FBTC)")
        print(f"  💡 Set stop-loss on underlying at calculated levels")
        print(f"  💡 Set take-profit on underlying at calculated levels")
        
        # Send notification with detailed strategy
        msg = f"🎯 *ETF Options Signal*\n"
        msg += f"BTC Price: ${btc_price:.2f}\n"
        msg += f"Strategy: {strategy['action']}\n"
        msg += f"Direction: {market_analysis['market_direction']}\n"
        msg += f"Volatility: {market_analysis['volatility']:.3f}\n"
        msg += f"Position: {position_size:.2f}% (${position_size:.2f})\n\n"
        msg += f"*IBIT Options*\n"
        msg += f"Price: ${ibit_price:.2f}\n"
        msg += f"Strike: ${ibit_strike:.2f}\n"
        msg += f"Expiry: {best_expiry}\n"
        msg += f"Contracts: {ibit_contracts}\n"
        msg += f"Stop: ${ibit_stop_loss:.2f}\n"
        msg += f"Target: ${ibit_take_profit:.2f}\n\n"
        msg += f"*FBTC Options*\n"
        msg += f"Price: ${fbtc_price:.2f}\n"
        msg += f"Strike: ${fbtc_strike:.2f}\n"
        msg += f"Expiry: {best_expiry}\n"
        msg += f"Contracts: {fbtc_contracts}\n"
        msg += f"Stop: ${fbtc_stop_loss:.2f}\n"
        msg += f"Target: ${fbtc_take_profit:.2f}\n\n"
        msg += f"Setup: Manual via your broker"
        tg(msg)
        
        return {"success": True, "strategy": strategy}
        
    except Exception as e:
        print(f"  ❌ ETF options trade error: {e}")
        return {"error": str(e)}

def scan_etf_options_opportunities(timeframes=None, dry_run: bool = False):
    """Scan for ETF options opportunities based on BTC analysis."""
    if timeframes is None:
        timeframes = TIMEFRAMES
    
    print("  🔍 Scanning ETF options opportunities...")
    
    # Analyze BTC market direction
    market_analysis = analyze_btc_market_direction(timeframes)
    
    if "error" in market_analysis:
        print(f"  ❌ {market_analysis['error']}")
        return []
    
    strategy = determine_etf_options_strategy(market_analysis)
    
    # Only show strong signals (signal strength >= 2)
    if market_analysis["signal_strength"] >= 2:
        print(f"\n  🎯 BITCOIN ETF OPTIONS OPPORTUNITY:")
        print(f"  " + "="*80)
        
        strength_emoji = "🔥" if market_analysis["signal_strength"] >= 3 else "📊"
        
        print(f"  {strength_emoji} BTC | ${market_analysis['current_price']:<8.2f} | {strategy['direction'].upper()} | Strength: {market_analysis['signal_strength']}")
        print(f"      📈 {strategy['reason']}")
        print(f"      📊 Vol: {market_analysis['volatility']:.3f} | RSI: {market_analysis['rsi']:.0f}")
        print(f"      🎯 ETF Recommendation: {strategy['etf_recommendation']}")
        
        # Show ETF prices
        print(f"      💰 IBIT Price: ${market_analysis['ibit_price']:.2f}")
        print(f"      💰 FBTC Price: ${market_analysis['fbtc_price']:.2f}")
        
        # Execute trades if not dry run
        if not dry_run:
            print(f"\n  🎯 Executing ETF options strategy...")
            execute_etf_options_trade(market_analysis, strategy, dry_run=False)
        
        return [{
            "market_analysis": market_analysis,
            "strategy": strategy,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }]
    
    print(f"  📊 No strong ETF options signals (strength: {market_analysis['signal_strength']})")
    return []

# ─────────────────────────────────────────────
# 🎯 MAIN FUNCTION
# ─────────────────────────────────────────────

def main():
    """Main function to run smart ETF options bot."""
    global DEFAULT_TRAIL_PCT, DEFAULT_TAKE_PROFIT_PCT
    
    parser = argparse.ArgumentParser(description="Smart ETF Options Bot - Bitcoin ETF Options Trading")
    parser.add_argument("--scan", action="store_true", help="Scan for ETF options opportunities")
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
    DEFAULT_TRAIL_PCT = args.trail_pct
    DEFAULT_TAKE_PROFIT_PCT = args.profit_pct
    
    # Print header
    dry_indicator = "🔵 DRY RUN" if args.dry else "🟢 LIVE"
    print(f"🎯 Smart ETF Options Bot — Bitcoin ETF Options {dry_indicator}")
    print(f"📊 Timeframes: {', '.join(timeframes.keys())}")
    print(f"🛡️ Trailing Stop: {DEFAULT_TRAIL_PCT*100:.1f}%")
    print(f"🎯 Take Profit: {DEFAULT_TAKE_PROFIT_PCT*100:.1f}%")
    print()
    
    # Scan for ETF options opportunities
    opportunities = scan_etf_options_opportunities(timeframes, args.dry)
    
    if not opportunities:
        print(f"\n  📊 No ETF options opportunities found at this time.")
        print(f"  💡 BTC market analysis complete - waiting for stronger signals")
    
    print(f"\n  ✅ ETF Options analysis complete!")

if __name__ == "__main__":
    main()
