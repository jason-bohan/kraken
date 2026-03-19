#!/usr/bin/env python3
"""
backfill_learning.py — One-shot: backfill learning.db with closed trades from Kraken history.

Matches buys to sells (FIFO) to create round-trip trades with real P&L.
Clears existing trade_log entries first to avoid duplicates.

Usage:
    python backfill_learning.py --dry    # preview
    python backfill_learning.py          # live
"""

import os
import sqlite3
import argparse
from datetime import datetime, timezone
from collections import defaultdict

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from kraken_connection import get_trade_history, query_orders

# Map Kraken pair names to clean symbols
PAIR_TO_SYMBOL = {
    "XXBTZUSD": "BTC", "XETHZUSD": "ETH", "XZECZUSD": "ZEC",
    "XXRPZUSD": "XRP", "XDGUSD": "DOGE",
    "SOLUSD": "SOL", "ADAUSD": "ADA", "DOTUSD": "DOT",
    "AVAXUSD": "AVAX", "LINKUSD": "LINK", "SUIUSD": "SUI",
    "TAOUSD": "TAO", "NEARUSD": "NEAR", "ICPUSD": "ICP",
    "RENDERUSD": "RENDER", "HBARUSD": "HBAR", "HYPEUSD": "HYPE",
    "TRUMPUSD": "TRUMP", "ZROUSD": "ZRO", "XPLUSD": "XPL",
    "PEPEUSD": "PEPE", "PLUMEUSD": "PLUME", "PUMPUSD": "PUMP",
    "FARTCOINUSD": "FARTCOIN", "NIGHTUSD": "NIGHT", "XCNUSD": "XCN",
    "RIVERUSD": "RIVER", "WARUSD": "WAR", "IDOSUSD": "IDOS",
    "ALCXUSD": "ALCX", "FETUSD": "FET", "SENTUSD": "SENT",
    "PENGUUSD": "PENGU", "WIFUSD": "WIF", "KASUSD": "KAS",
    "USELESSUSD": "USELESS", "BABYUSD": "BABY",
}

# Userref -> bot name mapping
BOT_USERREFS = {
    1: "stock_swing_bot",
    2: "btc_swing_bot",
    3: "eth_swing_bot",
    4: "sol_swing_bot",
    5: "dot_swing_bot",
    6: "doge_swing_bot",
    7: "btc_momentum_bot",
    8: "dynamic_hft_bot",
    9: "universal_bull_bot",
    10: "add_take_profits",
    11: "correlation_bot",
    12: "momentum_scanner_bot",
}


def get_all_trades():
    """Fetch all trades from Kraken."""
    all_trades = []
    for ofs in range(0, 2000, 50):
        trades = get_trade_history(count=50, ofs=ofs)
        if not trades:
            break
        all_trades.extend(trades)
        if len(trades) < 50:
            break
    return sorted(all_trades, key=lambda x: x.get("time", 0))


def identify_bot(trade, userref_cache):
    """Try to identify which bot made this trade."""
    ordertxid = trade.get("ordertxid", "")
    userref = userref_cache.get(ordertxid, 0)
    if userref and userref in BOT_USERREFS:
        return BOT_USERREFS[userref]

    # Heuristic based on pair and order type
    pair = trade.get("pair", "")
    order_type = trade.get("order_type", "")

    # HFT bot uses market orders on many pairs
    if order_type == "market":
        return "dynamic_hft_bot"

    return "unknown"


def match_trades(all_trades, userref_cache):
    """Match buys to sells FIFO to create round-trip trades."""
    # Group by pair
    pair_buys = defaultdict(list)  # pair -> [(trade, remaining_vol), ...]
    round_trips = []

    for t in all_trades:
        pair = t.get("pair", "")
        side = t.get("type", "")
        vol = float(t.get("vol", 0))
        price = float(t.get("price", 0))
        tm = t.get("time", 0)

        if side == "buy":
            bot = identify_bot(t, userref_cache)
            pair_buys[pair].append({
                "vol_remaining": vol,
                "vol_original": vol,
                "price": price,
                "time": tm,
                "bot": bot,
                "cost": float(t.get("cost", 0)),
                "fee": float(t.get("fee", 0)),
            })

        elif side == "sell":
            # Match against oldest buys (FIFO)
            remaining_sell = vol
            buys = pair_buys.get(pair, [])

            while remaining_sell > 0.000001 and buys:
                buy = buys[0]
                match_vol = min(remaining_sell, buy["vol_remaining"])

                if match_vol > 0.000001:
                    entry_price = buy["price"]
                    exit_price = price
                    pnl_pct = (exit_price - entry_price) / entry_price
                    pnl_amount = match_vol * (exit_price - entry_price)

                    symbol = PAIR_TO_SYMBOL.get(pair, pair.replace("USD", ""))
                    bot = buy["bot"]

                    round_trips.append({
                        "bot_name": bot,
                        "symbol": symbol,
                        "side": "buy",
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "position_size": match_vol,
                        "pnl_pct": pnl_pct,
                        "pnl_amount": pnl_amount,
                        "entry_time": datetime.fromtimestamp(buy["time"], tz=timezone.utc).isoformat(),
                        "exit_time": datetime.fromtimestamp(tm, tz=timezone.utc).isoformat(),
                        "exit_reason": "take_profit" if pnl_pct > 0 else "stop_loss",
                    })

                    buy["vol_remaining"] -= match_vol
                    remaining_sell -= match_vol

                if buy["vol_remaining"] < 0.000001:
                    buys.pop(0)

    return round_trips


