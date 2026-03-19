#!/usr/bin/env python3
"""
telegram_bot.py — Telegram command handler for Kraken trading bots.

Listens for slash commands and responds with portfolio/bot info.

Commands:
    /portfolio  — Current holdings and unrealized P&L
    /pnl        — Realized P&L summary
    /bots       — Bot health status
    /orders     — Open orders on Kraken
    /balance    — USD balance

Run as a screen session:
    screen -dmS telegram_bot bash -c "python telegram_bot.py >> logs/telegram_bot.log 2>&1"
"""

import os
import time
import requests as req
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from kraken_connection import (
    get_balance, get_ticker, get_open_orders, get_trade_history,
)

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
API = f"https://api.telegram.org/bot{TOKEN}"

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

DISPLAY_NAMES = {
    "XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "XETHZ": "ETH",
    "XDG": "DOGE", "XXDG": "DOGE", "XZEC": "ZEC", "XZECZ": "ZEC",
}

SKIP = {"ZUSD", "USD", "USDC", "USDT", "KFEE", "NFTS"}


def send(text: str, chat_id: str = None):
    """Send a message to Telegram."""
    cid = chat_id or CHAT_ID
    try:
        res = req.post(f"{API}/sendMessage", json={
            "chat_id": cid,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)
        # If Markdown parsing fails, retry without formatting
        if not res.json().get("ok"):
            req.post(f"{API}/sendMessage", json={
                "chat_id": cid,
                "text": text,
            }, timeout=10)
    except Exception as e:
        print(f"  Send error: {e}")


def _get_avg_entry_prices() -> dict[str, float]:
    """Calculate average entry price per pair from trade history."""
    trades = get_trade_history(count=200)
    if not trades:
        return {}

    # Track cost basis per pair using FIFO
    pair_buys: dict[str, list] = {}  # pair -> [(vol, price), ...]
    avg_entries: dict[str, float] = {}

    # Sort oldest first
    sorted_trades = sorted(trades, key=lambda t: t.get("time", 0))

    for t in sorted_trades:
        pair = t.get("pair", "")
        side = t.get("type", "")
        vol = float(t.get("vol", 0))
        price = float(t.get("price", 0))

        if side == "buy":
            pair_buys.setdefault(pair, []).append((vol, price))
        elif side == "sell":
            # Remove sold volume from buys (FIFO)
            remaining = vol
            buys = pair_buys.get(pair, [])
            while remaining > 0 and buys:
                bvol, bprice = buys[0]
                if bvol <= remaining:
                    remaining -= bvol
                    buys.pop(0)
                else:
                    buys[0] = (bvol - remaining, bprice)
                    remaining = 0

    # Calculate weighted average entry for remaining holdings
    for pair, buys in pair_buys.items():
        total_vol = sum(v for v, p in buys)
        if total_vol > 0:
            avg_entries[pair] = sum(v * p for v, p in buys) / total_vol

    return avg_entries


def cmd_portfolio(chat_id: str):
    """Handle /portfolio command."""
    balances = get_balance()
    usd = float(balances.get("ZUSD", 0))
    avg_entries = _get_avg_entry_prices()

    lines = ["*Portfolio*\n"]
    total_value = usd
    total_cost = 0
    holdings = []

    for asset, bal_str in balances.items():
        vol = float(bal_str)
        if vol <= 0 or asset in SKIP:
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

        name = DISPLAY_NAMES.get(asset, asset)
        total_value += value

        entry = avg_entries.get(pair)
        if entry and entry > 0:
            pnl_pct = ((price - entry) / entry) * 100
            cost = vol * entry
            total_cost += cost
        else:
            pnl_pct = None
            cost = value  # assume breakeven if no history

        holdings.append((name, vol, price, value, pnl_pct))

    # Sort by value descending
    holdings.sort(key=lambda x: -x[3])

    for name, vol, price, value, pnl_pct in holdings:
        if pnl_pct is not None:
            emoji = "🟢" if pnl_pct >= 0 else "🔴"
            pnl_str = f" ({pnl_pct:+.1f}%)"
        else:
            emoji = "⚪"
            pnl_str = ""
        lines.append(f"{emoji} {name}: ${value:.2f}{pnl_str}")

    lines.append(f"\n💵 Cash: ${usd:.2f}")
    lines.append(f"💼 *Total: ${total_value:.2f}*")

    send("\n".join(lines), chat_id)


def cmd_pnl(chat_id: str):
    """Handle /pnl command."""
    trades = get_trade_history(count=50)
    if not trades:
        send("No trade history found", chat_id)
        return

    buys = [t for t in trades if t.get("type") == "buy"]
    sells = [t for t in trades if t.get("type") == "sell"]

    total_bought = sum(float(t.get("cost", 0)) for t in buys)
    total_sold = sum(float(t.get("cost", 0)) for t in sells)
    total_fees = sum(float(t.get("fee", 0)) for t in trades)
    net = total_sold - total_bought - total_fees

    lines = [
        "*P&L Summary (last 50 trades)*\n",
        f"Buys: ${total_bought:.2f} ({len(buys)} trades)",
        f"Sells: ${total_sold:.2f} ({len(sells)} trades)",
        f"Fees: ${total_fees:.2f}",
        f"Net: {'🟢' if net >= 0 else '🔴'} ${net:.2f}",
    ]

    send("\n".join(lines), chat_id)


def cmd_bots(chat_id: str):
    """Handle /bots command."""
    import subprocess

    try:
        out = subprocess.check_output(["screen", "-ls"], text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        out = e.output

    expected = [
        "bot_BTC", "bot_ETH", "bot_SOL", "bot_ADA", "bot_DOT",
        "bot_DOGE", "bot_BTC_MOM", "bot_HFT", "bot_MOMENTUM", "telegram_bot",
    ]
    lines = ["*Bot Status*\n"]

    for bot in expected:
        display = bot.replace("_", "\\_")  # escape underscores for Markdown
        if bot in out:
            lines.append(f"🟢 {display}: running")
        else:
            lines.append(f"🔴 {display}: DOWN")

    send("\n".join(lines), chat_id)


# Bot name -> (learning engine bot_name, symbols, description)
BOT_INFO = {
    "btc":      ("stock_swing_bot", ["BTC"], "Swing trades BTC"),
    "eth":      ("stock_swing_bot", ["ETH"], "Swing trades ETH"),
    "sol":      ("stock_swing_bot", ["SOL"], "Swing trades SOL"),
    "ada":      ("stock_swing_bot", ["ADA"], "Swing trades ADA"),
    "dot":      ("dot_swing_bot",   ["DOT"], "Swing trades DOT"),
    "doge":     ("doge_swing_bot",  ["DOGE"], "Swing trades DOGE"),
    "hft":      ("dynamic_hft_bot", None, "High-frequency scalper"),
    "momentum": ("momentum_scanner_bot", None, "Momentum breakout scanner"),
}


def cmd_botinfo(chat_id: str, bot_key: str):
    """Handle /info <bot> command — show bot stats and recent trades."""
    import sqlite3

    bot_key = bot_key.lower().strip()
    if bot_key not in BOT_INFO:
        available = ", ".join(sorted(BOT_INFO.keys()))
        send(f"Unknown bot: {bot_key}\nAvailable: {available}", chat_id)
        return

    bot_name, symbols, desc = BOT_INFO[bot_key]

    # Check if screen is running
    import subprocess
    try:
        screen_out = subprocess.check_output(["screen", "-ls"], text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        screen_out = e.output

    screen_name = f"bot_{bot_key.upper()}"
    if bot_key == "momentum":
        screen_name = "bot_MOMENTUM"
    elif bot_key == "hft":
        screen_name = "bot_HFT"
    elif bot_key == "btc":
        screen_name = "bot_BTC"
    # etc — check generically
    status = "🟢 Running" if screen_name in screen_out else "🔴 DOWN"

    lines = [f"*{screen_name}* — {desc}", f"Status: {status}\n"]

    # Pull stats from learning.db
    try:
        db = sqlite3.connect("learning.db")
        db.row_factory = sqlite3.Row

        if symbols is None:
            # Get all symbols this bot has traded
            rows = db.execute(
                "SELECT DISTINCT symbol FROM trade_log WHERE bot_name = ?",
                (bot_name,),
            ).fetchall()
            symbols = [r["symbol"] for r in rows]

        if not symbols:
            lines.append("No trade history yet")
        else:
            total_trades = 0
            total_wins = 0
            total_pnl = 0.0

            for symbol in symbols:
                rows = db.execute(
                    """SELECT pnl_pct, pnl_amount, exit_reason, entry_time, exit_time, entry_price, exit_price
                       FROM trade_log
                       WHERE bot_name = ? AND symbol = ? AND status = 'closed'
                       ORDER BY id DESC LIMIT 20""",
                    (bot_name, symbol),
                ).fetchall()

                if not rows:
                    continue

                wins = sum(1 for r in rows if (r["pnl_pct"] or 0) > 0)
                avg_pnl = sum(r["pnl_pct"] or 0 for r in rows) / len(rows)
                total_trades += len(rows)
                total_wins += wins
                total_pnl += sum(r["pnl_amount"] or 0 for r in rows)

                wr = wins / len(rows) * 100
                lines.append(f"*{symbol}*: {len(rows)} trades, {wr:.0f}% win, avg {avg_pnl*100:+.1f}%")

                # Show last 3 trades
                for r in rows[:3]:
                    pnl = (r["pnl_pct"] or 0) * 100
                    emoji = "🟢" if pnl > 0 else "🔴"
                    reason = r["exit_reason"] or "?"
                    lines.append(f"  {emoji} {pnl:+.1f}% ({reason})")

            if total_trades > 0:
                overall_wr = total_wins / total_trades * 100
                lines.append(f"\n*Overall*: {total_trades} trades, {overall_wr:.0f}% win, P&L ${total_pnl:+.2f}")

        # Check for open positions
        open_rows = db.execute(
            "SELECT symbol, entry_price, position_size, entry_time FROM trade_log WHERE bot_name = ? AND status = 'open'",
            (bot_name,),
        ).fetchall()

        if open_rows:
            lines.append("\n*Open positions:*")
            for r in open_rows:
                ticker = get_ticker(ASSET_TO_PAIR.get(r["symbol"], r["symbol"] + "USD"))
                if ticker:
                    cur = float(ticker.get("c", [0])[0])
                    pnl = ((cur - r["entry_price"]) / r["entry_price"]) * 100
                    lines.append(f"  {r['symbol']}: entry ${r['entry_price']:.4f} → ${cur:.4f} ({pnl:+.1f}%)")
                else:
                    lines.append(f"  {r['symbol']}: entry ${r['entry_price']:.4f}")

        db.close()
    except Exception as e:
        lines.append(f"DB error: {e}")

    send("\n".join(lines), chat_id)


def cmd_trades(chat_id: str):
    """Handle /trades command — recent trades across all bots."""
    import sqlite3

    try:
        db = sqlite3.connect("learning.db")
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT bot_name, symbol, side, entry_price, exit_price, pnl_pct, pnl_amount, exit_reason, exit_time
               FROM trade_log WHERE status = 'closed'
               ORDER BY id DESC LIMIT 15""",
        ).fetchall()
        db.close()
    except Exception as e:
        send(f"DB error: {e}", chat_id)
        return

    if not rows:
        send("No closed trades yet", chat_id)
        return

    lines = [f"*Recent Trades (last {len(rows)})*\n"]
    for r in rows:
        pnl = (r["pnl_pct"] or 0) * 100
        amt = r["pnl_amount"] or 0
        emoji = "🟢" if pnl > 0 else "🔴"
        bot = r["bot_name"].replace("_bot", "").replace("stock_swing", "swing")
        lines.append(f"{emoji} {r['symbol']} {pnl:+.1f}% (${amt:+.2f}) — {bot} ({r['exit_reason']})")

    send("\n".join(lines), chat_id)


def cmd_orders(chat_id: str):
    """Handle /orders command."""
    orders = get_open_orders()
    if not orders:
        send("No open orders", chat_id)
        return

    lines = [f"*Open Orders ({len(orders)})*\n"]

    for txid, order in list(orders.items())[:15]:
        descr = order.get("descr", {})
        pair = descr.get("pair", "?")
        side = descr.get("type", "?")
        otype = descr.get("ordertype", "?")
        price = descr.get("price", "?")
        vol = order.get("vol", "?")
        close = descr.get("close", "")

        emoji = "🟢" if side == "buy" else "🔴"
        line = f"{emoji} {side} {vol} {pair} @ {price} ({otype})"
        if close:
            line += f"\n   ↳ {close}"
        lines.append(line)

    if len(orders) > 15:
        lines.append(f"\n...and {len(orders) - 15} more")

    send("\n".join(lines), chat_id)


def cmd_balance(chat_id: str):
    """Handle /balance command."""
    balances = get_balance()
    usd = float(balances.get("ZUSD", 0))
    send(f"💵 USD Balance: *${usd:.2f}*", chat_id)


def cmd_help(chat_id: str):
    """Handle /help command."""
    send(
        "*Available Commands*\n\n"
        "/portfolio — Holdings & values\n"
        "/pnl — Realized P&L summary\n"
        "/bots — Bot health status\n"
        "/botlist — All bots & descriptions\n"
        "/orders — Open orders\n"
        "/balance — USD balance\n"
        "/gainers — Top 10 24h gainers\n"
        "/trades — Recent trades (all bots)\n"
        "/info btc — Bot details & stats\n"
        "  _bots: btc eth sol ada dot doge hft momentum_\n"
        "/help — This message",
        chat_id,
    )


def _get_all_usd_pairs() -> list[str]:
    """Fetch all online USD trading pairs from Kraken."""
    try:
        res = req.get("https://api.kraken.com/0/public/AssetPairs", timeout=15)
        pairs = res.json().get("result", {})
        return [k for k, v in pairs.items()
                if v.get("quote") in ("ZUSD", "USD") and v.get("status") == "online"]
    except Exception:
        return []


def _clean_pair_name(raw_pair: str) -> str:
    """Strip Kraken's internal pair name down to the coin ticker."""
    name = raw_pair
    for suffix in ("ZUSD", "USD"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    # Kraken prefixes: XXBT -> BTC, XETH -> ETH, XXDG -> DOGE
    RENAMES = {"XXBT": "BTC", "XETH": "ETH", "XETHZ": "ETH",
               "XXDG": "DOGE", "XDG": "DOGE", "XZEC": "ZEC"}
    if name in RENAMES:
        name = RENAMES[name]
    elif name.startswith("X") and len(name) == 4:
        name = name[1:]
    return name


def _fmt_price(price: float) -> str:
    if price < 0.001:
        return f"${price:.8f}"
    if price < 1:
        return f"${price:.5f}"
    if price < 100:
        return f"${price:.3f}"
    return f"${price:.2f}"


def cmd_gainers(chat_id: str):
    """Handle /gainers command — top 10 24h gainers across all Kraken USD pairs."""
    send("Scanning all Kraken USD pairs...", chat_id)

    all_pairs = _get_all_usd_pairs()
    if not all_pairs:
        send("Could not fetch pair list", chat_id)
        return

    # Fetch tickers in batches (URL length limit)
    data = {}
    batch_size = 80
    for i in range(0, len(all_pairs), batch_size):
        batch = all_pairs[i:i + batch_size]
        try:
            res = req.get(
                "https://api.kraken.com/0/public/Ticker",
                params={"pair": ",".join(batch)},
                timeout=15,
            )
            batch_data = res.json().get("result", {})
            data.update(batch_data)
        except Exception:
            continue

    # Filter: need 24h volume > $10k to avoid dead pairs
    MIN_VOL_USD = 10_000
    gainers = []
    for raw_pair, info in data.items():
        try:
            price = float(info["c"][0])
            open_24h = float(info["o"])
            if open_24h <= 0 or price <= 0:
                continue
            vol_24h = float(info["v"][1])  # 24h volume in base currency
            vol_usd = vol_24h * price
            if vol_usd < MIN_VOL_USD:
                continue
            change_pct = ((price - open_24h) / open_24h) * 100
            name = _clean_pair_name(raw_pair)
            gainers.append((name, price, change_pct, vol_usd))
        except (KeyError, ValueError, ZeroDivisionError):
            continue

    # Deduplicate (some coins have multiple pair names like XETH/ETH)
    seen = {}
    for name, price, pct, vol in gainers:
        if name not in seen or vol > seen[name][3]:
            seen[name] = (name, price, pct, vol)
    gainers = list(seen.values())

    gainers.sort(key=lambda x: -x[2])

    lines = [f"*Top 10 Gainers (24h)* ({len(gainers)} pairs scanned)\n"]
    for name, price, pct, vol in gainers[:10]:
        emoji = "🚀" if pct >= 5 else "🟢"
        lines.append(f"{emoji} *{name}*: {pct:+.1f}% @ {_fmt_price(price)}")

    lines.append(f"\n*Bottom 5*\n")
    for name, price, pct, vol in gainers[-5:]:
        emoji = "🔴" if pct <= -5 else "📉"
        lines.append(f"{emoji} *{name}*: {pct:+.1f}% @ {_fmt_price(price)}")

    send("\n".join(lines), chat_id)


def cmd_botlist(chat_id: str):
    """Handle /botlist command — list all bots with descriptions."""
    lines = ["*Available Bots*\n"]
    for key, (bot_name, symbols, desc) in sorted(BOT_INFO.items()):
        syms = ", ".join(symbols) if symbols else "multi"
        lines.append(f"• *{key}* — {desc} ({syms})")
    lines.append(f"\nUse `/info <name>` for details")
    send("\n".join(lines), chat_id)


COMMANDS = {
    "/portfolio": cmd_portfolio,
    "/pnl": cmd_pnl,
    "/bots": cmd_bots,
    "/botlist": cmd_botlist,
    "/orders": cmd_orders,
    "/balance": cmd_balance,
    "/gainers": cmd_gainers,
    "/trades": cmd_trades,
    "/help": cmd_help,
    "/start": cmd_help,
}

# Commands that take an argument
ARG_COMMANDS = {
    "/info": cmd_botinfo,
}


def poll():
    """Long-poll for Telegram updates and handle commands."""
    offset = 0
    print(f"[{datetime.now()}] Telegram bot started — listening for commands")

    while True:
        try:
            res = req.get(f"{API}/getUpdates", params={
                "offset": offset,
                "timeout": 30,
            }, timeout=35)

            data = res.json()
            if not data.get("ok"):
                print(f"  Telegram error: {data}")
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                # Only respond to our chat
                if chat_id != CHAT_ID:
                    continue

                # Extract command (handle /command@botname format)
                cmd = text.split("@")[0].split()[0].lower() if text else ""

                if cmd in COMMANDS:
                    print(f"  [{datetime.now()}] Command: {cmd}")
                    try:
                        COMMANDS[cmd](chat_id)
                    except Exception as e:
                        send(f"Error: {e}", chat_id)
                        print(f"  Command error: {e}")
                elif cmd in ARG_COMMANDS:
                    # Commands that take arguments: /info btc
                    parts = text.split(None, 2)
                    arg = parts[1] if len(parts) > 1 else ""
                    print(f"  [{datetime.now()}] Command: {cmd} {arg}")
                    try:
                        ARG_COMMANDS[cmd](chat_id, arg)
                    except Exception as e:
                        send(f"Error: {e}", chat_id)
                        print(f"  Command error: {e}")

        except req.exceptions.Timeout:
            continue
        except Exception as e:
            print(f"  Poll error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        print("TELEGRAM_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        exit(1)
    poll()
