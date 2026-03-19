#!/usr/bin/env python3
"""
📈 Dynamic Stock Swing Trading Bot — Advanced Technical Analysis
Identifies high-probability swing trades for any stock using multiple indicators and risk management.

Usage:
    python3 stock_swing_bot.py AAPL          # trade AAPL
    python3 stock_swing_bot.py TSLA --dry    # dry run for TSLA
    python3 stock_swing_bot.py MSFT          # trade MSFT
"""

import os
import time
import argparse
import signal
import sys
import math
from datetime import datetime, timedelta
from kraken_connection import get_ticker, get_ohlc, get_balance, get_orderbook, place_order, place_bracket_order, get_open_orders, cancel_order
from kraken_connection import calculate_order_size
from learning_engine import LearningEngine

# ─────────────────────────────────────────────
# 📈 DYNAMIC STOCK CONFIGURATION
# ─────────────────────────────────────────────

def get_stock_config(symbol: str) -> dict:
    """Get configuration for a specific asset (crypto or stock)."""
    # Crypto configurations (optimized for swing trading)
    crypto_configs = {
        "BTC": {
            "rsi_oversold": 40,
            "rsi_overbought": 70,
            "dip_min": 0.02,
            "dip_max": 0.06,
            "profit_target": 0.10,
            "stop_loss": 0.08,
            "min_trade": 50.0,
            "volatility_threshold": 0.03
        },
        "ETH": {
            "rsi_oversold": 35,
            "rsi_overbought": 75,
            "dip_min": 0.03,
            "dip_max": 0.08,
            "profit_target": 0.10,
            "stop_loss": 0.08,
            "min_trade": 30.0,
            "volatility_threshold": 0.04
        },
        "SOL": {
            "rsi_oversold": 30,
            "rsi_overbought": 80,
            "dip_min": 0.05,
            "dip_max": 0.12,
            "profit_target": 0.10,
            "stop_loss": 0.08,
            "min_trade": 20.0,
            "volatility_threshold": 0.06
        },
        "ADA": {
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "dip_min": 0.04,
            "dip_max": 0.10,
            "profit_target": 0.10,
            "stop_loss": 0.08,
            "min_trade": 20.0,
            "volatility_threshold": 0.05
        },
        "DOT": {
            "rsi_oversold": 35,
            "rsi_overbought": 75,
            "dip_min": 0.04,
            "dip_max": 0.09,
            "profit_target": 0.10,
            "stop_loss": 0.08,
            "min_trade": 20.0,
            "volatility_threshold": 0.05
        }
    }
    
    # Stock configurations (for brokers that support stocks)
    stock_configs = {
        "AAPL": {
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "dip_min": 0.03,
            "dip_max": 0.08,
            "profit_target": 0.06,
            "stop_loss": 0.03,
            "min_trade": 100.0,
            "volatility_threshold": 0.05
        },
        "TSLA": {
            "rsi_oversold": 35,
            "rsi_overbought": 75,
            "dip_min": 0.04,
            "dip_max": 0.10,
            "profit_target": 0.08,
            "stop_loss": 0.04,
            "min_trade": 100.0,
            "volatility_threshold": 0.08
        }
    }
    
    # Return symbol-specific config or default
    symbol_upper = symbol.upper()
    return crypto_configs.get(symbol_upper) or stock_configs.get(symbol_upper) or {
        "rsi_oversold": 35,
        "rsi_overbought": 70,
        "dip_min": 0.03,
        "dip_max": 0.08,
        "profit_target": 0.10,
        "stop_loss": 0.08,
        "min_trade": 50.0,
        "volatility_threshold": 0.05
    }

# 📊 Technical Analysis Parameters
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOLUME_MA_PERIOD = 20
ATR_PERIOD = 14

# 🎯 Trading Parameters
MAX_POSITION_SIZE = 0.1  # Max 10% of portfolio
RISK_PER_TRADE = 0.02    # 2% max risk
MIN_RISK_REWARD = 2.0     # 1:2 minimum risk/reward
TRAILING_STOP_PCT = 0.02   # 2% trailing stop

# ⏱️ Timeframes for analysis
TIMEFRAMES = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240
}

# 📱 Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# 📈 Trading state (will be set dynamically)
in_position = False
entry_price = 0
stop_loss = 0
take_profit = 0
trailing_stop = 0
position_size = 0
last_signal = None
trade_history = []
SYMBOL = ""
PAIR = ""
ASSET = ""
CONFIG = {}
BOT_NAME = "stock_swing_bot"
LEARNER = LearningEngine()
active_trade_id = None

# 🎯 Order management
stop_loss_order_id = None
take_profit_order_id = None
trailing_stop_order_id = None

# ─────────────────────────────────────────────
# TELEGRAM NOTIFICATIONS
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