def run(dry: bool):
    print("Fetching all trades from Kraken...")
    all_trades = get_all_trades()
    print(f"  {len(all_trades)} trades fetched")

    # Build userref cache
    print("Looking up order userrefs...")
    ordertxids = list(set(t.get("ordertxid", "") for t in all_trades if t.get("ordertxid")))
    userref_cache = {}
    # Batch in groups of 20 (Kraken limit)
    for i in range(0, len(ordertxids), 20):
        batch = ordertxids[i:i + 20]
        try:
            result = query_orders(batch)
            for txid, info in result.items():
                ref = info.get("userref", 0)
                if ref:
                    userref_cache[txid] = ref
        except Exception as e:
            print(f"  Warning: query_orders batch failed: {e}")
    print(f"  {len(userref_cache)} orders with userrefs")

    # Match buys to sells
    print("Matching buy/sell pairs (FIFO)...")
    round_trips = match_trades(all_trades, userref_cache)
    print(f"  {len(round_trips)} round-trip trades matched")

    # Stats
    wins = sum(1 for rt in round_trips if rt["pnl_pct"] > 0)
    losses = len(round_trips) - wins
    total_pnl = sum(rt["pnl_amount"] for rt in round_trips)
    avg_pnl = sum(rt["pnl_pct"] for rt in round_trips) / len(round_trips) * 100 if round_trips else 0

    print(f"\n  Wins: {wins} | Losses: {losses} | Win rate: {wins/len(round_trips)*100:.0f}%")
    print(f"  Total P&L: ${total_pnl:.2f} | Avg: {avg_pnl:+.2f}%")

    # Show by bot
    bot_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for rt in round_trips:
        b = bot_stats[rt["bot_name"]]
        b["count"] += 1
        if rt["pnl_pct"] > 0:
            b["wins"] += 1
        b["pnl"] += rt["pnl_amount"]

    print("\n  By bot:")
    for bot, s in sorted(bot_stats.items(), key=lambda x: -x[1]["count"]):
        wr = s["wins"] / s["count"] * 100 if s["count"] else 0
        print(f"    {bot}: {s['count']} trades, {wr:.0f}% win, ${s['pnl']:+.2f}")

    if dry:
        print("\n[DRY RUN] Would insert these into learning.db")
        print("Top 10 trades:")
        for rt in sorted(round_trips, key=lambda x: -abs(x["pnl_amount"]))[:10]:
            print(f"  {rt['symbol']} {rt['pnl_pct']*100:+.1f}% (${rt['pnl_amount']:+.2f}) [{rt['bot_name']}] {rt['exit_reason']}")
        return

    # Write to DB
    print("\nWriting to learning.db...")
    db = sqlite3.connect("learning.db")

    # Clear existing trades (they're all stale 'open' entries)
    existing = db.execute("SELECT COUNT(*) FROM trade_log").fetchone()[0]
    print(f"  Clearing {existing} existing entries...")
    db.execute("DELETE FROM trade_log")

    # Insert round trips as closed trades
    for rt in round_trips:
        db.execute("""
            INSERT INTO trade_log
            (bot_name, symbol, side, status, entry_time, exit_time,
             entry_price, exit_price, position_size, confidence,
             pnl_amount, pnl_pct, exit_reason, features_json, config_json)
            VALUES (?, ?, ?, 'closed', ?, ?, ?, ?, ?, 0.5, ?, ?, ?, '{}', '{}')
        """, (
            rt["bot_name"], rt["symbol"], rt["side"],
            rt["entry_time"], rt["exit_time"],
            rt["entry_price"], rt["exit_price"], rt["position_size"],
            rt["pnl_amount"], rt["pnl_pct"], rt["exit_reason"],
        ))

    db.commit()
    final_count = db.execute("SELECT COUNT(*) FROM trade_log WHERE status = 'closed'").fetchone()[0]
    print(f"  Inserted {final_count} closed trades")
    db.close()
    print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    run(dry=args.dry)
