#!/usr/bin/env python3
"""
Dynamic HFT Bot — Kraken
Finds the TOP_N most volatile liquid USD pairs across Kraken every 15 minutes
and trades each one in its own thread.

Strategy per pair (see SETTINGS for current values):
  - Buy on RSI < RSI_OVERSOLD or a DIP_MIN–DIP_MAX dip from the 30-min high
  - Take profit at +PROFIT_PCT, stop at -STOP_PCT (standalone stop-loss on Kraken)
  - MAX_USD_PER_PAIR per trade, MAX_TOTAL_USD across all pairs

Open positions are saved to state/dynamic_hft_bot.json and restored on
restart. A pair that leaves the top list keeps its thread until its position
is closed.

Usage:
    python3 dynamic_hft_bot.py              # live
    python3 dynamic_hft_bot.py --dry        # dry run with $50 fake balance
    python3 dynamic_hft_bot.py --dry --sim-balance 100  # custom fake balance

Requires kraken_connection.py in same folder.
.env needs: KRAKEN_API_KEY, KRAKEN_API_SECRET

NOTE: To debug pair discovery, set DEBUG_PAIRS = True below.
To rediscover ticker fields: print(list(tickers["XBTUSD"].keys()))
Ticker fields: a=ask, b=bid, c=last, v=volume, p=vwap, h=high, l=low, o=open
"""

import os
import time
import argparse
import threading
from datetime import datetime
from kraken_connection import (
    get_balance, get_ticker, get_ohlc, place_order, calculate_order_size,
    get_pair_base_balance, get_fill, get_order_status, cancel_order,
)
from position_guardian import place_exit_orders, check_exit_orders, cancel_remaining_exit
from position_store import PositionStore
from learning_engine import LearningEngine
from market_sentiment import should_buy as check_sentiment
import requests as req

BOT_NAME = "dynamic_hft_bot"
LEARNER  = LearningEngine()
STORE    = PositionStore(BOT_NAME)
USERREF  = 8                    # tags this bot's orders on Kraken

BASE_URL = "https://api.kraken.com"

# ─────────────────────────────────────────────
# SETTINGS — FIXED FOR BETTER WIN RATE
# ─────────────────────────────────────────────
TOP_N            = 10           # number of pairs to trade at once
RESCAN_SECS      = 900          # rescan for new top pairs every 15 min
CHECK_SECS       = 30           # how often each pair thread checks price
PROFIT_PCT       = 0.10         # 10% profit target — backtested optimal
STOP_PCT         = 0.08         # 8% stop loss — wider SL avoids noise stopouts
RSI_OVERSOLD     = 40           # entry RSI threshold (buy when RSI is below this)
RSI_OVERBOUGHT   = 70           # never enter when RSI is above this (too pumped)
DIP_MIN          = 0.02         # enter on 2%+ dip
DIP_MAX          = 0.15         # skip if drop > 15% (crash territory)
RSI_PERIOD       = 14
MIN_CONFIDENCE   = 0.30         # minimum confidence score to enter a trade
MAX_USD_PER_PAIR = 15.0         # max $ to risk per pair (must exceed Kraken ~$10 min)
MAX_TOTAL_USD    = 30.0         # hard cap across all pairs
MIN_VOLUME_USD   = 1_000_000   # skip pairs with < $1M 24h volume (bull/neutral)
MIN_VOLUME_BEAR  = 10_000_000  # $10M volume filter in bear market (cuts illiquid coins)
MIN_VOLATILITY   = 0.05         # skip pairs with < 5% 24h swing
RESERVE_USD      = 5.0          # always keep $5 in reserve
SL_RETRY_SECS    = 300          # retry a missing stop-loss at most this often
DRY_START_BAL    = 50.0         # default fake starting balance for dry run
DEBUG_PAIRS      = False        # set True to print all pair scores during scan
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT    = os.getenv("TELEGRAM_CHAT_ID", "")


