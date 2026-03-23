#!/usr/bin/env python3
"""
Momentum Scanner Bot — Kraken
Scans all liquid USD pairs for momentum breakouts with volume confirmation.

Strategy:
- Scans ~300 USD pairs every 5 minutes
- Buys when price is up 3-10% in 1-2 hours with volume spike
- OTO bracket: +10% TP / -5% SL
- Max 3 positions, $15 per trade

Usage:
    python3 momentum_scanner_bot.py           # live
    python3 momentum_scanner_bot.py --dry     # dry run
"""

import os
import sys
import time
import signal
import argparse
import requests as req
from datetime import datetime, timezone
from collections import defaultdict

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from kraken_connection import (
    get_balance, get_asset_pairs, get_open_orders, get_ticker,
    place_order, calculate_order_size, get_min_order_info, cancel_order,
)
from position_guardian import _fmt_price
from market_sentiment import should_buy as check_sentiment, format_sentiment

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
SCAN_INTERVAL = 300          # 5 minutes between scans
MAX_POSITIONS = 3            # max concurrent momentum trades
MAX_TRADE_USD = 15.0         # per trade
USERREF = 12                 # portfolio analyzer tag
RESERVE_USD = 5.0            # keep in reserve

# Entry signals
MIN_GAIN_1H = 0.03           # 3% min gain in ~1 hour
MAX_GAIN_1H = 0.10           # 10% max (not chasing)
MAX_GAIN_24H = 0.20          # skip if >20% in 24h (too late)
MIN_VOLUME_24H_USD = 100_000 # minimum liquidity
MIN_TRADE_RATIO = 1.5        # today's trade rate vs 24h avg

# Exit: standalone SL on Kraken + bot loop handles TP
PROFIT_PCT = 0.10            # +10% take profit (bot loop market sell)
STOP_PCT = 0.08              # -8% stop loss — backtested optimal (wider avoids noise stopouts)

# Skip stablecoins, wrapped tokens, leveraged
SKIP_BASES = {
    "USDT", "USDC", "DAI", "PYUSD", "TUSD", "UST", "USDP", "GUSD",
    "WBTC", "WETH", "STETH", "CBETH", "RETH",
}

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
# {pair: [(timestamp, price, trades_today, trades_24h), ...]}
price_history: dict[str, list] = defaultdict(list)

# {pair: {entry_price, volume, entry_time, bracket_txid}}
positions: dict[str, dict] = {}

shutdown = False


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def tg(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=8,
        )
    except Exception:
        pass