def extract_learning_features(signal_info: dict, current_price: float) -> dict:
    """Capture a compact feature snapshot at trade entry for later evaluation."""
    analysis = signal_info.get("analysis", {})
    one_hour = analysis.get("1h", {})
    fifteen_min = analysis.get("15m", {})
    five_min = analysis.get("5m", {})
    return {
        "price": current_price,
        "confidence": float(signal_info.get("confidence", 0.0)),
        "signal_reason": signal_info.get("reason", ""),
        "rsi_1h": one_hour.get("rsi"),
        "rsi_15m": fifteen_min.get("rsi"),
        "rsi_5m": five_min.get("rsi"),
        "atr_1h": one_hour.get("atr"),
        "trend_1h": one_hour.get("trend"),
        "trend_4h": analysis.get("4h", {}).get("trend"),
        "volume_trend_1h": one_hour.get("volume", {}).get("trend"),
        "macd_histogram_1h": one_hour.get("macd", {}).get("histogram"),
        "price_change_20_1h": one_hour.get("price_change_20"),
    }


def load_learning_config(symbol: str, base_config: dict) -> dict:
    """Apply bounded parameter tuning from recent closed trades."""
    base_config.setdefault("min_confidence", 0.60)
    tuned_config, metrics = LEARNER.tune_config(BOT_NAME, symbol, base_config)
    print(
        f"  Learning: {metrics['sample_size']} closed trades | "
        f"win rate {metrics['win_rate'] * 100:.1f}% | "
        f"avg P&L {metrics['avg_pnl_pct'] * 100:+.2f}%"
    )
    if metrics.get("tuning_applied"):
        print(
            f"  Tuned params: confidence>={tuned_config['min_confidence']:.2f}, "
            f"RSI<{tuned_config['rsi_oversold']}, "
            f"target {tuned_config['profit_target'] * 100:.1f}%, "
            f"stop {tuned_config['stop_loss'] * 100:.1f}%"
        )
    else:
        print(f"  Learning status: {metrics.get('tuning_reason', 'not enough data')}")
    return tuned_config


# ─────────────────────────────────────────────
# TECHNICAL ANALYSIS FUNCTIONS
# ─────────────────────────────────────────────

def calculate_rsi(prices: list, period: int = 14) -> float:
    """Calculate RSI indicator."""
    if len(prices) < period + 1:
        return 50.0
    
    gains, losses = [], []
    for i in range(1, len(prices)):
        delta = prices[i] - prices[i-1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))
    
    if len(gains) < period:
        return 50.0
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_macd(prices: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """Calculate MACD indicator."""
    if len(prices) < slow:
        return {"macd": 0, "signal": 0, "histogram": 0}
    
    # Calculate EMAs
    def ema(data: list, period: int) -> float:
        multiplier = 2 / (period + 1)
        ema_val = sum(data[-period:]) / period
        for price in data[-period:]:
            ema_val = (price * multiplier) + (ema_val * (1 - multiplier))
        return ema_val
    
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    macd_line = fast_ema - slow_ema
    
    # For signal line, we'd need historical MACD values - simplified here
    signal_line = macd_line * 0.9  # Simplified
    histogram = macd_line - signal_line
    
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram
    }

