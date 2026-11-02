#!/usr/bin/env python3
"""
SOL Grid Bot — Kraken
Places a grid of limit buy orders below current SOL price.
Profits from every bounce between grid levels regardless of direction.

Strategy:
  - Place N buy limit orders below current price, $GRID_SPACING apart
  - When a buy fills → place a sell at buy_price + GRID_SPACING
  - When a sell fills → place a buy at sell_price - GRID_SPACING
  - Capture the spread on every bounce

Usage:
    python3 sol_grid_bot.py           # live
    python3 sol_grid_bot.py --dry     # simulate fills without real orders
"""

import os
import time
import json
import argparse
from datetime import datetime
from pathlib import Path

from kraken_connection import (
    get_balance, get_ticker, place_order,
    get_open_orders, cancel_order, calculate_order_size,
)
from learning_engine import LearningEngine
import requests as req

BOT_NAME  = "sol_grid_bot"
PAIR      = "SOLUSD"
LEARNER   = LearningEngine()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# GRID SETTINGS
# ─────────────────────────────────────────────
GRID_LEVELS    = 4      # buy orders to place initially
GRID_SPACING   = 3.0    # $ between grid levels
USD_PER_LEVEL  = 10.0   # $ per buy order
GRID_LOWER     = 60.0   # never place buys below this
GRID_UPPER     = 100.0  # never place sells above this
CHECK_SECS     = 30     # how often to poll for fills
USERREF        = 9      # unique tag to identify our grid orders on Kraken

