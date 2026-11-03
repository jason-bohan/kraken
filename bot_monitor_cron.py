#!/usr/bin/env python3
"""
bot_monitor_cron.py — Scheduled health check and parameter tuning.

Run via cron every 30 minutes, with the venv's python (bots are restarted
with the same interpreter that runs this script):
    */30 * * * * cd /path/to/kraken && venv/bin/python bot_monitor_cron.py >> logs/monitor_cron.log 2>&1

What it does:
  1. Health check: restart any crashed bot screen sessions
  2. Learning: report win rate / P&L from the learning DB
  3. Protection: stop-losses (and take-profit sells) for holdings NO bot manages.
     Pairs listed in a bot's state/*.json file are left to that bot.
  4. Report: print summary (and optionally Telegram)
"""

import os
import shlex
import subprocess
import sys
import time
from datetime import datetime

# Ensure we run from the project directory (needed for cron)
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)
os.makedirs("logs", exist_ok=True)

from dotenv import load_dotenv
load_dotenv()

from kraken_connection import (
    get_balance, get_ticker, get_open_orders, place_order, get_min_order_info,
    cancel_order, pair_altname,
)
from position_store import all_bot_positions
from bot_registry import BOTS
from notify import notify
from position_guardian import _fmt_price
from learning_engine import LearningEngine
from market_sentiment import get_market_sentiment, format_sentiment, should_buy as check_sentiment

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

LEARNER = LearningEngine()

# Expected bot screen sessions: name -> (script args, log file, optional key profile).
# A key profile is used only if KRAKEN_API_KEY_<PROFILE> is set in .env; separate
# keys per process avoid "EAPI:Invalid nonce" (see .env.example).
EXPECTED_BOTS = {name: (b["args"], b["log"], b["profile"]) for name, b in BOTS.items()}

# Bot name -> (learning engine bot_name, symbols it trades)
BOT_LEARNING = {
    "bot_BTC":     ("stock_swing_bot", ["BTC"]),
    "bot_ETH":     ("stock_swing_bot", ["ETH"]),
    "bot_SOL":     ("stock_swing_bot", ["SOL"]),
    "bot_ADA":     ("stock_swing_bot", ["ADA"]),
    "bot_HFT":     ("dynamic_hft_bot", None),  # None = check all symbols in DB
}


def bot_command(args: list[str], log_path: str, profile: str | None) -> str:
    """Shell command to start a bot with this interpreter, appending to its log."""
    env = ""
    if profile and os.getenv(f"KRAKEN_API_KEY_{profile}"):
        env = f"KRAKEN_KEY_PROFILE={profile} "
    cmd = " ".join(shlex.quote(a) for a in [sys.executable, "-u", *args])
    return f"{env}{cmd} >> {shlex.quote(log_path)} 2>&1"


def bot_owned_pairs() -> set[str]:
    """Altnames of every pair a bot is currently managing (from state/*.json)."""
    owned = set()
    for positions in all_bot_positions().values():
        for pair in positions:
            owned.add(pair_altname(pair))
    return owned

# Asset -> pair mapping for protection scan
ASSET_TO_PAIR = {
    "RIVER": "RIVERUSD", "WAR": "WARUSD", "HYPE": "HYPEUSD", "SUI": "SUIUSD",
    "PUMP": "PUMPUSD", "ICP": "ICPUSD", "XZEC": "ZECUSD", "XZECZ": "ZECUSD",
    "DOT": "DOTUSD", "PLUME": "PLUMEUSD", "ZRO": "ZROUSD", "TAO": "TAOUSD",
    "SOL": "SOLUSD", "ADA": "ADAUSD", "DOGE": "XDGUSD", "XDG": "XDGUSD",
    "XXDG": "XDGUSD", "ETH": "ETHUSD", "XETH": "ETHUSD", "XETHZ": "ETHUSD",
    "BTC": "XBTUSD", "XXBT": "XBTUSD", "XBT": "XBTUSD", "AVAX": "AVAXUSD",
    "IDOS": "IDOSUSD", "LINK": "LINKUSD", "XPL": "XPLUSD", "ALCX": "ALCXUSD",
    "NEAR": "NEARUSD", "PEPE": "PEPEUSD", "BABY": "BABYUSD",
    "FARTCOIN": "FARTCOINUSD", "HBAR": "HBARUSD", "NIGHT": "NIGHTUSD",
    "RENDER": "RENDERUSD", "TRUMP": "TRUMPUSD", "XCN": "XCNUSD",
}

SKIP_ASSETS = {"ZUSD", "USD", "USDC", "USDT", "KFEE", "NFTS"}

PROFIT_PCT = 0.10            # 10% profit target — backtested optimal
STOP_PCT = 0.08              # 8% stop loss — wider avoids noise stopouts
MIN_USD_VALUE = 0.50


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def tg(msg: str):
    notify(msg)