def clean_name(raw_pair: str) -> str:
    """Strip Kraken pair to readable coin name."""
    name = raw_pair
    for suffix in ("ZUSD", "USD"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    renames = {"XXBT": "BTC", "XETH": "ETH", "XETHZ": "ETH",
               "XXDG": "DOGE", "XDG": "DOGE", "XZEC": "ZEC"}
    if name in renames:
        return renames[name]
    if name.startswith("X") and len(name) == 4:
        return name[1:]
    return name


def sig_handler(signum, frame):
    global shutdown
    print(f"\n[{ts()}] Shutting down...")
    shutdown = True


# ─────────────────────────────────────────────
# PAIR DISCOVERY
# ─────────────────────────────────────────────
def discover_liquid_pairs() -> list[tuple[str, str]]:
    """Return [(pair_key, base_asset), ...] for all online USD pairs."""
    all_pairs = get_asset_pairs()
    result = []
    for key, info in all_pairs.items():
        if info.get("status") != "online":
            continue
        quote = info.get("quote", "")
        if quote not in ("ZUSD", "USD"):
            continue
        base = info.get("base", "")
        # Skip stablecoins and wrapped
        base_clean = base.lstrip("XZ")
        if base in SKIP_BASES or base_clean in SKIP_BASES:
            continue
        result.append((key, base))
    return result


# ─────────────────────────────────────────────
# BATCH TICKER FETCH
# ─────────────────────────────────────────────
def batch_fetch_tickers(pair_keys: list[str]) -> dict:
    """Fetch tickers for many pairs in batches. Returns {pair: ticker_data}."""
    all_tickers = {}
    batch_size = 80
    for i in range(0, len(pair_keys), batch_size):
        batch = pair_keys[i:i + batch_size]
        try:
            res = req.get(
                "https://api.kraken.com/0/public/Ticker",
                params={"pair": ",".join(batch)},
                timeout=15,
            )
            body = res.json()
            if not body.get("error"):
                all_tickers.update(body.get("result", {}))
        except Exception as e:
            print(f"  [{ts()}] Batch ticker error: {e}")
        if i + batch_size < len(pair_keys):
            time.sleep(1)
    return all_tickers


# ─────────────────────────────────────────────
# PRICE HISTORY
# ─────────────────────────────────────────────
def update_history(tickers: dict):
    """Append current snapshot to price history, prune old entries."""
    now = time.time()
    cutoff = now - 9000  # keep 2.5 hours

    for pair, info in tickers.items():
        try:
            price = float(info["c"][0])
            trades_today = int(info["t"][0])
            trades_24h = int(info["t"][1])
            price_history[pair].append((now, price, trades_today, trades_24h))
            # Prune old
            price_history[pair] = [
                e for e in price_history[pair] if e[0] > cutoff
            ]
        except (KeyError, ValueError):
            continue


# ─────────────────────────────────────────────
# SIGNAL DETECTION
# ─────────────────────────────────────────────
def check_signal(pair: str, ticker: dict) -> dict | None:
    """Check if pair has a momentum entry signal. Returns signal dict or None."""
    history = price_history.get(pair, [])
    if len(history) < 12:  # need ~1 hour of data
        return None

    try:
        price = float(ticker["c"][0])
        open_24h = float(ticker["o"])
        if open_24h <= 0 or price <= 0:
            return None

        # 24h change — skip if too late
        change_24h = (price - open_24h) / open_24h
        if change_24h > MAX_GAIN_24H:
            return None

        # Volume filter
        vol_24h = float(ticker["v"][1]) * price
        if vol_24h < MIN_VOLUME_24H_USD:
            return None

        # 1-hour gain from our history
        price_1h_ago = history[-12][1]  # ~1 hour ago (12 * 5min)
        if price_1h_ago <= 0:
            return None
        gain_1h = (price - price_1h_ago) / price_1h_ago

        # 2-hour gain if we have enough data
        if len(history) >= 24:
            price_2h_ago = history[-24][1]
            gain_2h = (price - price_2h_ago) / price_2h_ago
        else:
            gain_2h = gain_1h

        best_gain = max(gain_1h, gain_2h)
        if best_gain < MIN_GAIN_1H or best_gain > MAX_GAIN_1H:
            return None

        # Trade activity spike — is trading rate elevated today?
        trades_today = int(ticker["t"][0])
        trades_24h = int(ticker["t"][1])
        now_utc = datetime.now(timezone.utc)
        hours_today = now_utc.hour + now_utc.minute / 60
        if hours_today < 0.5:
            hours_today = 0.5  # avoid division issues near midnight
        expected = trades_24h * (hours_today / 24)
        trade_ratio = trades_today / max(expected, 1)

        if trade_ratio < MIN_TRADE_RATIO:
            return None

        name = clean_name(pair)
        return {
            "pair": pair,
            "name": name,
            "price": price,
            "gain_1h": gain_1h,
            "change_24h": change_24h,
            "vol_24h_usd": vol_24h,
            "trade_ratio": trade_ratio,
            "reason": f"+{best_gain*100:.1f}% in 1h, vol ${vol_24h/1000:.0f}k, activity {trade_ratio:.1f}x",
        }

    except (KeyError, ValueError, ZeroDivisionError):
        return None


# ─────────────────────────────────────────────
# POSITION MANAGEMENT
# ─────────────────────────────────────────────
def reconstruct_positions():
    """On startup, check for existing momentum positions (userref=12 SL orders)."""
    orders = get_open_orders()

    for txid, order in orders.items():
        descr = order.get("descr", {})
        if order.get("userref") != USERREF and str(order.get("userref")) != str(USERREF):
            continue
        if descr.get("type") != "sell":
            continue
        if descr.get("ordertype") != "stop-loss":
            continue

        pair = descr.get("pair", "")
        if pair and pair not in positions:
            sl_price = float(descr.get("price", 0))
            if sl_price > 0:
                # Estimate entry from SL price
                entry_est = sl_price / (1 - STOP_PCT)
                vol = float(order.get("vol", 0))
                positions[pair] = {
                    "entry_price": entry_est,
                    "volume": vol,
                    "entry_time": "recovered",
                    "sl_txid": txid,
                }
                print(f"  [{ts()}] Recovered position: {pair} ~${entry_est:.4f} vol={vol} SL={txid}")


def check_positions():
    """Check if SL fired or TP reached for momentum positions."""
    if not positions:
        return

    orders = get_open_orders()
    order_txids = set(orders.keys())
    closed = []

    for pair, pos in list(positions.items()):
        name = clean_name(pair)
        entry = pos["entry_price"]
        volume = pos["volume"]
        sl_txid = pos.get("sl_txid")

        # Check if SL fired (txid no longer in open orders)
        if sl_txid and sl_txid != "dry" and sl_txid not in order_txids:
            pnl_pct = -STOP_PCT  # approximate
            print(f"  [{ts()}] SL fired: {name} ({pair}) ~{pnl_pct*100:.1f}%")
            tg(f"🛑 *Momentum SL*: {name} ~{pnl_pct*100:.1f}% (entry ${entry:.4f})")
            closed.append(pair)
            continue

        # Check if TP reached (bot loop handles TP via market sell)
        ticker = get_ticker(pair)
        if not ticker:
            continue
        price = float(ticker.get("c", [0])[0])
        pnl_pct = (price - entry) / entry if entry else 0

        if pnl_pct >= PROFIT_PCT:
            print(f"  [{ts()}] TP reached: {name} +{pnl_pct*100:.1f}% — selling @ ${price:.4f}")
            # Cancel SL before selling
            if sl_txid and sl_txid != "dry" and sl_txid in order_txids:
                cancel_order(sl_txid)
                time.sleep(0.5)
            ok, result = place_order(pair, "sell", "market", volume=volume, userref=USERREF)
            if ok:
                pnl_usd = (price - entry) * volume
                tg(f"💰 *Momentum TP*: {name} +{pnl_pct*100:.1f}% (${pnl_usd:+.2f})")
                closed.append(pair)
            else:
                print(f"  [{ts()}] TP sell failed for {name}: {result}")

    for pair in closed:
        del positions[pair]


def execute_buy(signal: dict, dry_run: bool) -> bool:
    """Execute a momentum buy and place OTO bracket."""
    pair = signal["pair"]
    price = signal["price"]
    name = signal["name"]

    # Check balance
    balances = get_balance()
    usd = float(balances.get("ZUSD", 0))
    available = max(0, usd - RESERVE_USD)
    spend = min(available, MAX_TRADE_USD)
    # Apply sentiment size multiplier
    sentiment = check_sentiment()
    spend = round(spend * sentiment["size_multiplier"], 2)

    if spend < 1.0:
        print(f"  [{ts()}] Not enough USD (${usd:.2f}) to buy {name}")
        return False

    # Calculate order size
    order_info = calculate_order_size(pair, price, spend)
    if not order_info.get("can_afford"):
        print(f"  [{ts()}] Can't afford minimum for {name}")
        return False

    volume = order_info["volume"]
    cost = order_info["cost"]

    if dry_run:
        print(f"  [{ts()}] [DRY] BUY {volume} {name} @ ${price:.4f} (~${cost:.2f})")
        print(f"  [{ts()}] [DRY] Would place SL -{STOP_PCT*100:.0f}% | TP +{PROFIT_PCT*100:.0f}% (bot loop)")
        positions[pair] = {
            "entry_price": price,
            "volume": volume,
            "entry_time": ts(),
            "sl_txid": "dry",
        }
        return True

    # Market buy
    print(f"  [{ts()}] BUY {volume} {name} @ ~${price:.4f} (~${cost:.2f})")
    ok, result = place_order(pair, "buy", "market", volume=volume, cost=cost, userref=USERREF)
    if not ok:
        print(f"  [{ts()}] Buy failed: {result}")
        return False

    buy_txid = result.get("txid", [])
    print(f"  [{ts()}] Bought: {buy_txid}")

    # Small delay for balance to settle
    time.sleep(2)

    # Place standalone stop-loss for protection
    sl_price = price * (1 - STOP_PCT)
    sl_price_str = _fmt_price(sl_price, pair)
    tp_price = price * (1 + PROFIT_PCT)

    ok2, result2 = place_order(
        pair=pair,
        side="sell",
        order_type="stop-loss",
        volume=volume,
        price=sl_price_str,
        userref=USERREF,
    )

    sl_txid = None
    if ok2:
        sl_txid = result2.get("txid", [""])[0] if result2.get("txid") else None
        print(f"  [{ts()}] SL placed @ ${sl_price_str} | TP target ${tp_price:.4f} (bot loop)")
    else:
        print(f"  [{ts()}] WARNING: SL failed: {result2}")
        print(f"  [{ts()}] Position is UNPROTECTED — cron monitor will catch it")

    positions[pair] = {
        "entry_price": price,
        "volume": volume,
        "entry_time": ts(),
        "sl_txid": sl_txid,
    }

    tg(
        f"🚀 *Momentum BUY*: {name}\n"
        f"Price: ${price:.4f} (~${cost:.2f})\n"
        f"Signal: {signal['reason']}\n"
        f"SL: ${sl_price:.4f} (-{STOP_PCT*100:.0f}%) | TP target: ${tp_price:.4f} (+{PROFIT_PCT*100:.0f}%)"
    )

    return True


# ─────────────────────────────────────────────
# HELD ASSETS (avoid double-buying)
# ─────────────────────────────────────────────
def get_held_bases(balances: dict) -> set[str]:
    """Return set of base assets we already hold (from any bot)."""
    held = set()
    for asset, bal_str in balances.items():
        if float(bal_str) > 0 and asset not in {"ZUSD", "USD", "USDC", "USDT", "KFEE", "NFTS"}:
            held.add(asset)
    return held


def pair_base_is_held(pair: str, held_bases: set[str], pair_info: dict) -> bool:
    """Check if the base asset of a pair is already held."""
    base = pair_info.get("base", "")
    if base in held_bases:
        return True
    # Also check without X/Z prefix
    base_clean = base.lstrip("XZ")
    if base_clean in held_bases:
        return True
    return False


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def run(dry_run: bool = False):
    global shutdown

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    mode = "DRY RUN" if dry_run else "LIVE"
    print("=" * 60)
    print(f"  Momentum Scanner Bot — {mode}")
    print(f"  Max positions: {MAX_POSITIONS} @ ${MAX_TRADE_USD}/trade")
    print(f"  Entry: +{MIN_GAIN_1H*100:.0f}% to +{MAX_GAIN_1H*100:.0f}% in 1h, activity {MIN_TRADE_RATIO}x+")
    print(f"  Exit: TP +{PROFIT_PCT*100:.0f}% / SL -{STOP_PCT*100:.0f}%")
    print(f"  Scan interval: {SCAN_INTERVAL}s")
    print("=" * 60)

    tg(f"🔍 *Momentum Scanner started* ({mode})\nMax {MAX_POSITIONS} positions @ ${MAX_TRADE_USD}")

    # Recover any existing positions from open orders
    if not dry_run:
        reconstruct_positions()

    cycle = 0

    while not shutdown:
        try:
            cycle += 1
            print(f"\n[{ts()}] Scan #{cycle}")

            # 1. Discover all liquid USD pairs
            liquid_pairs = discover_liquid_pairs()
            pair_keys = [p[0] for p in liquid_pairs]
            pair_bases = {p[0]: p[1] for p in liquid_pairs}
            print(f"  {len(liquid_pairs)} USD pairs found")

            # 2. Batch fetch tickers
            tickers = batch_fetch_tickers(pair_keys)
            print(f"  {len(tickers)} tickers fetched")

            # 3. Update price history
            update_history(tickers)

            # Count how many pairs have enough history
            ready = sum(1 for p in tickers if len(price_history.get(p, [])) >= 12)
            if ready == 0:
                elapsed = cycle * SCAN_INTERVAL / 60
                remaining = max(0, 60 - elapsed)
                print(f"  Warmup: {cycle}/12 cycles (~{remaining:.0f}min until signals)")

            # 4. Check existing positions
            check_positions()

            # 5. Scan for new signals if we have room
            sentiment = check_sentiment()
            print(f"  {format_sentiment()}")
            if len(positions) < MAX_POSITIONS and ready > 0 and sentiment["allow"]:
                balances = get_balance()
                held_bases = get_held_bases(balances)

                signals = []
                for pair in tickers:
                    # Skip if base asset already held
                    base = pair_bases.get(pair, "")
                    if base in held_bases or base.lstrip("XZ") in held_bases:
                        continue
                    # Skip if already in momentum positions
                    if pair in positions:
                        continue

                    sig = check_signal(pair, tickers[pair])
                    if sig:
                        signals.append(sig)

                if signals:
                    # Sort by trade activity ratio (strongest volume spike first)
                    signals.sort(key=lambda s: s["trade_ratio"], reverse=True)
                    print(f"  {len(signals)} signals found:")
                    for s in signals[:5]:
                        print(f"    {s['name']}: {s['reason']}")

                    # Execute top signals
                    for sig in signals:
                        if len(positions) >= MAX_POSITIONS:
                            break
                        execute_buy(sig, dry_run)
                else:
                    if ready > 0:
                        print(f"  No signals ({ready} pairs ready)")
            elif not sentiment["allow"]:
                print(f"  🚫 Buys blocked: {sentiment['reason']}")
            elif len(positions) >= MAX_POSITIONS:
                print(f"  At max positions ({MAX_POSITIONS}/{MAX_POSITIONS})")

            # 6. Position summary
            if positions:
                print(f"  Positions ({len(positions)}/{MAX_POSITIONS}):")
                for pair, pos in positions.items():
                    name = clean_name(pair)
                    ticker = tickers.get(pair)
                    if ticker:
                        cur = float(ticker["c"][0])
                        pnl = ((cur - pos["entry_price"]) / pos["entry_price"]) * 100
                        print(f"    {name}: entry ${pos['entry_price']:.4f} → ${cur:.4f} ({pnl:+.1f}%)")
                    else:
                        print(f"    {name}: entry ${pos['entry_price']:.4f}")

        except Exception as e:
            print(f"  [{ts()}] Error: {e}")
            import traceback
            traceback.print_exc()

        # Sleep in small increments so we can catch shutdown signal
        for _ in range(SCAN_INTERVAL):
            if shutdown:
                break
            time.sleep(1)

    print(f"[{ts()}] Momentum Scanner stopped")
    tg("🔍 *Momentum Scanner stopped*")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry)
