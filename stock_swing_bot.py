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
from datetime import datetime
from kraken_connection import (
    get_ticker, get_ohlc, get_balance, place_order, cancel_order,
    get_order_status, get_fill, get_pair_base_balance, below_minimum,
)
import strategies
from learning_engine import LearningEngine
from position_store import PositionStore
from market_sentiment import should_buy as check_sentiment, format_sentiment

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

# 🎯 Trading Parameters
MAX_POSITION_SIZE = 0.1  # Max 10% of portfolio
RISK_PER_TRADE = 0.02    # 2% max risk
MIN_RISK_REWARD = 2.0     # 1:2 minimum risk/reward


# 📱 Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# 📈 Trading state (set in run())
position = None          # open position dict, or None when flat (see POSITION MANAGEMENT)
trade_history = []
SYMBOL = ""
PAIR = ""
CONFIG = {}
BOT_NAME = "stock_swing_bot"
LEARNER = LearningEngine()
STORE = None             # PositionStore for state/stock_swing_bot_<SYMBOL>.json
USERREF = 1              # tags this bot's orders on Kraken
SL_RETRY_SECS = 300      # retry a missing stop-loss at most this often
active_trade_id = None

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
# TECHNICAL ANALYSIS (maths lives in strategies.py, shared with backtester.py)
# ─────────────────────────────────────────────

calculate_rsi = strategies.rsi
calculate_macd = strategies.macd
generate_signal = strategies.swing_signal


def multi_timeframe_analysis(symbol: str) -> dict:
    """Fetch candles for each timeframe and compute indicators."""
    analysis = {}
    for tf_name, tf_minutes in strategies.SWING_TIMEFRAMES.items():
        try:
            result = strategies.analyze_timeframe(get_ohlc(PAIR, interval=tf_minutes))
            if result:
                analysis[tf_name] = result
        except Exception as e:
            print(f"  ⚠️ Error analyzing {tf_name}: {e}")
    return analysis


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# POSITION MANAGEMENT
# ─────────────────────────────────────────────
#
# `position` is None when flat, otherwise a dict saved to
# state/stock_swing_bot_<SYMBOL>.json on every change:
#   entry_price, volume, fees, stop_loss, take_profit, sl_txid, trade_id, entry_time
# A stop-loss order on Kraken protects it even while the bot is down.

def calculate_position_size(entry_price: float, stop_loss_price: float, account_balance: float) -> float:
    """Calculate position size from risk per trade, capped by MAX_POSITION_SIZE."""
    risk_amount = account_balance * RISK_PER_TRADE
    risk_per_share = abs(entry_price - stop_loss_price)

    if risk_per_share == 0:
        return 0

    max_shares = risk_amount / risk_per_share
    max_shares_by_value = account_balance * MAX_POSITION_SIZE / entry_price
    shares = min(max_shares, max_shares_by_value)

    # Ensure minimum trade size
    if shares * entry_price < CONFIG["min_trade"]:
        shares = CONFIG["min_trade"] / entry_price

    return shares


def _persist() -> None:
    if STORE is None:
        return
    if position:
        STORE.set(PAIR, position)
    else:
        STORE.remove(PAIR)


def protect_position(dry_run: bool) -> None:
    """Place the stop-loss on Kraken if the position has none (rate-limited)."""
    if dry_run or not position or position.get("sl_txid") or position.get("dust"):
        return
    now = time.time()
    if now < position.get("sl_retry_at", 0):
        return
    position["sl_retry_at"] = now + SL_RETRY_SECS

    reason = below_minimum(PAIR, position["volume"], position["stop_loss"])
    if reason:
        print(f"  ⚠️ No stop-loss possible: {reason}")
        position["dust"] = True
        _persist()
        return

    ok, info = place_order(PAIR, "sell", "stop-loss", volume=position["volume"],
                           price=position["stop_loss"], userref=USERREF)
    if ok:
        position["sl_txid"] = (info.get("txid") or [None])[0]
        print(f"  🛡️ Stop-loss on Kraken @ ${position['stop_loss']:.6g} [{position['sl_txid']}]")
    else:
        print(f"  ❌ Stop-loss order failed (retry in {SL_RETRY_SECS // 60} min): {info}")
    _persist()