def calculate_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Calculate Average True Range for volatility."""
    if len(highs) < period + 1:
        return 0.0
    
    tr_values = []
    for i in range(1, len(highs)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i-1])
        low_close = abs(lows[i] - closes[i-1])
        tr = max(high_low, high_close, low_close)
        tr_values.append(tr)
    
    if len(tr_values) < period:
        return sum(tr_values) / len(tr_values) if tr_values else 0.0
    
    return sum(tr_values[-period:]) / period

def find_support_resistance(prices: list, lookback: int = 20) -> dict:
    """Find key support and resistance levels."""
    if len(prices) < lookback * 2:
        return {"support": 0, "resistance": 0}
    
    recent_prices = prices[-lookback:]
    support = min(recent_prices)
    resistance = max(recent_prices)
    
    return {
        "support": support,
        "resistance": resistance,
        "mid_range": (support + resistance) / 2
    }

def analyze_volume(candles: list) -> dict:
    """Analyze volume patterns."""
    if len(candles) < VOLUME_MA_PERIOD:
        return {"volume_ratio": 1.0, "trend": "neutral"}
    
    volumes = [float(c[5]) for c in candles]  # Volume is at index 5
    current_volume = volumes[-1]
    avg_volume = sum(volumes[-VOLUME_MA_PERIOD:-1]) / (VOLUME_MA_PERIOD - 1)
    
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
    
    # Determine volume trend
    if volume_ratio > 1.5:
        trend = "high"
    elif volume_ratio > 1.2:
        trend = "above_average"
    elif volume_ratio < 0.8:
        trend = "low"
    else:
        trend = "average"
    
    return {
        "volume_ratio": volume_ratio,
        "trend": trend,
        "current": current_volume,
        "average": avg_volume
    }

def multi_timeframe_analysis(symbol: str) -> dict:
    """Analyze multiple timeframes for confluence."""
    analysis = {}
    
    for timeframe_name, timeframe_minutes in TIMEFRAMES.items():
        try:
            candles = get_ohlc(PAIR, interval=timeframe_minutes)
            if not candles or len(candles) < 50:
                continue
            
            closes = [float(c[4]) for c in candles]
            highs = [float(c[2]) for c in candles]
            lows = [float(c[3]) for c in candles]
            
            current_price = closes[-1]
            
            # Technical indicators
            rsi = calculate_rsi(closes, RSI_PERIOD)
            macd = calculate_macd(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
            atr = calculate_atr(highs, lows, closes, ATR_PERIOD)
            sr = find_support_resistance(closes)
            volume = analyze_volume(candles)
            
            # Trend determination
            price_change = (current_price - closes[-20]) / closes[-20] if len(closes) > 20 else 0
            
            analysis[timeframe_name] = {
                "price": current_price,
                "rsi": rsi,
                "macd": macd,
                "atr": atr,
                "support": sr["support"],
                "resistance": sr["resistance"],
                "volume": volume,
                "trend": "bullish" if price_change > 0.02 else "bearish" if price_change < -0.02 else "sideways",
                "price_change_20": price_change
            }
            
        except Exception as e:
            print(f"  ⚠️ Error analyzing {timeframe_name}: {e}")
            continue
    
    return analysis

def generate_signal(analysis: dict, config: dict) -> dict:
    """Generate trading signal based on multi-timeframe confluence."""
    if not analysis:
        return {"signal": "hold", "confidence": 0, "reason": "No data"}
    
    # Count bullish/bearish signals across timeframes
    bullish_signals = 0
    bearish_signals = 0
    total_weight = 0
    weighted_score = 0
    
    signal_details = []
    
    # Higher timeframes have more weight
    timeframe_weights = {"5m": 1, "15m": 2, "1h": 3, "4h": 4}
    
    for tf, data in analysis.items():
        weight = timeframe_weights.get(tf, 1)
        total_weight += weight
        
        # RSI signals (using stock-specific thresholds)
        if data["rsi"] < config["rsi_oversold"]:
            bullish_signals += weight
            weighted_score += weight * 2
            signal_details.append(f"{tf}: RSI oversold ({data['rsi']:.1f})")
        elif data["rsi"] > config["rsi_overbought"]:
            bearish_signals += weight
            weighted_score -= weight * 2
            signal_details.append(f"{tf}: RSI overbought ({data['rsi']:.1f})")
        
        # MACD signals
        if data["macd"]["histogram"] > 0:
            bullish_signals += weight
            weighted_score += weight
            signal_details.append(f"{tf}: MACD bullish")
        elif data["macd"]["histogram"] < 0:
            bearish_signals += weight
            weighted_score -= weight
            signal_details.append(f"{tf}: MACD bearish")
        
        # Trend signals
        if data["trend"] == "bullish":
            bullish_signals += weight * 0.5
            weighted_score += weight * 0.5
        elif data["trend"] == "bearish":
            bearish_signals += weight * 0.5
            weighted_score -= weight * 0.5
        
        # Volume confirmation
        if data["volume"]["trend"] in ["high", "above_average"]:
            if bullish_signals > bearish_signals:
                weighted_score += weight * 0.3
            elif bearish_signals > bullish_signals:
                weighted_score -= weight * 0.3
    
    # Determine final signal
    confidence = abs(weighted_score) / (total_weight * 2) if total_weight > 0 else 0
    
    if weighted_score > total_weight * 0.3:  # Strong bullish
        signal = "buy"
    elif weighted_score < -total_weight * 0.3:  # Strong bearish
        signal = "sell"
    else:
        signal = "hold"
    
    # Additional filters using stock-specific config
    current_price = analysis.get("1h", {}).get("price", 0)
    
    # Avoid trading in overly volatile markets
    if signal != "hold":
        atr_1h = analysis.get("1h", {}).get("atr", 0)
        if atr_1h > 0 and atr_1h / current_price > config["volatility_threshold"]:
            confidence *= 0.7
            signal_details.append("High volatility warning")
    
    return {
        "signal": signal,
        "confidence": confidence,
        "reason": "; ".join(signal_details[:4]),  # Top 4 reasons
        "price": current_price,
        "analysis": analysis
    }

# ─────────────────────────────────────────────
# POSITION MANAGEMENT
# ─────────────────────────────────────────────

def calculate_position_size(entry_price: float, stop_loss_price: float, account_balance: float) -> float:
    """Calculate optimal position size based on risk management."""
    risk_amount = account_balance * RISK_PER_TRADE
    risk_per_share = abs(entry_price - stop_loss_price)
    
    if risk_per_share == 0:
        return 0
    
    max_shares = risk_amount / risk_per_share
    max_position_value = account_balance * MAX_POSITION_SIZE
    max_shares_by_value = max_position_value / entry_price
    
    # Take the more conservative limit
    shares = min(max_shares, max_shares_by_value)
    
    # Ensure minimum trade size
    if shares * entry_price < CONFIG["min_trade"]:
        shares = CONFIG["min_trade"] / entry_price
    
    return shares

def place_limit_orders(entry_price: float, position_shares: float, stop_loss_price: float, take_profit_price: float):
    """Place standalone stop-loss for downside protection. Bot loop handles TP via market sell."""
    global stop_loss_order_id, take_profit_order_id

    try:
        # Cancel any existing orders first
        cancel_all_limit_orders()

        if stop_loss_price > 0 and position_shares > 0:
            print(f"  🛡️ Placing SL protection @ ${stop_loss_price:.2f} | TP target @ ${take_profit_price:.2f} (bot loop)")
            ok, info = place_order(
                pair=PAIR,
                side="sell",
                order_type="stop-loss",
                volume=round(position_shares, 8),
                price=stop_loss_price,
            )

            if ok:
                txids = info.get('txid', [])
                stop_loss_order_id = txids[0] if txids else None
                take_profit_order_id = None  # TP handled by bot loop, not a standing order
                print(f"  ✅ SL protection placed: {stop_loss_order_id}")
            else:
                print(f"  ❌ SL order failed: {info}")

        return True

    except Exception as e:
        print(f"  ❌ Error placing limit orders: {e}")
        return False

def cancel_all_limit_orders():
    """Cancel all existing limit orders."""
    global stop_loss_order_id, take_profit_order_id, trailing_stop_order_id
    
    orders_to_cancel = [
        ("Stop-loss", stop_loss_order_id),
        ("Take-profit", take_profit_order_id),
        ("Trailing stop", trailing_stop_order_id)
    ]
    
    for order_name, order_id in orders_to_cancel:
        if order_id:
            try:
                print(f"  🗑️ Canceling {order_name.lower()} order: {order_id}")
                cancel_result, cancel_info = cancel_order(order_id)
                if cancel_result:
                    print(f"  ✅ {order_name} order canceled")
                else:
                    print(f"  ❌ Failed to cancel {order_name.lower()}: {cancel_info}")
            except Exception as e:
                print(f"  ⚠️ Error canceling {order_name.lower()}: {e}")
    
    # Reset order IDs
    stop_loss_order_id = None
    take_profit_order_id = None
    trailing_stop_order_id = None

def check_order_status():
    """Check if the standalone stop-loss or trailing stop has been filled."""
    global stop_loss_order_id, take_profit_order_id, trailing_stop_order_id, in_position

    try:
        # Nothing to check if no orders placed
        if not stop_loss_order_id and not trailing_stop_order_id:
            return False

        # get_open_orders() returns {txid: order_data, ...}
        open_orders = get_open_orders()
        open_txids = set(open_orders.keys())

        sl_open = stop_loss_order_id in open_txids if stop_loss_order_id else False
        ts_open = trailing_stop_order_id in open_txids if trailing_stop_order_id else False

        sl_fired = (stop_loss_order_id and not sl_open)
        ts_fired = (trailing_stop_order_id and not ts_open)

        if sl_fired or ts_fired:
            ticker = get_ticker(PAIR)
            current_price = float(ticker.get("c", [0])[0]) if ticker else entry_price

            exit_reason = "trailing_stop" if ts_fired else "stop_loss"
            print(f"  🎯 {exit_reason.replace('_', ' ').title()} order filled!")

            # Cancel any remaining orders
            cancel_all_limit_orders()

            # Reconcile state — position already closed by exchange, no new order needed
            execute_sell(current_price, exit_reason, dry_run=False, skip_order=True)

            return True

        return False

    except Exception as e:
        print(f"  ⚠️ Error checking order status: {e}")
        return False

def update_trailing_stop_order(current_price: float):
    """Update trailing stop loss order."""
    global trailing_stop_order_id
    
    if not in_position or trailing_stop == 0:
        return
    
    # Calculate new trailing stop
    new_trailing_stop = current_price * (1 - TRAILING_STOP_PCT)
    
    # Only update if trailing stop moved up significantly (at least 1%)
    if trailing_stop_order_id and new_trailing_stop > trailing_stop * 1.01:
        print(f"  📈 Updating trailing stop: ${trailing_stop:.2f} → ${new_trailing_stop:.2f}")
        
        try:
            # Cancel old trailing stop
            cancel_result, cancel_info = cancel_order(trailing_stop_order_id)
            if cancel_result:
                print(f"  ✅ Old trailing stop canceled")
                
                # Place new trailing stop
                trail_result, trail_info = place_order(
                    pair=PAIR,
                    side="sell",
                    order_type="stop-loss",
                    volume=position_size,
                    price=new_trailing_stop,
                    validate=False
                )
                
                if trail_result:
                    trailing_stop_order_id = trail_info.get('txid', [None])[0]
                    trailing_stop = new_trailing_stop
                    print(f"  ✅ New trailing stop placed: {trailing_stop_order_id}")
                else:
                    print(f"  ❌ Failed to place new trailing stop: {trail_info}")
            
        except Exception as e:
            print(f"  ❌ Error updating trailing stop: {e}")

def update_trailing_stop(current_price: float):
    """Update trailing stop loss."""
    global trailing_stop
    
    if not in_position or trailing_stop == 0:
        return
    
    # Move trailing stop up if price increased
    new_trailing_stop = current_price * (1 - TRAILING_STOP_PCT)
    if new_trailing_stop > trailing_stop:
        trailing_stop = new_trailing_stop
        print(f"  📈 Trailing stop updated: ${trailing_stop:.2f}")

def check_exit_conditions(current_price: float) -> str:
    """Check if we should exit the position."""
    global in_position, entry_price, stop_loss, take_profit, trailing_stop
    
    if not in_position:
        return "hold"
    
    # Check stop loss
    if current_price <= stop_loss:
        return "stop_loss"
    
    # Check take profit
    if current_price >= take_profit:
        return "take_profit"
    
    # Check trailing stop
    if trailing_stop > 0 and current_price <= trailing_stop:
        return "trailing_stop"
    
    return "hold"

# ─────────────────────────────────────────────
# TRADING FUNCTIONS
# ─────────────────────────────────────────────

def get_buy_size(dry_run: bool) -> float:
    """Calculate optimal buy size using USD or USDC balance."""
    if dry_run:
        return CONFIG["min_trade"] / 150.0  # Approximate stock price
    
    balances = get_balance()
    available_usd = float(balances.get("ZUSD", 0))
    available_usdc = float(balances.get("USDC", 0))
    
    # Use USD first, fallback to USDC if USD insufficient
    if available_usd >= CONFIG["min_trade"]:
        total_available = available_usd
        funding_source = "USD"
    elif available_usdc >= CONFIG["min_trade"]:
        total_available = available_usdc
        funding_source = "USDC"
    else:
        total_available = max(available_usd, available_usdc)
        funding_source = "USD" if available_usd >= available_usdc else "USDC"
    
    print(f"  💰 Available: ${available_usd:.2f} USD, ${available_usdc:.2f} USDC")
    print(f"  🏦 Using: ${total_available:.2f} {funding_source}")
    
    if total_available < CONFIG["min_trade"]:
        print(f"  ❌ Insufficient {funding_source}: need ${CONFIG['min_trade']:.2f}, have ${total_available:.2f}")
        return 0.0
    
    return CONFIG["min_trade"] / 150.0  # Will be calculated dynamically in real trading

def get_sell_size(dry_run: bool) -> float:
    """How many shares to SELL from existing holdings."""
    if dry_run:
        return 10.0  # Simulate selling 10 shares
    
    balances = get_balance()
    available = float(balances.get(ASSET, 0))
    
    if available <= 0:
        return 0.0
    
    # Sell 50% of holdings
    return available * 0.5

def execute_buy(signal_info: dict, dry_run: bool) -> bool:
    """Execute a buy order with proper risk management."""
    global in_position, entry_price, stop_loss, take_profit, trailing_stop, position_size, last_signal, active_trade_id
    
    try:
        current_price = signal_info["price"]
        
        # Calculate position size
        balances = get_balance()
        available_usd = float(balances.get("ZUSD", 0))
        available_usdc = float(balances.get("USDC", 0))
        
        # Use USD first, fallback to USDC if USD insufficient
        if available_usd >= CONFIG["min_trade"]:
            account_balance = available_usd
            funding_source = "USD"
            quote_currency = "ZUSD"
        elif available_usdc >= CONFIG["min_trade"]:
            account_balance = available_usdc
            funding_source = "USDC"
            quote_currency = "USDC"
        else:
            # Use whichever has more balance
            if available_usd >= available_usdc:
                account_balance = available_usd
                funding_source = "USD"
                quote_currency = "ZUSD"
            else:
                account_balance = available_usdc
                funding_source = "USDC"
                quote_currency = "USDC"
        
        print(f"  💰 Available: ${available_usd:.2f} USD, ${available_usdc:.2f} USDC")
        print(f"  🏦 Using: ${account_balance:.2f} {funding_source}")
        
        # Set stop loss based on ATR or stock-specific percentage
        atr = signal_info["analysis"].get("1h", {}).get("atr", 0)
        if atr > 0:
            stop_loss_price = current_price - (atr * 1.5)  # 1.5x ATR stop
        else:
            stop_loss_price = current_price * (1 - CONFIG["stop_loss"])
        
        position_shares = calculate_position_size(current_price, stop_loss_price, account_balance)
        
        if position_shares <= 0:
            print(f"  ❌ Invalid position size: {position_shares}")
            return False
        
        # Calculate take profit using stock-specific target
        take_profit_price = current_price * (1 + CONFIG["profit_target"])
        
        # Validate risk/reward
        risk = current_price - stop_loss_price
        reward = take_profit_price - current_price
        risk_reward_ratio = reward / risk if risk > 0 else 0
        
        if risk_reward_ratio < MIN_RISK_REWARD:
            print(f"  ❌ Poor risk/reward: {risk_reward_ratio:.2f} < {MIN_RISK_REWARD}")
            return False
        
        # Check if we have enough balance
        required_cost = position_shares * current_price
        if required_cost > account_balance:
            print(f"  ❌ Insufficient {funding_source}: need ${required_cost:.2f}, have ${account_balance:.2f}")
            return False
        
        if dry_run:
            print(f"  📊 [DRY] Buy Signal Analysis:")
            print(f"     Entry: ${current_price:.2f}")
            print(f"     Stop: ${stop_loss_price:.2f} ({CONFIG['stop_loss']*100:.1f}%)")
            print(f"     Target: ${take_profit_price:.2f} ({CONFIG['profit_target']*100:.1f}%)")
            print(f"     Shares: {position_shares:.2f}")
            print(f"     Cost: ${required_cost:.2f} ({funding_source})")
            print(f"     Risk/Reward: {risk_reward_ratio:.2f}")
            print(f"     Confidence: {signal_info['confidence']:.2f}")
            return True
        
        # Place order using the appropriate pair
        if funding_source == "USDC":
            order_pair = f"{SYMBOL}USDC"  # Use USDC pair if funding with USDC
        else:
            order_pair = PAIR  # Use default USD pair
        
        order_result, order_info = place_order(
            pair=order_pair,
            side="buy",
            order_type="market",
            volume=position_shares,
            cost=required_cost,
            validate=False,
            userref=1
        )
        
        if order_result:
            # Update position state
            in_position = True
            entry_price = current_price
            stop_loss = stop_loss_price
            take_profit = take_profit_price
            trailing_stop = 0  # Initialize trailing stop
            position_size = position_shares
            last_signal = "buy"
            
            print(f"  ✅ BUY EXECUTED: {position_shares:.2f} shares @ ${current_price:.2f}")
            print(f"  💰 Cost: ${required_cost:.2f} ({funding_source})")
            print(f"  🛡️ Stop Loss: ${stop_loss:.2f}")
            print(f"  🎯 Take Profit: ${take_profit:.2f}")
            print(f"  📊 Risk/Reward: {risk_reward_ratio:.2f}")
            
            # Place limit orders for stop-loss and take-profit
            if not dry_run:
                place_limit_orders(current_price, position_shares, stop_loss_price, take_profit_price)
            
            active_trade_id = LEARNER.record_entry(
                bot_name=BOT_NAME,
                symbol=SYMBOL,
                side="buy",
                entry_price=current_price,
                position_size=position_shares,
                confidence=float(signal_info.get("confidence", 0.0)),
                features=extract_learning_features(signal_info, current_price),
                config=CONFIG,
            )

            # Send notification
            msg = f"📈 *{SYMBOL} BUY EXECUTED*\n"
            msg += f"Shares: {position_shares:.2f} @ ${current_price:.2f}\n"
            msg += f"Cost: ${required_cost:.2f} ({funding_source})\n"
            msg += f"Stop Loss: ${stop_loss:.2f}\n"
            msg += f"Take Profit: ${take_profit:.2f}\n"
            msg += f"Risk/Reward: {risk_reward_ratio:.2f}\n"
            msg += f"Signal: {signal_info['reason']}"
            tg(msg)

            return True
        else:
            print(f"  ❌ Buy order failed: {order_info}")
            return False
            
    except Exception as e:
        print(f"  ❌ Buy execution error: {e}")
        return False

def execute_sell(current_price: float, reason: str, dry_run: bool, skip_order: bool = False) -> bool:
    """Execute a sell order.

    skip_order=True skips placing a market sell (used when the exchange already
    closed the position via a limit/stop order) but still reconciles local state
    and records the outcome in the learning DB.
    """
    global in_position, entry_price, stop_loss, take_profit, trailing_stop, position_size, last_signal, trade_history, active_trade_id

    try:
        if not in_position:
            return False

        if dry_run:
            pnl_pct = (current_price - entry_price) / entry_price * 100
            print(f"  📊 [DRY] Sell Signal:")
            print(f"     Exit: ${current_price:.2f}")
            print(f"     Entry: ${entry_price:.2f}")
            print(f"     P&L: {pnl_pct:+.2f}%")
            print(f"     Reason: {reason}")
            return True

        if skip_order:
            order_result = True
            order_info = "exchange-managed exit"
        else:
            # Place sell order
            order_result, order_info = place_order(
                pair=PAIR,
                side="sell",
                order_type="market",
                volume=position_size,
                cost=position_size * current_price,
                validate=False,
                userref=1
            )
        
        if order_result:
            # Calculate P&L
            pnl_amount = (current_price - entry_price) * position_size
            pnl_pct = (current_price - entry_price) / entry_price * 100
            
            # Record trade
            trade = {
                "entry_price": entry_price,
                "exit_price": current_price,
                "shares": position_size,
                "pnl_amount": pnl_amount,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "entry_time": datetime.now(),
                "duration": "N/A"
            }
            trade_history.append(trade)
            
            print(f"  ✅ SELL EXECUTED: {position_size:.2f} shares @ ${current_price:.2f}")
            print(f"  💰 P&L: ${pnl_amount:+.2f} ({pnl_pct:+.2f}%)")
            print(f"  📝 Reason: {reason}")
            
            # Cancel all limit orders
            if not dry_run:
                cancel_all_limit_orders()
            
            if active_trade_id:
                LEARNER.record_exit(
                    trade_id=active_trade_id,
                    exit_price=current_price,
                    pnl_amount=pnl_amount,
                    pnl_pct=pnl_pct / 100,
                    exit_reason=reason,
                )
                active_trade_id = None

            # Send notification
            msg = f"📈 *{SYMBOL} SELL EXECUTED*\n"
            msg += f"Shares: {position_size:.2f} @ ${current_price:.2f}\n"
            msg += f"P&L: ${pnl_amount:+.2f} ({pnl_pct:+.2f}%)\n"
            msg += f"Reason: {reason}"
            tg(msg)

            # Reset position state
            in_position = False
            entry_price = 0
            stop_loss = 0
            take_profit = 0
            trailing_stop = 0
            position_size = 0
            last_signal = "sell"
            
            return True
        else:
            print(f"  ❌ Sell order failed: {order_info}")
            return False
            
    except Exception as e:
        print(f"  ❌ Sell execution error: {e}")
        return False

# ─────────────────────────────────────────────
# MAIN TRADING LOOP
# ─────────────────────────────────────────────

def get_pair_format(symbol: str) -> str:
    """Get correct Kraken pair format for crypto vs stocks/ETFs."""
    # Crypto pairs
    if symbol == "BTC":
        return "XBTUSD"
    elif symbol == "ETH":
        return "ETHUSD"
    elif symbol in ["SOL", "DOT", "ADA", "LINK", "UNI", "WAR"]:
        return f"{symbol}USD"
    
    # Stock/ETF pairs (3-4 letter tickers)
    elif len(symbol) <= 4 and symbol.replace(".", "").isalpha():
        if symbol.endswith(".EQ"):
            return f"{symbol}USD"  # Already has .EQ suffix
        else:
            return f"{symbol}.EQUSD"  # Add .EQ suffix for stocks/ETFs
    
    # Default to crypto format
    else:
        return f"{symbol}USD"

def is_stock_or_etf(symbol: str) -> bool:
    """Check if symbol is a stock/ETF (not crypto)."""
    # Remove .EQ suffix if present
    clean_symbol = symbol.replace(".EQ", "")
    
    # Stocks/ETFs are typically 1-5 letters, no crypto prefixes
    crypto_prefixes = ["XBT", "XETH", "XXBT"]
    stock_like = (
        clean_symbol.isalpha() and 
        len(clean_symbol) <= 5 and
        not any(clean_symbol.startswith(prefix) for prefix in crypto_prefixes) and
        clean_symbol not in ["BTC", "ETH", "SOL", "DOT", "ADA", "LINK", "UNI", "WAR"]
    )
    
    return stock_like

def run(symbol: str, dry_run: bool = False, manual_entry: bool = False):
    """Main trading loop."""
    global SYMBOL, PAIR, ASSET, CONFIG, active_trade_id
    
    # Set global variables for this symbol
    SYMBOL = symbol.upper()
    
    # Use the new pair format logic
    PAIR = get_pair_format(SYMBOL)
    
    # Set asset name for balance checking
    if SYMBOL == "BTC":
        ASSET = "XXBT"
    elif SYMBOL == "ETH":
        ASSET = "XETH"
    else:
        ASSET = SYMBOL
    
    CONFIG = load_learning_config(SYMBOL, get_stock_config(SYMBOL))

    # Recover learning state after restart/crash
    try:
        balances = get_balance()
        asset_balance = float(balances.get(ASSET, 0))
        if asset_balance > 0:
            recovered_id = LEARNER.recover_open_trade(BOT_NAME, SYMBOL)
            if recovered_id:
                active_trade_id = recovered_id
                print(f"  Recovered open trade id={recovered_id} for {SYMBOL}")
            else:
                print(f"  Position detected but no open trade row found — entry was not logged")
        else:
            orphaned = LEARNER.close_orphaned_trades(BOT_NAME, SYMBOL)
            if orphaned:
                print(f"  Closed {orphaned} orphaned trade row(s) — no live position at startup")
    except Exception as e:
        print(f"  Warning: could not check learning state at startup: {e}")

    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    entry_mode = "🖱️ MANUAL" if manual_entry else "🤖 AUTO"
    asset_type = "📈 Stock/ETF" if is_stock_or_etf(SYMBOL) else "🪙 Crypto"
    
    print("=" * 60)
    print(f"  {asset_type} {SYMBOL} Swing Trading Bot {mode} {entry_mode}")
    print(f"  Pair: {PAIR}")
    print(f"  Risk per trade: {RISK_PER_TRADE*100:.1f}%")
    print(f"  Min risk/reward: {MIN_RISK_REWARD}:1")
    print(f"  Profit target: {CONFIG['profit_target']*100:.1f}%")
    print(f"  Stop loss: {CONFIG['stop_loss']*100:.1f}%")
    print(f"  RSI thresholds: {CONFIG['rsi_oversold']}/{CONFIG['rsi_overbought']}")
    if manual_entry:
        print(f"  🎮 Manual entry mode - Press ENTER to trade")
    print("=" * 60)
    
    tg(f"{asset_type} *{SYMBOL} Bot started* ({mode} {entry_mode})")
    
    cycle = 0
    
    try:
        while True:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] Cycle {cycle}")
            
            # Get current price
            ticker = get_ticker(PAIR)
            if not ticker:
                print("  ⚠️ No ticker data")
                time.sleep(30)
                continue
            
            current_price = float(ticker.get("c", [0])[0])
            print(f"  💰 Current Price: ${current_price:.2f}")
            
            # Check exit conditions if in position
            if in_position:
                # Check if SL fired on Kraken
                if check_order_status():
                    time.sleep(60)  # Wait after exit
                    continue

                # Check TP/SL/trailing in bot loop (TP has no standing order)
                exit_signal = check_exit_conditions(current_price)
                if exit_signal != "hold":
                    print(f"  🎯 Exit signal: {exit_signal} @ ${current_price:.2f}")
                    cancel_all_limit_orders()  # Cancel SL before market sell
                    execute_sell(current_price, exit_signal, dry_run)
                    time.sleep(60)
                    continue

                # Update trailing stop order if price increased
                update_trailing_stop_order(current_price)

                print(f"  📊 Position: {position_size:.2f} shares @ ${entry_price:.2f}")
                print(f"  🛡️ Stop: ${stop_loss:.2f} | 🎯 Target: ${take_profit:.2f}")
                if trailing_stop > 0:
                    print(f"  📈 Trailing: ${trailing_stop:.2f}")

                # Show active orders
                if stop_loss_order_id:
                    print(f"  📋 Active: 🛡️ SL {stop_loss_order_id}")
            else:
                # Manual entry prompt
                if manual_entry:
                    print(f"  🎮 Manual Entry: Press ENTER to buy {SYMBOL} at ${current_price:.2f}, or 'q' to quit")
                    try:
                        user_input = input("  > ").strip().lower()
                        if user_input == 'q':
                            print("  👋 Exiting manual mode...")
                            break
                        elif user_input == '' or user_input.lower() in ['buy', 'b', 'enter']:
                            # Create manual signal
                            manual_signal = {
                                "signal": "buy",
                                "confidence": 1.0,
                                "reason": "Manual entry",
                                "price": current_price,
                                "analysis": {}
                            }
                            
                            print(f"  🎯 Executing MANUAL BUY at ${current_price:.2f}")
                            if execute_buy(manual_signal, dry_run):
                                print("  ✅ Manual buy executed")
                            else:
                                print("  ❌ Manual buy failed")
                        else:
                            print(f"  ⏭️ Skipping - input: '{user_input}'")
                    except KeyboardInterrupt:
                        print("\n  👋 Interrupted by user")
                        break
                    except EOFError:
                        print("\n  👋 End of input")
                        break
            
            # Auto trading signals (only if not in manual mode)
            if not manual_entry and not in_position:
                # Generate trading signal
                analysis = multi_timeframe_analysis(SYMBOL)
                signal = generate_signal(analysis, CONFIG)
                
                print(f"  🎯 Signal: {signal['signal'].upper()} (confidence: {signal['confidence']:.2f})")
                print(f"  📝 Reason: {signal['reason']}")
                
                # Execute trades based on signal
                if signal['signal'] == 'buy' and signal['confidence'] >= CONFIG['min_confidence']:
                    if execute_buy(signal, dry_run):
                        print("  ✅ Buy order placed")
                    else:
                        print("  ❌ Buy order failed")
                elif signal['signal'] == 'sell':
                    print("  ⚠️ Sell signal but no position")
            elif not manual_entry:
                # Show analysis even in manual mode for reference
                analysis = multi_timeframe_analysis(SYMBOL)
                signal = generate_signal(analysis, CONFIG)
                
                print(f"  🎯 Auto Signal: {signal['signal'].upper()} (confidence: {signal['confidence']:.2f})")
                print(f"  📝 Reason: {signal['reason']}")
            
            # Show performance summary
            if trade_history and cycle % 10 == 0:
                winning_trades = [t for t in trade_history if t['pnl_amount'] > 0]
                win_rate = len(winning_trades) / len(trade_history) * 100
                total_pnl = sum(t['pnl_amount'] for t in trade_history)
                
                print(f"  📊 Performance: {win_rate:.1f}% win rate, ${total_pnl:+.2f} total P&L")
            
            if not manual_entry:
                time.sleep(30)  # Wait 30 seconds between checks (auto mode)
            else:
                time.sleep(5)   # Shorter wait in manual mode for responsiveness
            
    except KeyboardInterrupt:
        print(f"\n👋 Bot stopped by user")
        tg(f"👋 *{SYMBOL} Bot stopped*")
    except Exception as e:
        print(f"\n❌ Bot error: {e}")
        tg(f"❌ *{SYMBOL} Bot error*: {e}")

# ─────────────────────────────────────────────
# SIGNAL HANDLING
# ─────────────────────────────────────────────

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global in_position
    if in_position:
        print(f"\n⚠️ WARNING: Still in position!")
    print(f"\n👋 Shutting down {SYMBOL} bot...")
    sys.exit(0)

# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    parser = argparse.ArgumentParser(description="Dynamic Stock Swing Trading Bot")
    parser.add_argument("symbol", help="Stock symbol (e.g., AAPL, TSLA, MSFT)")
    parser.add_argument("--dry", action="store_true", help="Dry run — no real orders placed")
    parser.add_argument("--manual", action="store_true", help="Manual entry mode — press ENTER to buy")
    args = parser.parse_args()
    
    run(symbol=args.symbol, dry_run=args.dry, manual_entry=args.manual)