# ─────────────────────────────────────────────
# SHARED STATE
# ─────────────────────────────────────────────
positions        = {}             # { pair: position dict } — live view of open positions
positions_lock   = threading.Lock()
active_threads   = {}

# Dry run virtual balance — protected by its own lock
dry_balance     = DRY_START_BAL
dry_balance_lock = threading.Lock()
dry_start_bal   = DRY_START_BAL

# Trade log for dry run summary
dry_trades      = []            # list of { pair, side, price, volume, pnl_usd }
dry_trades_lock = threading.Lock()

# Market regime — updated every rescan cycle, read by all pair threads
market_regime      = "neutral"   # "bull", "neutral", or "bear"
market_regime_lock = threading.Lock()


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def tg(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=8
        )
    except:
        pass


# ─────────────────────────────────────────────
# DRY RUN BALANCE HELPERS
# ─────────────────────────────────────────────
def dry_buy(pair: str, volume: float, price: float) -> bool:
    """Deduct cost from virtual balance. Returns False if insufficient funds."""
    global dry_balance
    cost = volume * price
    with dry_balance_lock:
        if dry_balance - cost < RESERVE_USD:
            return False
        dry_balance -= cost
    with dry_trades_lock:
        dry_trades.append({
            "pair": pair, "side": "buy",
            "price": price, "volume": volume, "pnl_usd": -cost
        })
    return True


def dry_sell(pair: str, volume: float, price: float, entry_price: float):
    """Add proceeds to virtual balance and record P&L."""
    global dry_balance
    proceeds = volume * price
    cost     = volume * entry_price
    pnl      = proceeds - cost
    with dry_balance_lock:
        dry_balance += proceeds
    with dry_trades_lock:
        dry_trades.append({
            "pair": pair, "side": "sell",
            "price": price, "volume": volume, "pnl_usd": pnl
        })
    return pnl


def dry_available_usd() -> float:
    """How much virtual USD is available to trade."""
    with dry_balance_lock:
        return max(0, dry_balance - RESERVE_USD)


def print_dry_summary():
    """Print full dry run P&L summary."""
    with dry_balance_lock:
        bal = dry_balance
    with dry_trades_lock:
        trades = list(dry_trades)

    sells   = [t for t in trades if t["side"] == "sell"]
    wins    = [t for t in sells if t["pnl_usd"] > 0]
    losses  = [t for t in sells if t["pnl_usd"] <= 0]
    total_pnl = sum(t["pnl_usd"] for t in sells)
    win_rate  = len(wins) / len(sells) * 100 if sells else 0

    print("\n" + "=" * 55)
    print("  📊 DRY RUN BALANCE SUMMARY")
    print("=" * 55)
    print(f"  Starting balance : ${dry_start_bal:.2f}")
    print(f"  Current balance  : ${bal:.2f}")
    print(f"  Total P&L        : ${total_pnl:+.2f}")
    print(f"  Net change       : {((bal - dry_start_bal) / dry_start_bal * 100):+.2f}%")
    print(f"  Completed trades : {len(sells)}")
    print(f"  Wins / Losses    : {len(wins)}W / {len(losses)}L ({win_rate:.0f}% win rate)")
    if sells:
        best  = max(sells, key=lambda t: t["pnl_usd"])
        worst = min(sells, key=lambda t: t["pnl_usd"])
        print(f"  Best trade       : {best['pair']} ${best['pnl_usd']:+.4f}")
        print(f"  Worst trade      : {worst['pair']} ${worst['pnl_usd']:+.4f}")
    print("=" * 55)