def get_running_screens() -> set[str]:
    """Return set of active screen session names."""
    try:
        out = subprocess.check_output(["screen", "-ls"], text=True, stderr=subprocess.STDOUT)
        names = set()
        for line in out.splitlines():
            line = line.strip()
            if "." in line and ("Detached" in line or "Attached" in line):
                # Format: "12345.bot_BTC (date) (Detached)"
                name = line.split(".")[1].split("\t")[0].split(" ")[0]
                names.add(name)
        return names
    except subprocess.CalledProcessError:
        return set()


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────
# 1. HEALTH CHECK — restart crashed bots
# ─────────────────────────────────────────────

def health_check() -> list[str]:
    """Check that all expected bot screens are running. Restart any that crashed."""
    running = get_running_screens()
    restarted = []

    for name, (args, log_path, profile) in EXPECTED_BOTS.items():
        if name in running:
            continue

        print(f"  [{ts()}] RESTART: {name} is not running — relaunching")
        subprocess.run(
            ["screen", "-dmS", name, "bash", "-c", bot_command(args, log_path, profile)],
            cwd=PROJECT_DIR,
        )
        restarted.append(name)

    return restarted


# ─────────────────────────────────────────────
# 2. LEARNING — tune parameters
# ─────────────────────────────────────────────

def run_tuning() -> list[str]:
    """Run learning engine tuning for bots with enough trade data."""
    tuning_notes = []

    for bot_screen, (bot_name, symbols) in BOT_LEARNING.items():
        if symbols is None:
            # HFT bot — get all symbols it has traded
            with LEARNER._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT symbol FROM trade_log WHERE bot_name = ? AND status = 'closed'",
                    (bot_name,),
                ).fetchall()
            symbols = [r["symbol"] for r in rows]

        for symbol in symbols:
            metrics = LEARNER.summarize(bot_name, symbol)
            if metrics["sample_size"] < 5:
                continue

            note = (
                f"  {bot_name}/{symbol}: "
                f"{metrics['sample_size']} trades, "
                f"{metrics['win_rate']*100:.0f}% win rate, "
                f"avg P&L {metrics['avg_pnl_pct']*100:.1f}%"
            )
            print(f"  [{ts()}] {note}")
            tuning_notes.append(note)

            if metrics["sample_size"] >= 12:
                base_config = {
                    "min_confidence": 0.6,
                    "rsi_oversold": 40,
                    "volatility_threshold": 0.05,
                    "profit_target": PROFIT_PCT,
                    "stop_loss": STOP_PCT,
                }
                tuned, tune_metrics = LEARNER.tune_config(bot_name, symbol, base_config)
                if tune_metrics.get("tuning_applied", True):
                    changes = {k: v for k, v in tuned.items() if v != base_config.get(k)}
                    if changes:
                        print(f"  [{ts()}] TUNED {bot_name}/{symbol}: {changes}")
                        tuning_notes.append(f"  TUNED: {changes}")

    return tuning_notes


# ─────────────────────────────────────────────
# 3. PROTECTION — OTO brackets for unprotected positions
# ─────────────────────────────────────────────