STATE_FILE = Path(__file__).parent / "sol_grid_state.json"


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
            timeout=8,
        )
    except Exception:
        pass


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ─────────────────────────────────────────────
# STATE  (persisted to disk so restarts are safe)
# ─────────────────────────────────────────────
def load_state() -> dict:
    """Load grid state from disk."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"orders": {}, "total_profit": 0.0, "fills": 0}


def save_state(state: dict):
    """Persist grid state to disk."""
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def get_sol_price() -> float:
    ticker = get_ticker(PAIR)
    if not ticker:
        return 0.0
    return float(ticker["c"][0])


def round_price(price: float) -> float:
    """Round to 2 decimal places for SOL price levels."""
    return round(price, 2)


def place_limit(side: str, price: float, volume: float, dry_run: bool) -> str | None:
    """Place a limit order. Returns order ID or None on failure."""
    price = round_price(price)
    volume = round(volume, 4)
    if dry_run:
        fake_id = f"DRY-{side.upper()}-{price:.2f}-{int(time.time())}"
        print(f"  [DRY] Would place {side} {volume} SOL @ ${price:.2f}")
        return fake_id
    ok, result = place_order(PAIR, side, "limit", volume, price=price, userref=USERREF)
    if ok:
        txid = result["txid"][0]
        return txid
    print(f"  ❌ Failed to place {side} @ ${price:.2f}: {result}")
    return None


def get_our_open_orders(dry_run: bool, state: dict) -> set:
    """Return set of our open order IDs currently on Kraken."""
    if dry_run:
        return set(state["orders"].keys())
    orders = get_open_orders()
    return {
        oid for oid, o in orders.items()
        if o.get("userref") == USERREF
    }


# ─────────────────────────────────────────────
# GRID INITIALISATION
# ─────────────────────────────────────────────
def place_initial_grid(price: float, dry_run: bool, state: dict):
    """Place GRID_LEVELS buy orders below current price."""
    print(f"\n  [{ts()}] 📐 Setting up grid around ${price:.2f}")
    print(f"  Grid spacing: ${GRID_SPACING} | Levels: {GRID_LEVELS} | ${USD_PER_LEVEL}/level")

    placed = 0
    for i in range(1, GRID_LEVELS + 1):
        buy_price = round_price(price - i * GRID_SPACING)
        if buy_price < GRID_LOWER:
            print(f"  Skipping level {i} — ${buy_price:.2f} below floor ${GRID_LOWER}")
            continue

        volume = round(USD_PER_LEVEL / buy_price, 4)
        if volume <= 0:
            continue

        oid = place_limit("buy", buy_price, volume, dry_run)
        if oid:
            state["orders"][oid] = {
                "side":   "buy",
                "price":  buy_price,
                "volume": volume,
            }
            print(f"  ✅ Buy order #{i}: {volume:.4f} SOL @ ${buy_price:.2f} (${volume*buy_price:.2f})")
            placed += 1

    save_state(state)
    print(f"  [{ts()}] Grid live — {placed} buy orders placed")
    tg(f"📐 *SOL Grid Bot started* — {placed} buy orders around ${price:.2f}\nSpacing: ${GRID_SPACING} | ${USD_PER_LEVEL}/level")


# ─────────────────────────────────────────────
# FILL DETECTION + RESPONSE
# ─────────────────────────────────────────────
def check_fills(dry_run: bool, state: dict, sim_price: float = 0.0):
    """Detect filled orders and place counter-orders."""
    open_ids = get_our_open_orders(dry_run, state)
    known_ids = set(state["orders"].keys())
    filled_ids = known_ids - open_ids

    if not filled_ids:
        return

    price_now = sim_price if dry_run else get_sol_price()

    for oid in filled_ids:
        order = state["orders"].pop(oid)
        side   = order["side"]
        price  = order["price"]
        volume = order["volume"]
        pnl    = 0.0

        if side == "buy":
            # Buy filled → place sell one level above
            sell_price = round_price(price + GRID_SPACING)
            if sell_price > GRID_UPPER:
                print(f"  [{ts()}] Buy @ ${price:.2f} filled — sell ${sell_price:.2f} above ceiling, skipping")
                save_state(state)
                continue

            print(f"  [{ts()}] ✅ Buy filled @ ${price:.2f} → placing sell @ ${sell_price:.2f}")
            LEARNER.record_entry(
                bot_name=BOT_NAME, symbol=PAIR, side="buy",
                entry_price=price, position_size=volume,
                confidence=1.0,
                features={"grid_level": price, "grid_spacing": GRID_SPACING},
                config={"usd_per_level": USD_PER_LEVEL, "grid_spacing": GRID_SPACING},
            )
            new_oid = place_limit("sell", sell_price, volume, dry_run)
            if new_oid:
                state["orders"][new_oid] = {"side": "sell", "price": sell_price, "volume": volume, "buy_price": price}
                tg(f"✅ *SOL Grid* buy filled @ ${price:.2f} → sell placed @ ${sell_price:.2f}")

        elif side == "sell":
            # Sell filled → record profit, place buy one level below
            buy_price_orig = order.get("buy_price", price - GRID_SPACING)
            pnl = (price - buy_price_orig) * volume
            state["total_profit"] = round(state["total_profit"] + pnl, 4)
            state["fills"] += 1

            print(f"  [{ts()}] 💰 Sell filled @ ${price:.2f} | Grid profit: +${pnl:.3f} | Total: +${state['total_profit']:.3f}")
            LEARNER.record_entry(
                bot_name=BOT_NAME, symbol=PAIR, side="sell",
                entry_price=price, position_size=volume,
                confidence=1.0,
                features={"grid_level": price, "pnl": pnl},
                config={"usd_per_level": USD_PER_LEVEL, "grid_spacing": GRID_SPACING},
            )

            rebuy_price = round_price(price - GRID_SPACING)
            if rebuy_price < GRID_LOWER:
                print(f"  Rebuy ${rebuy_price:.2f} below floor — not replacing")
                save_state(state)
                continue

            rebuy_volume = round(USD_PER_LEVEL / rebuy_price, 4)
            new_oid = place_limit("buy", rebuy_price, rebuy_volume, dry_run)
            if new_oid:
                state["orders"][new_oid] = {"side": "buy", "price": rebuy_price, "volume": rebuy_volume}
                tg(f"💰 *SOL Grid* sell filled @ ${price:.2f} +${pnl:.3f} → rebuy @ ${rebuy_price:.2f}")

        save_state(state)


# ─────────────────────────────────────────────
# DRY RUN SIMULATOR
# ─────────────────────────────────────────────
def simulate_fills(state: dict, current_price: float) -> float:
    """Simulate fills in dry run by checking if price crossed any order."""
    for oid, order in list(state["orders"].items()):
        if order["side"] == "buy" and current_price <= order["price"]:
            state["orders"].pop(oid)
            return order["price"]
        if order["side"] == "sell" and current_price >= order["price"]:
            state["orders"].pop(oid)
            return order["price"]
    return 0.0


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run(dry_run: bool = False):
    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    print("=" * 55)
    print(f"  SOL Grid Bot — Kraken  {mode}")
    print(f"  Pair          : {PAIR}")
    print(f"  Grid levels   : {GRID_LEVELS}")
    print(f"  Grid spacing  : ${GRID_SPACING}")
    print(f"  Per level     : ${USD_PER_LEVEL}")
    print(f"  Range         : ${GRID_LOWER} — ${GRID_UPPER}")
    print("=" * 55)

    state = load_state()

    # Determine starting price
    price = get_sol_price()
    if price <= 0:
        print("  ❌ Could not fetch SOL price, exiting")
        return

    print(f"  [{ts()}] SOL price: ${price:.2f}")

    # Place initial grid if no orders exist yet
    if not state["orders"]:
        place_initial_grid(price, dry_run, state)
    else:
        print(f"  [{ts()}] Resuming grid — {len(state['orders'])} orders tracked | Total profit: ${state['total_profit']:.3f}")

    # Main loop
    cycle = 0
    while True:
        try:
            time.sleep(CHECK_SECS)
            cycle += 1
            price = get_sol_price()
            if price <= 0:
                continue

            if dry_run:
                simulate_fills(state, price)

            check_fills(dry_run, state, sim_price=price)

            open_count  = len([o for o in state["orders"].values() if o["side"] == "buy"])
            sell_count  = len([o for o in state["orders"].values() if o["side"] == "sell"])
            print(f"  [{ts()}] SOL ${price:.2f} | Grid: {open_count} buys / {sell_count} sells | "
                  f"Profit: +${state['total_profit']:.3f} ({state['fills']} fills)")

            # Refill grid if all buy orders have been consumed and price dropped below all levels
            buy_prices = [o["price"] for o in state["orders"].values() if o["side"] == "buy"]
            if not buy_prices or (buy_prices and price < min(buy_prices) - GRID_SPACING * 2):
                print(f"  [{ts()}] 🔄 Grid drifted — re-anchoring around ${price:.2f}")
                place_initial_grid(price, dry_run, state)

        except KeyboardInterrupt:
            print(f"\n\n  [{ts()}] 👋 Shutting down grid bot")
            print(f"  Total grid profit: +${state['total_profit']:.3f} across {state['fills']} fills")
            tg(f"🛑 *SOL Grid Bot stopped* | Profit: +${state['total_profit']:.3f} | Fills: {state['fills']}")
            break
        except Exception as e:
            print(f"  [{ts()}] ⚠️ Error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Dry run — no real orders")
    args = parser.parse_args()
    run(dry_run=args.dry)