# ─────────────────────────────────────────────
# PAIR DISCOVERY
# ─────────────────────────────────────────────
def get_all_tickers() -> dict:
    """
    Fetch all Kraken tickers in one API call (public, no auth needed).
    NOTE: To see all fields: print(list(result["XBTUSD"].keys()))
    Fields: a=ask, b=bid, c=last, v=volume[today,24h], h=high, l=low, o=open
    """
    try:
        res = req.get(f"{BASE_URL}/0/public/Ticker", timeout=10)
        if res.status_code == 200:
            return res.json().get("result", {})
    except Exception as e:
        print(f"  ⚠️ Ticker fetch error: {e}")
    return {}


def get_market_regime() -> str:
    """
    Determine market regime using Fear & Greed + 24h market cap change.
      bull    — mkt_change > +1% AND fear_greed > 40  → small caps allowed, trend filter off
      bear    — mkt_change < -2% OR  fear_greed < 25  → $10M volume floor, trend filter on
      neutral — everything else                        → behaves like bull
    """
    try:
        from market_sentiment import get_market_sentiment
        s = get_market_sentiment()
        fg  = s.get("fear_greed", 50)
        chg = s.get("market_cap_change_24h", 0)
        if chg < -2.0 or fg < 25:
            return "bear"
        if chg > 1.0 and fg > 40:
            return "bull"
    except Exception:
        pass
    return "neutral"


def score_pairs(tickers: dict) -> list:
    """
    Score all pairs by volatility * volume, adjusted by historical performance.

    Performance multiplier from learning.db:
      - win rate > 60% and positive avg P&L  -> up to 1.5x boost
      - win rate < 40% or negative avg P&L   -> down to 0.5x penalty
      - no history                            -> neutral 1.0x
    """
    with market_regime_lock:
        regime = market_regime
    vol_floor = MIN_VOLUME_BEAR if regime == "bear" else MIN_VOLUME_USD

    scored = []
    for pair, data in tickers.items():
        try:
            high       = float(data["h"][1])
            low        = float(data["l"][1])
            last       = float(data["c"][0])
            vol        = float(data["v"][1])
            volume_usd = vol * last

            if low == 0 or volume_usd < vol_floor:
                continue

            volatility = (high - low) / low

            if volatility < MIN_VOLATILITY or last < 0.000001:
                continue

            # Base score: volatility weighted by log volume
            import math
            base_score = volatility * math.log1p(volume_usd)

            # Performance multiplier from learning history
            metrics = LEARNER.summarize(BOT_NAME, pair, lookback=30)
            if metrics["sample_size"] >= 5:
                win_rate  = metrics["win_rate"]
                avg_pnl   = metrics["avg_pnl_pct"]
                if win_rate > 0.60 and avg_pnl > 0:
                    perf_mult = 1.0 + min(0.5, win_rate - 0.60 + avg_pnl * 5)
                elif win_rate < 0.40 or avg_pnl < 0:
                    perf_mult = max(0.5, win_rate + 0.1)
                else:
                    perf_mult = 1.0
            else:
                perf_mult = 1.0

            final_score = base_score * perf_mult
            scored.append((pair, final_score, volume_usd, perf_mult))

            if DEBUG_PAIRS:
                print(f"  {pair:<20} vol={volatility*100:.1f}% score={final_score:.2f} perf={perf_mult:.2f}x")

        except (KeyError, ValueError, ZeroDivisionError):
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def get_top_pairs(n: int = TOP_N) -> list:
    """Returns top N pairs by combined volatility + performance score."""
    tickers = get_all_tickers()
    if not tickers:
        return []

    scored = score_pairs(tickers)
    top    = scored[:n]

    print(f"\n  🔍 Top {n} pairs (volatility + performance score):")
    for pair, score, vol_usd, perf_mult in top:
        boost = f" {perf_mult:.2f}x" if perf_mult != 1.0 else ""
        print(f"     {pair:<20} score={score:.2f}{boost} | ${vol_usd:,.0f} 24h volume")

    return [pair for pair, _, _, _ in top]


# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────
def calculate_rsi(closes: list, period: int = 14) -> float:
    """RSI from closing prices. NOTE: To debug: print(closes[-period:])"""
    if len(closes) < period + 1:
        return 50.0

    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-period + i] - closes[-period + i - 1]
        if delta >= 0:
            gains.append(delta); losses.append(0)
        else:
            gains.append(0); losses.append(abs(delta))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_pair_data(pair: str) -> tuple:
    """
    Returns (price, rsi, recent_high) for any pair.
    NOTE: To rediscover OHLC fields: print(get_ohlc(pair, 1)[0])
    Candle: [time, open, high, low, close, vwap, volume, count]
    """
    ticker = get_ticker(pair)
    if not ticker:
        return None, None, None, None

    try:
        price = float(ticker.get("a", [0])[0])
    except:
        return None, None, None, None

    candles = get_ohlc(pair, interval=1)  # 1-min candles for HFT
    if not candles or len(candles) < RSI_PERIOD + 5:
        return price, 50.0, price, price

    closes      = [float(c[4]) for c in candles]
    highs       = [float(c[2]) for c in candles]
    rsi         = calculate_rsi(closes, RSI_PERIOD)
    recent_high = max(highs[-30:])
    ma20        = sum(closes[-20:]) / min(20, len(closes))

    return price, rsi, recent_high, ma20


# ─────────────────────────────────────────────
# TRADE SIZING
# ─────────────────────────────────────────────
def get_trade_size(pair: str, price: float, dry_run: bool) -> float:
    """Position size: use available USD up to MAX_USD_PER_PAIR, respecting Kraken's dynamic ordermin."""
    if price <= 0:
        return 0.0

    if dry_run:
        usd_avail = dry_available_usd()
    else:
        balances  = get_balance()
        usd_avail = max(0, float(balances.get("ZUSD", 0)) - RESERVE_USD)

    remaining = MAX_TOTAL_USD - deployed_total()

    trade_usd = min(usd_avail, remaining, MAX_USD_PER_PAIR)

    if trade_usd <= 0:
        return 0.0

    # calculate_order_size fetches Kraken's real ordermin for this pair
    info = calculate_order_size(pair, price, available_usd=trade_usd)
    if not info.get("can_afford"):
        return 0.0
    return info["volume"]




def deployed_total() -> float:
    """USD currently committed to open positions (at entry price)."""
    with positions_lock:
        return sum(p["entry_price"] * p["volume"] for p in positions.values())


# ─────────────────────────────────────────────
# POSITION LIFECYCLE
# ─────────────────────────────────────────────
def _save(pair: str, position: dict, dry_run: bool) -> None:
    with positions_lock:
        positions[pair] = position
    if not dry_run:
        STORE.set(pair, position)


def _forget(pair: str, dry_run: bool) -> None:
    with positions_lock:
        positions.pop(pair, None)
    if not dry_run:
        STORE.remove(pair)


def open_position(pair: str, volume: float, price: float, confidence: float,
                  features: dict, reason: str, dry_run: bool) -> dict | None:
    """Buy, protect with a stop-loss, record and persist. Returns the position or None."""
    ts = datetime.now().strftime("%H:%M:%S")
    if dry_run:
        if not dry_buy(pair, volume, price):
            print(f"  [DRY] ⚠️ Insufficient virtual balance")
            return None
        position = {"entry_price": price, "volume": volume, "fees": 0.0}
        _save(pair, position, dry_run)
        print(f"  [DRY] Bought {volume} {pair} @ ${price:.4f} | Balance: ${dry_balance:.2f}")
        return position

    ok, result = place_order(pair, "buy", "market", volume, userref=USERREF)
    if not ok:
        print(f"  ❌ Buy failed: {result}")
        return None

    # Use the actual fill, not the ticker price we saw before ordering.
    txid = (result.get("txid") or [None])[0]
    fill = get_fill(txid) if txid else None
    if fill and fill["vol_exec"] > 0:
        volume, price, fee = fill["vol_exec"], fill["avg_price"], fill["fee"]
    else:
        fee = 0.0
        print(f"  [{ts}] ⚠️ {pair}: could not read fill for {txid}; using requested volume/price")

    exit_orders = place_exit_orders(pair, volume, price, PROFIT_PCT, STOP_PCT, userref=USERREF)
    trade_id = LEARNER.record_entry(
        bot_name=BOT_NAME, symbol=pair, side="buy", entry_price=price,
        position_size=volume, confidence=confidence, features=features,
        config={"rsi_oversold": RSI_OVERSOLD, "profit_pct": PROFIT_PCT, "stop_pct": STOP_PCT},
    )
    position = {
        "entry_price": price, "volume": volume, "fees": fee,
        "exit_orders": exit_orders, "trade_id": trade_id,
        "entry_time": datetime.now().isoformat(timespec="seconds"),
        "order_txid": txid,
    }
    _save(pair, position, dry_run)
    tg(f"🛒 *{pair}* buy {volume:.8g} @ ${price:.6g} | {reason}")
    return position