def protect_positions() -> list[str]:
    """Find unprotected holdings and add standalone stop-loss orders.
    Also migrate old OTO brackets to standalone SLs and handle stale SLs."""
    protected_notes = []

    balances = get_balance()
    open_orders = get_open_orders()
    owned = bot_owned_pairs()
    if owned:
        print(f"  [{ts()}] Managed by bots (skipped): {', '.join(sorted(owned))}")

    # Categorize existing sell orders by pair
    pair_orders: dict[str, list[dict]] = {}  # pair -> [{"txid", "descr", "type"}, ...]
    for txid, order in open_orders.items():
        descr = order.get("descr", {})
        if descr.get("type") == "sell":
            pair = descr.get("pair", "")
            if pair_altname(pair) in owned:
                continue
            order_type = descr.get("ordertype", "")
            close_str = descr.get("close", "")
            entry = {"txid": txid, "descr": descr, "order_type": order_type,
                     "vol": float(order.get("vol", 0)), "has_close": bool(close_str)}
            pair_orders.setdefault(pair, []).append(entry)

    # Step 1: Migrate old OTO brackets → cancel and replace with standalone SL
    for pair, orders in pair_orders.items():
        for entry in orders:
            if not entry["has_close"]:
                continue  # Already a standalone order, skip

            # This is an old OTO bracket (limit sell with conditional close)
            close_str = entry["descr"].get("close", "")
            try:
                sl_price = float(close_str.split("stop loss")[-1].strip())
            except (ValueError, IndexError):
                continue

            ticker = get_ticker(pair)
            if not ticker:
                continue
            price = float(ticker.get("c", [0])[0])
            vol = entry["vol"]
            txid = entry["txid"]

            min_info = get_min_order_info(pair)
            if min_info and vol < min_info.get("ordermin", 0):
                continue

            if price < sl_price:
                # Price already below SL — cancel OTO and market sell immediately
                print(f"  [{ts()}] STALE OTO: {pair} ${price:.4f} < SL ${sl_price:.4f} — selling")
                cancel_order(txid)
                time.sleep(1)
                ok, result = place_order(pair, "sell", "market", volume=vol)
                if ok:
                    note = f"  SOLD {pair}: price below SL (${price:.4f} < ${sl_price:.4f})"
                    print(f"  [{ts()}] {note}")
                    protected_notes.append(note)
                    tg(f"🔴 *SL fired*: sold {vol} {pair} @ ~${price:.4f} (below SL ${sl_price:.4f})")
                else:
                    print(f"  [{ts()}] Failed to sell {pair}: {result}")
            else:
                # OTO bracket still above SL — migrate to standalone SL
                print(f"  [{ts()}] MIGRATE: {pair} OTO bracket → standalone SL @ ${sl_price:.4f}")
                cancel_order(txid)
                time.sleep(1)
                sl_price_str = _fmt_price(sl_price, pair)
                ok, result = place_order(pair, "sell", "stop-loss", volume=vol, price=sl_price_str)
                if ok:
                    note = f"  MIGRATED {pair}: standalone SL @ ${sl_price:.4f}"
                    print(f"  [{ts()}] {note}")
                    protected_notes.append(note)
                else:
                    print(f"  [{ts()}] Failed to migrate {pair}: {result}")

    # Step 2: Check standalone stop-loss orders for stale SLs
    # (stop-loss orders fire automatically, but double-check just in case)

    # Re-fetch orders after any migrations
    if protected_notes:
        open_orders = get_open_orders()

    # Build set of pairs with any sell order (standalone SL or limit)
    protected_pairs_set = set()
    for order in open_orders.values():
        descr = order.get("descr", {})
        if descr.get("type") == "sell":
            protected_pairs_set.add(descr.get("pair", ""))

    # Step 3: Check TP on positions with existing SL orders
    # (Catches orphaned positions where no bot is monitoring TP)
    if protected_notes:
        open_orders = get_open_orders()
    for txid, order in open_orders.items():
        descr = order.get("descr", {})
        if descr.get("type") != "sell" or descr.get("ordertype") != "stop-loss":
            continue
        pair = descr.get("pair", "")
        if pair_altname(pair) in owned:
            continue  # the bot handles its own take-profit
        sl_price = float(descr.get("price", 0))
        vol = float(order.get("vol", 0))
        if sl_price <= 0 or vol <= 0:
            continue

        # Estimate entry from SL price
        entry_est = sl_price / (1 - STOP_PCT)
        tp_price = entry_est * (1 + PROFIT_PCT)

        ticker = get_ticker(pair)
        if not ticker:
            continue
        price = float(ticker.get("c", [0])[0])
        pnl_pct = (price - entry_est) / entry_est if entry_est > 0 else 0

        if price >= tp_price:
            print(f"  [{ts()}] TP HIT: {pair} ${price:.4f} >= ${tp_price:.4f} (+{pnl_pct*100:.1f}%) — selling")
            cancel_order(txid)
            time.sleep(1)
            ok, result = place_order(pair, "sell", "market", volume=vol)
            if ok:
                pnl_usd = (price - entry_est) * vol
                note = f"  TP SOLD {pair}: +{pnl_pct*100:.1f}% (${pnl_usd:+.2f})"
                print(f"  [{ts()}] {note}")
                protected_notes.append(note)
                tg(f"💰 *TP hit*: sold {vol} {pair} @ ${price:.4f} (+{pnl_pct*100:.1f}%)")
            else:
                print(f"  [{ts()}] TP sell failed for {pair}: {result}")

    # Step 4: Place standalone stop-loss on unprotected positions
    for asset, bal_str in balances.items():
        volume = float(bal_str)
        if volume <= 0 or asset in SKIP_ASSETS:
            continue

        pair = ASSET_TO_PAIR.get(asset)
        if not pair or pair in protected_pairs_set or pair_altname(pair) in owned:
            continue

        ticker = get_ticker(pair)
        if not ticker:
            continue

        price = float(ticker.get("c", [0])[0])
        if price <= 0:
            continue

        usd_value = volume * price
        if usd_value < MIN_USD_VALUE:
            continue

        # Check if volume meets minimum sell requirement
        min_info = get_min_order_info(pair)
        if min_info and volume < min_info.get("ordermin", 0):
            continue

        sl_price = price * (1 - STOP_PCT)
        sl_price_str = _fmt_price(sl_price, pair)

        ok, result = place_order(
            pair=pair,
            side="sell",
            order_type="stop-loss",
            volume=volume,
            price=sl_price_str,
        )

        if ok:
            note = f"  PROTECTED {asset} ({pair}): SL @ ${sl_price:.4f} (-{STOP_PCT*100:.0f}%)"
            print(f"  [{ts()}] {note}")
            protected_notes.append(note)
        else:
            err = result.get("error", result)
            print(f"  [{ts()}] Failed to protect {asset}: {err}")

    return protected_notes


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"  BOT MONITOR — {ts()}")
    print(f"{'='*60}")

    # 1. Health check
    print(f"\n  [Health Check]")
    restarted = health_check()
    if restarted:
        print(f"  Restarted: {', '.join(restarted)}")
    else:
        print(f"  All bots running")

    # 2. Learning / tuning
    print(f"\n  [Learning]")
    tuning_notes = run_tuning()
    if not tuning_notes:
        print(f"  Not enough closed trades yet for tuning")

    # 3. Protection
    print(f"\n  [Protection]")
    try:
        protected_notes = protect_positions()
    except Exception as e:
        # e.g. open orders could not be fetched — skip rather than act blind
        print(f"  Protection step skipped: {e}")
        protected_notes = [f"Protection skipped: {e}"]
    if not protected_notes:
        print(f"  All eligible positions protected")

    # 4. Summary
    print(f"\n  [Summary]")
    running = get_running_screens()
    print(f"  Bots running: {len(running)}/{len(EXPECTED_BOTS)}")

    # 5. Portfolio snapshot for Telegram
    try:
        balances = get_balance()
        usd_bal = float(balances.get("ZUSD", 0))
        total_value = usd_bal
        top_holdings = []

        for asset, bal_str in balances.items():
            vol = float(bal_str)
            if vol <= 0 or asset in SKIP_ASSETS:
                continue
            pair = ASSET_TO_PAIR.get(asset)
            if not pair:
                continue
            ticker = get_ticker(pair)
            if not ticker:
                continue
            price = float(ticker.get("c", [0])[0])
            value = vol * price
            if value < 0.10:
                continue
            total_value += value
            name = {"XXBT": "BTC", "XETH": "ETH", "XETHZ": "ETH", "XDG": "DOGE",
                     "XXDG": "DOGE", "XZEC": "ZEC", "XZECZ": "ZEC"}.get(asset, asset)
            top_holdings.append((name, value))

        top_holdings.sort(key=lambda x: -x[1])
    except Exception:
        total_value = 0
        top_holdings = []
        usd_bal = 0

    # Sentiment check (now includes on-chain signals)
    print(f"\n  [Sentiment + On-Chain]")
    try:
        sentiment = get_market_sentiment()
        decision = check_sentiment(sentiment)
        print(f"  {format_sentiment(sentiment)}")
        print(f"  Buy allowed: {decision['allow']} | Size: {decision['size_multiplier']}x")
        print(f"  {decision['reason']}")
    except Exception as e:
        print(f"  Sentiment check failed: {e}")
        sentiment = {}
        decision = {"allow": True, "reason": "Sentiment unavailable"}

    onchain_line = ""
    try:
        from onchain_signals import get_onchain_signals, format_onchain
        onchain = get_onchain_signals()
        onchain_line = format_onchain(onchain)
        print(f"  {onchain_line}")
    except Exception as e:
        print(f"  On-chain unavailable: {e}")

    # Telegram report — always send the 30-min snapshot
    lines = [f"*30m Update — {ts()}*"]
    fg = sentiment.get("fear_greed", "?")
    fg_label = sentiment.get("fear_greed_label", "?")
    mkt_chg = sentiment.get("market_cap_change_24h", 0)
    lines.append(f"Sentiment: F&G {fg} ({fg_label}) | Mkt {mkt_chg:+.1f}%")
    if onchain_line:
        lines.append(f"On-chain: {onchain_line}")
    lines.append(f"Bots: {len(running)}/{len(EXPECTED_BOTS)}")
    if total_value > 0:
        lines.append(f"Portfolio: *${total_value:.2f}* (cash ${usd_bal:.2f})")
        for name, value in top_holdings[:5]:
            lines.append(f"  {name}: ${value:.2f}")
        if len(top_holdings) > 5:
            rest = sum(v for _, v in top_holdings[5:])
            lines.append(f"  +{len(top_holdings)-5} more: ${rest:.2f}")
    if restarted:
        lines.append(f"Restarted: {', '.join(restarted)}")
    for note in tuning_notes[:3]:
        lines.append(note.strip())
    for note in protected_notes[:3]:
        lines.append(note.strip())
    tg("\n".join(lines))

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