def check_sl_filled() -> bool:
    """True only if our stop-loss order FILLED on Kraken. A cancelled one is cleared for re-placement."""
    if not position or not position.get("sl_txid"):
        return False
    status = get_order_status(position["sl_txid"])
    if status == "closed":
        print(f"  🎯 Stop-loss filled on Kraken [{position['sl_txid']}]")
        return True
    if status in ("canceled", "expired"):
        print(f"  ⚠️ Stop-loss {position['sl_txid']} was {status} — will re-place")
        position["sl_txid"] = None
        position["sl_retry_at"] = 0
        _persist()
    return False


def release_stop_loss() -> str:
    """
    Cancel our stop-loss so the coins can be market-sold.
    Returns "released", "filled" (it already executed) or "error" (retry later).
    """
    sl = position.get("sl_txid") if position else None
    if not sl:
        return "released"
    status = get_order_status(sl)
    if status == "closed":
        return "filled"
    if status in ("canceled", "expired") or cancel_order(sl):
        position["sl_txid"] = None
        return "released"
    return "error"


def check_exit_conditions(current_price: float) -> str:
    """Return "stop_loss", "take_profit" or "hold"."""
    if not position:
        return "hold"
    if current_price <= position["stop_loss"]:
        return "stop_loss"
    if current_price >= position["take_profit"]:
        return "take_profit"
    return "hold"


# ─────────────────────────────────────────────
# TRADING FUNCTIONS
# ─────────────────────────────────────────────

def _stop_and_target(entry: float, atr: float) -> tuple[float, float]:
    return strategies.swing_stop_and_target(entry, atr, CONFIG)


def execute_buy(signal_info: dict, dry_run: bool) -> bool:
    """Execute a buy with risk-based sizing, then protect it with a stop-loss."""
    global position, active_trade_id

    current_price = signal_info["price"]
    available_usd = float(get_balance().get("ZUSD", 0))
    print(f"  💰 Available: ${available_usd:.2f} USD")

    atr = signal_info.get("analysis", {}).get("1h", {}).get("atr", 0)
    stop_loss_price, take_profit_price = _stop_and_target(current_price, atr)

    position_shares = calculate_position_size(current_price, stop_loss_price, available_usd)
    size_mult = signal_info.get("size_multiplier", 1.0)
    if size_mult < 1.0:
        position_shares = round(position_shares * size_mult, 8)
        print(f"  🌍 Sentiment scaling: {size_mult}x → {position_shares}")
    if position_shares <= 0:
        print(f"  ❌ Invalid position size: {position_shares}")
        return False

    risk = current_price - stop_loss_price
    reward = take_profit_price - current_price
    risk_reward_ratio = reward / risk if risk > 0 else 0
    if risk_reward_ratio < MIN_RISK_REWARD:
        print(f"  ❌ Poor risk/reward: {risk_reward_ratio:.2f} < {MIN_RISK_REWARD}")
        return False

    required_cost = position_shares * current_price
    if required_cost > available_usd:
        print(f"  ❌ Insufficient USD: need ${required_cost:.2f}, have ${available_usd:.2f}")
        return False

    if dry_run:
        print(f"  📊 [DRY] Buy: {position_shares:.8g} @ ${current_price:.6g} (${required_cost:.2f}) | "
              f"stop ${stop_loss_price:.6g} | target ${take_profit_price:.6g} | R:R {risk_reward_ratio:.2f} | "
              f"confidence {signal_info['confidence']:.2f}")
        position = {"entry_price": current_price, "volume": position_shares, "fees": 0.0,
                    "stop_loss": stop_loss_price, "take_profit": take_profit_price, "sl_txid": None}
        return True

    ok, info = place_order(pair=PAIR, side="buy", order_type="market",
                           volume=position_shares, userref=USERREF)
    if not ok:
        print(f"  ❌ Buy order failed: {info}")
        return False

    # Use the real fill (price, volume, fee), not the ticker we saw before ordering.
    txid = (info.get("txid") or [None])[0]
    fill = get_fill(txid) if txid else None
    entry, volume, fee = current_price, position_shares, 0.0
    if fill and fill["vol_exec"] > 0:
        entry, volume, fee = fill["avg_price"], fill["vol_exec"], fill["fee"]
    else:
        print(f"  ⚠️ Could not read fill for {txid}; using requested size/price")
    stop_loss_price, take_profit_price = _stop_and_target(entry, atr)

    active_trade_id = LEARNER.record_entry(
        bot_name=BOT_NAME, symbol=SYMBOL, side="buy", entry_price=entry,
        position_size=volume, confidence=float(signal_info.get("confidence", 0.0)),
        features=extract_learning_features(signal_info, entry), config=CONFIG,
    )
    position = {
        "entry_price": entry, "volume": volume, "fees": fee,
        "stop_loss": stop_loss_price, "take_profit": take_profit_price,
        "sl_txid": None, "trade_id": active_trade_id, "order_txid": txid,
        "entry_time": datetime.now().isoformat(timespec="seconds"),
    }
    _persist()
    protect_position(dry_run)

    print(f"  ✅ BUY EXECUTED: {volume:.8g} @ ${entry:.6g} (${entry*volume:.2f}, fee ${fee:.4f})")
    print(f"  🛡️ Stop ${stop_loss_price:.6g} | 🎯 Target ${take_profit_price:.6g} | R:R {risk_reward_ratio:.2f}")
    tg(f"📈 *{SYMBOL} BUY* {volume:.8g} @ ${entry:.6g} (${entry*volume:.2f})\n"
       f"Stop ${stop_loss_price:.6g} | Target ${take_profit_price:.6g}\nSignal: {signal_info['reason']}")
    return True