def close_position(pair: str, position: dict, price: float, reason: str,
                   dry_run: bool, exchange_filled: bool = False) -> bool:
    """
    Sell (unless the exchange already did), record net P&L and forget the position.
    Returns False if the position is still open (sell failed); the caller retries.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    entry, volume = position["entry_price"], position["volume"]
    fees = position.get("fees", 0.0)

    if dry_run:
        pnl = dry_sell(pair, volume, price, entry)
        print(f"  [DRY] {reason}: sold {volume} {pair} | P&L: ${pnl:+.4f} | Balance: ${dry_balance:.2f}")
        _forget(pair, dry_run)
        return True

    exit_price = price
    eo = position.get("exit_orders") or {}
    if exchange_filled:
        fill = get_fill(eo["sl"]) if eo.get("sl") else None
        if fill and fill["vol_exec"] > 0:
            exit_price, volume = fill["avg_price"], fill["vol_exec"]
            fees += fill["fee"]
    else:
        cancel_remaining_exit(eo)  # raises if open orders can't be read → retried next cycle
        held = get_pair_base_balance(pair)
        sell_volume = min(volume, held) if held > 0 else volume
        ok, result = place_order(pair, "sell", "market", sell_volume, userref=USERREF)
        if not ok and result.get("below_minimum"):
            # Too small to sell on Kraken: stop managing it instead of retrying forever.
            print(f"  [{ts}] 🧹 {pair}: {sell_volume} is below Kraken's minimum — dropping as dust")
            reason = "dust"
        elif not ok:
            print(f"  ❌ {pair} sell failed: {result}")
            # The SL was cancelled above; put protection back before retrying.
            position["exit_orders"] = place_exit_orders(pair, volume, entry, PROFIT_PCT, STOP_PCT, userref=USERREF)
            _save(pair, position, dry_run)
            return False
        else:
            txid = (result.get("txid") or [None])[0]
            fill = get_fill(txid) if txid else None
            if fill and fill["vol_exec"] > 0:
                exit_price, volume = fill["avg_price"], fill["vol_exec"]
                fees += fill["fee"]

    pnl_usd = (exit_price - entry) * volume - fees
    pnl_pct = pnl_usd / (entry * volume) if entry and volume else 0.0
    if position.get("trade_id"):
        LEARNER.record_exit(position["trade_id"], exit_price, pnl_usd, pnl_pct, reason)
    _forget(pair, dry_run)
    icon = "💰" if pnl_usd > 0 else "🛑"
    print(f"  [{ts}] {icon} {pair} closed ({reason}) @ ${exit_price:.6g} | net {pnl_pct*100:+.2f}% (${pnl_usd:+.4f})")
    tg(f"{icon} *{pair}* {reason} {pnl_pct*100:+.2f}% (${pnl_usd:+.4f} net)")
    return True


def ensure_protected(pair: str, position: dict, dry_run: bool) -> None:
    """Re-place a missing stop-loss (failed, cancelled elsewhere, or restored), rate-limited."""
    if dry_run:
        return
    eo = position.get("exit_orders") or {}
    if eo.get("sl") or eo.get("below_minimum"):
        return
    now = time.time()
    if now < position.get("sl_retry_at", 0):
        return
    position["sl_retry_at"] = now + SL_RETRY_SECS
    print(f"  🛡️ {pair}: no stop-loss on Kraken — placing one")
    position["exit_orders"] = place_exit_orders(
        pair, position["volume"], position["entry_price"], PROFIT_PCT, STOP_PCT, userref=USERREF,
    )
    _save(pair, position, dry_run)


def restore_positions() -> dict:
    """
    Reload saved positions after a restart and reconcile them with Kraken:
      - stop-loss filled while we were down → record the exit
      - coins no longer held → record as closed externally
      - otherwise keep managing it (volume capped at what is held)
    """
    stored = STORE.all()
    if not stored:
        return {}
    balances = get_balance()
    restored = {}
    for pair, pos in stored.items():
        eo = pos.get("exit_orders") or {}
        status = get_order_status(eo["sl"]) if eo.get("sl") else None
        if status == "closed":
            print(f"  ♻️ {pair}: stop-loss filled while the bot was down")
            close_position(pair, pos, eo.get("sl_price", pos["entry_price"]), "stop_loss", False, exchange_filled=True)
            continue
        if status in ("canceled", "expired"):
            eo["sl"] = None
            pos["exit_orders"] = eo

        if not balances:
            print(f"  ⚠️ {pair}: balance unavailable — restoring saved position as-is")
            restored[pair] = pos
            continue
        held = get_pair_base_balance(pair, balances=balances)
        if held < pos["volume"] * 0.5:
            print(f"  ♻️ {pair}: saved position no longer held ({held}) — recording as closed externally")
            if eo.get("sl") and status in ("open", "pending"):
                cancel_order(eo["sl"])  # its coins are gone; don't leave a stray stop-loss
            ticker = get_ticker(pair)
            price = float(ticker.get("c", [pos["entry_price"]])[0]) if ticker else pos["entry_price"]
            close_position(pair, pos, price, "closed_externally", False, exchange_filled=True)
            continue
        pos["volume"] = min(pos["volume"], held)
        restored[pair] = pos
        print(f"  ♻️ Restored {pair}: {pos['volume']:.8g} @ ${pos['entry_price']:.6g}")
    with positions_lock:
        positions.update(restored)
    for pair, pos in restored.items():
        STORE.set(pair, pos)
    return restored


# ─────────────────────────────────────────────
# PER-PAIR TRADING THREAD
# ─────────────────────────────────────────────
def trade_pair(pair: str, dry_run: bool, stop_event: threading.Event, position: dict | None = None):
    """
    Runs in its own thread. Monitors one pair and trades it.
    When stop_event is set the thread stops looking for entries, but keeps
    managing an open position until it is closed.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] 🚀 Starting thread for {pair}" + (" (restored position)" if position else ""))

    while not (stop_event.is_set() and position is None):
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            price, rsi, recent_high, ma20 = get_pair_data(pair)
            if price is None:
                time.sleep(CHECK_SECS)
                continue

            dip = (recent_high - price) / recent_high if recent_high else 0

            # ── MANAGE POSITION ──────────────────────────
            if position:
                if not dry_run and check_exit_orders(position.get("exit_orders")):
                    close_position(pair, position, price, "stop_loss", dry_run, exchange_filled=True)
                    position = None
                    continue

                ensure_protected(pair, position, dry_run)

                entry   = position["entry_price"]
                pnl_pct = (price - entry) / entry
                pnl_usd = (price - entry) * position["volume"]
                bal_str = f" | 💰 Bal: ${dry_balance:.2f}" if dry_run else ""
                print(f"  [{ts}] {pair:<18} ${price:.6g} | RSI {rsi:.0f} | PnL {pnl_pct*100:+.2f}% (${pnl_usd:+.4f}){bal_str}")

                if pnl_pct >= PROFIT_PCT or pnl_pct <= -STOP_PCT:
                    reason = "take_profit" if pnl_pct >= PROFIT_PCT else "stop_loss"
                    if close_position(pair, position, price, reason, dry_run):
                        position = None

            # ── LOOK FOR ENTRY ────────────────────────────
            elif not stop_event.is_set():
                rsi_signal = rsi <= RSI_OVERSOLD
                dip_signal = DIP_MIN <= dip <= DIP_MAX
                not_overbought = rsi < RSI_OVERBOUGHT

                # Trend filter: active in bear market, disabled in bull/neutral
                with market_regime_lock:
                    regime = market_regime
                uptrend = (price > ma20) if regime == "bear" else True

                # Don't buy what we already hold (other bot or manual holding)
                already_held = not dry_run and get_pair_base_balance(pair, balances=get_balance()) * price > 5.0

                bal_str   = f" | 💰 Bal: ${dry_balance:.2f}" if dry_run else ""
                held_str  = " HELD" if already_held else ""
                print(f"  [{ts}] {pair:<18} ${price:.6g} | RSI {rsi:.0f} | Dip {dip*100:.1f}% | "
                      f"Trend {'UP' if uptrend else 'DOWN'} [{regime}]{held_str}{bal_str}")

                confidence = min(1.0, (RSI_OVERSOLD - rsi) / RSI_OVERSOLD + dip)

                if (rsi_signal or dip_signal) and not_overbought and uptrend and not already_held:
                    if confidence < MIN_CONFIDENCE:
                        print(f"  [{ts}] 🚫 {pair} skipped: confidence {confidence:.3f} < {MIN_CONFIDENCE}")
                    else:
                        sentiment = check_sentiment()
                        if not sentiment["allow"]:
                            print(f"  [{ts}] 🚫 {pair} signal blocked: {sentiment['reason']}")
                        else:
                            volume = round(get_trade_size(pair, price, dry_run) * sentiment["size_multiplier"], 8)
                            if volume <= 0:
                                print(f"  [{ts}] ⚠️ {pair} signal but no budget left")
                            else:
                                reason = f"RSI {rsi:.0f} + dip -{dip*100:.1f}%"
                                print(f"  [{ts}] 🎯 {pair} ENTRY: {reason} | Buying {volume} @ ${price:.6g} (${volume*price:.2f})")
                                position = open_position(
                                    pair, volume, price, confidence,
                                    {"rsi": rsi, "dip": dip, "price": price, "ma20": ma20},
                                    reason, dry_run,
                                )

            time.sleep(CHECK_SECS)

        except Exception as e:
            print(f"  [{ts}] ⚠️ {pair} error: {e}")
            time.sleep(CHECK_SECS)

    print(f"  [{datetime.now().strftime('%H:%M:%S')}] 🛑 Thread stopped: {pair}")


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def _start_thread(pair: str, dry_run: bool, stop_events: dict, position: dict | None = None):
    stop_event = threading.Event()
    stop_events[pair] = stop_event
    t = threading.Thread(target=trade_pair, args=(pair, dry_run, stop_event, position),
                         daemon=True, name=f"thread-{pair}")
    t.start()
    active_threads[pair] = t


