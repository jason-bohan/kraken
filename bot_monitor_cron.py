#!/usr/bin/env python3
"""
bot_monitor_cron.py — Scheduled health check and parameter tuning.

Run via cron every 30 minutes:
    */30 * * * * cd /home/jasonbohan2/kraken && /home/jasonbohan2/kraken/venv/bin/python bot_monitor_cron.py >> logs/monitor_cron.log 2>&1

What it does:
  1. Health check: restart any crashed bot screen sessions
  2. Learning: run tune_config() for bots with enough closed trades
  3. Protection: scan for unprotected positions and add OTO brackets
  4. Report: print summary (and optionally Telegram)
"""

import os
import subprocess
import time
from datetime import datetime

# Ensure we run from the project directory (needed for cron)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from kraken_connection import (
    get_balance, get_ticker, get_open_orders, place_order, get_min_order_info,
)
from learning_engine import LearningEngine

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

LEARNER = LearningEngine()

# Expected bot screen sessions and their launch commands
EXPECTED_BOTS = {
    "bot_BTC":     "python stock_swing_bot.py BTC >> logs/BTC_swing.log 2>&1",
    "bot_ETH":     "python stock_swing_bot.py ETH >> logs/ETH_swing.log 2>&1",
    "bot_SOL":     "python stock_swing_bot.py SOL >> logs/SOL_swing.log 2>&1",
    "bot_ADA":     "python stock_swing_bot.py ADA >> logs/ADA_swing.log 2>&1",
    "bot_DOT":     "python dot_swing_bot.py >> logs/DOT_swing.log 2>&1",
    "bot_DOGE":    "python doge_swing_bot.py --monitor >> logs/DOGE_swing.log 2>&1",
    "bot_BTC_MOM": "python btc_swing_bot.py >> logs/BTC_momentum.log 2>&1",
    "bot_HFT":     "python dynamic_hft_bot.py >> logs/HFT.log 2>&1",
}

# Bot name -> (learning engine bot_name, symbols it trades)
BOT_LEARNING = {
    "bot_BTC":     ("stock_swing_bot", ["BTC"]),
    "bot_ETH":     ("stock_swing_bot", ["ETH"]),
    "bot_SOL":     ("stock_swing_bot", ["SOL"]),
    "bot_ADA":     ("stock_swing_bot", ["ADA"]),
    "bot_HFT":     ("dynamic_hft_bot", None),  # None = check all symbols in DB
}

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

PROFIT_PCT = 0.08
STOP_PCT = 0.04
MIN_USD_VALUE = 0.50

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def tg(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=8,
        )
    except Exception:
        pass


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

    for name, cmd in EXPECTED_BOTS.items():
        if name in running:
            continue

        print(f"  [{ts()}] RESTART: {name} is not running — relaunching")
        subprocess.run(
            ["screen", "-dmS", name, "bash", "-c", cmd],
            cwd="/home/jasonbohan2/kraken",
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
    """Find unprotected holdings and add OTO brackets where possible."""
    protected_notes = []

    balances = get_balance()
    open_orders = get_open_orders()

    # Find pairs already protected
    protected_pairs = set()
    for order in open_orders.values():
        descr = order.get("descr", {})
        if descr.get("type") == "sell":
            protected_pairs.add(descr.get("pair", ""))

    for asset, bal_str in balances.items():
        volume = float(bal_str)
        if volume <= 0 or asset in SKIP_ASSETS:
            continue

        pair = ASSET_TO_PAIR.get(asset)
        if not pair or pair in protected_pairs:
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

        tp_price = price * (1 + PROFIT_PCT)
        sl_price = price * (1 - STOP_PCT)

        ok, result = place_order(
            pair=pair,
            side="sell",
            order_type="limit",
            volume=volume,
            price=tp_price,
            close_ordertype="stop-loss",
            close_price=sl_price,
        )

        if ok:
            note = f"  PROTECTED {asset} ({pair}): TP @ ${tp_price:.4f} | SL @ ${sl_price:.4f}"
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
    protected_notes = protect_positions()
    if not protected_notes:
        print(f"  All eligible positions protected")

    # 4. Summary
    print(f"\n  [Summary]")
    running = get_running_screens()
    print(f"  Bots running: {len(running)}/{len(EXPECTED_BOTS)}")

    # Telegram report
    if restarted or tuning_notes or protected_notes:
        lines = [f"*Bot Monitor — {ts()}*"]
        if restarted:
            lines.append(f"Restarted: {', '.join(restarted)}")
        for note in tuning_notes[:5]:
            lines.append(note.strip())
        for note in protected_notes[:5]:
            lines.append(note.strip())
        tg("\n".join(lines))

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