def execute_sell(current_price: float, reason: str, dry_run: bool, exchange_filled: bool = False) -> bool:
    """
    Close the position. exchange_filled=True when Kraken's stop-loss already sold it.
    Records net P&L (after fees). Returns False if the position is still open.
    """
    global position, active_trade_id
    if not position:
        return False

    entry, volume = position["entry_price"], position["volume"]
    fees = position.get("fees", 0.0)
    exit_price = current_price

    if dry_run:
        pnl_pct = (current_price - entry) / entry * 100
        print(f"  📊 [DRY] Sell ({reason}) @ ${current_price:.6g} | entry ${entry:.6g} | P&L {pnl_pct:+.2f}%")
        trade_history.append({"pnl_amount": (current_price - entry) * volume, "pnl_pct": pnl_pct})
        position = None
        return True

    if not exchange_filled:
        released = release_stop_loss()
        if released == "filled":
            exchange_filled = True
        elif released == "error":
            print(f"  ⚠️ Could not cancel stop-loss; will retry")
            return False

    if exchange_filled:
        fill = get_fill(position["sl_txid"]) if position.get("sl_txid") else None
        if fill and fill["vol_exec"] > 0:
            exit_price, volume = fill["avg_price"], fill["vol_exec"]
            fees += fill["fee"]
    else:
        held = get_pair_base_balance(PAIR)
        sell_volume = min(volume, held) if held > 0 else volume
        ok, info = place_order(pair=PAIR, side="sell", order_type="market",
                               volume=sell_volume, userref=USERREF)
        if not ok and info.get("below_minimum"):
            print(f"  🧹 {sell_volume} {SYMBOL} is below Kraken's minimum — dropping position as dust")
            reason = "dust"
        elif not ok:
            print(f"  ❌ Sell order failed: {info}")
            position["sl_retry_at"] = 0
            protect_position(dry_run)  # put protection back before retrying
            return False
        else:
            txid = (info.get("txid") or [None])[0]
            fill = get_fill(txid) if txid else None
            if fill and fill["vol_exec"] > 0:
                exit_price, volume = fill["avg_price"], fill["vol_exec"]
                fees += fill["fee"]

    pnl_amount = (exit_price - entry) * volume - fees
    pnl_pct = pnl_amount / (entry * volume) * 100 if entry and volume else 0.0
    trade_history.append({"pnl_amount": pnl_amount, "pnl_pct": pnl_pct})

    trade_id = position.get("trade_id") or active_trade_id
    if trade_id:
        LEARNER.record_exit(trade_id=trade_id, exit_price=exit_price, pnl_amount=pnl_amount,
                            pnl_pct=pnl_pct / 100, exit_reason=reason)
    active_trade_id = None

    print(f"  ✅ SELL ({reason}): {volume:.8g} @ ${exit_price:.6g} | net P&L ${pnl_amount:+.2f} ({pnl_pct:+.2f}%)")
    tg(f"📈 *{SYMBOL} SELL* ({reason}) @ ${exit_price:.6g}\nNet P&L: ${pnl_amount:+.2f} ({pnl_pct:+.2f}%)")
    position = None
    _persist()
    return True