def run(dry_run: bool = False, sim_balance: float = DRY_START_BAL):
    global dry_balance, dry_start_bal, market_regime

    if dry_run:
        dry_balance   = sim_balance
        dry_start_bal = sim_balance

    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    print("=" * 60)
    print(f"  Dynamic HFT Bot — Kraken  {mode}")
    print(f"  Top pairs       : {TOP_N} (rescanned every {RESCAN_SECS//60} min)")
    print(f"  Profit target   : +{PROFIT_PCT*100:.0f}%")
    print(f"  Stop loss       : -{STOP_PCT*100:.0f}%")
    print(f"  Max per pair    : ${MAX_USD_PER_PAIR}")
    print(f"  Max total       : ${MAX_TOTAL_USD}")
    print(f"  Check every     : {CHECK_SECS}s per pair")
    if dry_run:
        print(f"  Sim balance     : ${sim_balance:.2f}")
    print("=" * 60)
    tg(f"🤖 *Dynamic HFT Bot started* ({mode}) — {TOP_N} pairs, ${MAX_TOTAL_USD} max")

    stop_events = {}
    if not dry_run:
        for pair, pos in restore_positions().items():
            _start_thread(pair, dry_run, stop_events, pos)

    scan_cycle = 0
    while True:
        try:
            scan_cycle += 1
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{ts}] 🔍 Scan #{scan_cycle} — finding top {TOP_N} volatile pairs...")

            new_regime = get_market_regime()
            with market_regime_lock:
                prev_regime = market_regime
                market_regime = new_regime
            if new_regime != prev_regime:
                print(f"  [{ts}] 📊 Market regime: {prev_regime.upper()} → {new_regime.upper()}")
                tg(f"📊 Market regime changed: *{prev_regime.upper()}* → *{new_regime.upper()}*")
            else:
                print(f"  [{ts}] 📊 Market regime: {new_regime.upper()}")

            # Clean up threads that finished (dropped pairs whose position closed)
            for pair in [p for p, t in active_threads.items() if not t.is_alive()]:
                del active_threads[pair]
                stop_events.pop(pair, None)

            new_pairs = get_top_pairs(TOP_N)
            if not new_pairs:
                print(f"  ⚠️ Could not fetch pairs, retrying in 60s")
                time.sleep(60)
                continue

            with positions_lock:
                held_pairs = set(positions)
            current_pairs = set(active_threads.keys())
            pairs_to_add  = set(new_pairs) - current_pairs
            pairs_to_drop = current_pairs - set(new_pairs)

            for pair in pairs_to_drop:
                if pair in stop_events and not stop_events[pair].is_set():
                    note = " (keeps managing its open position)" if pair in held_pairs else ""
                    print(f"  [{ts}] ⏹ Dropping {pair} from rotation{note}")
                    stop_events[pair].set()

            for pair in pairs_to_add:
                print(f"  [{ts}] ▶️ Adding {pair} to rotation")
                _start_thread(pair, dry_run, stop_events)

            if not pairs_to_add and not pairs_to_drop:
                print(f"  ✅ Same top {TOP_N} pairs — no rotation needed")

            dep = deployed_total()
            if dry_run:
                print(f"\n  📊 Positions: {len(held_pairs)} | Deployed: ${dep:.2f} | "
                      f"💰 Balance: ${dry_balance:.2f} | P&L: ${dry_balance - dry_start_bal:+.2f}")
                print_dry_summary()
            else:
                usd_bal = float(get_balance().get("ZUSD", 0))
                print(f"\n  📊 Positions: {len(held_pairs)} | Deployed: ${dep:.2f} | USD balance: ${usd_bal:.2f}")

            time.sleep(RESCAN_SECS)

        except KeyboardInterrupt:
            print("\n\n👋 Shutting down (open positions stay protected by their stop-losses)...")
            for event in stop_events.values():
                event.set()
            if dry_run:
                print_dry_summary()
            tg("🛑 *Dynamic HFT Bot stopped*")
            break
        except Exception as e:
            print(f"⚠️ Main loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Dry run — no real orders placed")
    parser.add_argument("--sim-balance", type=float, default=DRY_START_BAL,
                        help=f"Starting virtual balance for dry run (default: ${DRY_START_BAL})")
    args = parser.parse_args()
    run(dry_run=args.dry, sim_balance=args.sim_balance)