def restore_position(dry_run: bool) -> None:
    """
    After a restart, reload the saved position and reconcile it with Kraken.
    Holdings this bot did not buy are left alone (bot_monitor_cron protects them).
    """
    global position, active_trade_id
    if dry_run:
        return
    stored = STORE.get(PAIR)
    held = get_pair_base_balance(PAIR)

    if not stored:
        orphaned = LEARNER.close_orphaned_trades(BOT_NAME, SYMBOL)
        if orphaned:
            print(f"  Closed {orphaned} orphaned trade row(s) — no saved position")
        if held > 0:
            print(f"  ℹ️ Holding {held:.8g} {SYMBOL} that this bot did not open — not managing it")
        return

    position = stored
    active_trade_id = stored.get("trade_id")
    status = get_order_status(stored["sl_txid"]) if stored.get("sl_txid") else None

    if status == "closed":
        print(f"  ♻️ Stop-loss filled while the bot was down")
        execute_sell(stored["stop_loss"], "stop_loss", dry_run, exchange_filled=True)
        return
    if status in ("canceled", "expired"):
        position["sl_txid"] = None

    if held < stored["volume"] * 0.5:
        print(f"  ♻️ Saved position no longer held ({held:.8g} {SYMBOL}) — recording as closed externally")
        if position.get("sl_txid") and status in ("open", "pending"):
            cancel_order(position["sl_txid"])
        ticker = get_ticker(PAIR)
        price = float(ticker.get("c", [stored["entry_price"]])[0]) if ticker else stored["entry_price"]
        position["sl_txid"] = None
        execute_sell(price, "closed_externally", dry_run, exchange_filled=True)
        return

    position["volume"] = min(stored["volume"], held)
    position["sl_retry_at"] = 0
    _persist()
    print(f"  ♻️ Restored position: {position['volume']:.8g} {SYMBOL} @ ${position['entry_price']:.6g} "
          f"| stop ${position['stop_loss']:.6g} | target ${position['take_profit']:.6g}")
    protect_position(dry_run)

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
    """Main trading loop. One process trades one symbol."""
    global SYMBOL, PAIR, CONFIG, STORE

    SYMBOL = symbol.upper()
    PAIR = get_pair_format(SYMBOL)
    STORE = PositionStore(f"{BOT_NAME}_{SYMBOL}")
    CONFIG = load_learning_config(SYMBOL, get_stock_config(SYMBOL))

    try:
        restore_position(dry_run)
    except Exception as e:
        print(f"  ⚠️ Could not restore saved position at startup: {e}")

    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    entry_mode = "🖱️ MANUAL" if manual_entry else "🤖 AUTO"
    print("=" * 60)
    print(f"  🪙 {SYMBOL} Swing Trading Bot {mode} {entry_mode}")
    print(f"  Pair: {PAIR}")
    print(f"  Risk per trade: {RISK_PER_TRADE*100:.1f}% | Min risk/reward: {MIN_RISK_REWARD}:1")
    print(f"  Profit target: {CONFIG['profit_target']*100:.1f}% | Stop loss: {CONFIG['stop_loss']*100:.1f}% (or 1.5x ATR)")
    print(f"  RSI thresholds: {CONFIG['rsi_oversold']}/{CONFIG['rsi_overbought']}")
    if manual_entry:
        print(f"  🎮 Manual entry mode - Press ENTER to trade")
    print("=" * 60)
    tg(f"🪙 *{SYMBOL} Bot started* ({mode} {entry_mode})")

    cycle = 0
    while True:
        try:
            cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] Cycle {cycle}")

            ticker = get_ticker(PAIR)
            if not ticker:
                print("  ⚠️ No ticker data")
                time.sleep(30)
                continue
            current_price = float(ticker.get("c", [0])[0])
            print(f"  💰 Current Price: ${current_price:.6g}")

            if position:
                # Stop-loss executed on Kraken?
                if not dry_run and check_sl_filled():
                    execute_sell(current_price, "stop_loss", dry_run, exchange_filled=True)
                    time.sleep(60)
                    continue

                protect_position(dry_run)

                exit_signal = check_exit_conditions(current_price)
                if exit_signal != "hold":
                    print(f"  🎯 Exit signal: {exit_signal} @ ${current_price:.6g}")
                    execute_sell(current_price, exit_signal, dry_run)
                    time.sleep(60)
                    continue

                pnl_pct = (current_price - position["entry_price"]) / position["entry_price"] * 100
                print(f"  📊 Position: {position['volume']:.8g} @ ${position['entry_price']:.6g} ({pnl_pct:+.2f}%)")
                print(f"  🛡️ Stop: ${position['stop_loss']:.6g} | 🎯 Target: ${position['take_profit']:.6g}"
                      + (f" | SL order {position['sl_txid']}" if position.get("sl_txid") else ""))

            elif manual_entry:
                print(f"  🎮 Manual Entry: Press ENTER to buy {SYMBOL} at ${current_price:.6g}, or 'q' to quit")
                try:
                    user_input = input("  > ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    print("\n  👋 Exiting manual mode...")
                    break
                if user_input == "q":
                    break
                if user_input in ("", "buy", "b", "enter"):
                    analysis = multi_timeframe_analysis(SYMBOL)
                    manual_signal = {"signal": "buy", "confidence": 1.0, "reason": "Manual entry",
                                     "price": current_price, "analysis": analysis}
                    print("  ✅ Manual buy executed" if execute_buy(manual_signal, dry_run) else "  ❌ Manual buy failed")

            else:
                analysis = multi_timeframe_analysis(SYMBOL)
                signal_info = generate_signal(analysis, CONFIG)
                print(f"  🎯 Signal: {signal_info['signal'].upper()} (confidence: {signal_info['confidence']:.2f})")
                print(f"  📝 Reason: {signal_info['reason']}")

                if signal_info["signal"] == "buy" and signal_info["confidence"] >= CONFIG["min_confidence"]:
                    sentiment = check_sentiment()
                    print(f"  🌍 {format_sentiment()}")
                    if not sentiment["allow"]:
                        print(f"  🚫 {sentiment['reason']}")
                    else:
                        signal_info["size_multiplier"] = sentiment["size_multiplier"]
                        execute_buy(signal_info, dry_run)

            if trade_history and cycle % 10 == 0:
                wins = [t for t in trade_history if t["pnl_amount"] > 0]
                total_pnl = sum(t["pnl_amount"] for t in trade_history)
                print(f"  📊 Session: {len(wins)}/{len(trade_history)} wins, ${total_pnl:+.2f} net P&L")

            time.sleep(5 if manual_entry else 30)

        except KeyboardInterrupt:
            print(f"\n👋 Bot stopped by user")
            tg(f"👋 *{SYMBOL} Bot stopped*")
            break
        except Exception as e:
            # One bad cycle (API hiccup, bad data) must not kill the bot.
            print(f"  ❌ Cycle error: {e}")
            time.sleep(30)


# SIGNAL HANDLING
# ─────────────────────────────────────────────

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    if position:
        print(f"\n⚠️ Still in position — the stop-loss on Kraken stays active and the position is saved for restart.")
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
