#!/usr/bin/env python3
"""
One-shot script: scans your current Kraken balance and places
stop-loss + take-profit exit orders for any holdings that don't
already have open sell orders.

Usage:
    python3 protect_now.py              # live
    python3 protect_now.py --dry        # dry run (validate only, no orders placed)

Adjust PROFIT_PCT and STOP_PCT below if you want different targets.
"""

import sys
from kraken_connection import get_balance, get_ticker, get_open_orders, get_asset_pairs
from position_guardian import place_exit_orders

PROFIT_PCT = 0.08   # 8% take profit
STOP_PCT   = 0.04   # 4% stop loss
MIN_USD    = 5.0    # ignore holdings worth less than this

DRY_RUN = "--dry" in sys.argv

# Kraken internal asset name → (pair, display name)
# Covers the most common assets; extend as needed
ASSET_PAIR_MAP = {
    "XXBT":  ("XBTUSD",  "BTC"),
    "XETH":  ("ETHUSD",  "ETH"),
    "SOL":   ("SOLUSD",  "SOL"),
    "XXRP":  ("XRPUSD",  "XRP"),
    "ADA":   ("ADAUSD",  "ADA"),
    "XDOGE": ("XDGUSD",  "DOGE"),
    "DOT":   ("DOTUSD",  "DOT"),
    "LINK":  ("LINKUSD", "LINK"),
    "AVAX":  ("AVAXUSD", "AVAX"),
    "MATIC": ("MATICUSD","MATIC"),
    "ATOM":  ("ATOMUSD", "ATOM"),
    "LTC":   ("XLTCZUSD","LTC"),
    "UNI":   ("UNIUSD",  "UNI"),
    "AAVE":  ("AAVEUSD", "AAVE"),
    # From current portfolio
    "WAR":   ("WARUSD",  "WAR"),
    "XPL":   ("XPLUSD",  "XPL"),
    "BTCZ":  ("BTCZUSD", "BTCZ"),
    "SOXS":  ("SOXSUSD", "SOXS"),
}

print("=" * 55)
print(f"  Protect Now  —  {'DRY RUN' if DRY_RUN else 'LIVE'}")
print(f"  Target: +{PROFIT_PCT*100:.0f}%  |  Stop: -{STOP_PCT*100:.0f}%")
print("=" * 55)

balances    = get_balance()
open_orders = get_open_orders()

# Build set of pairs that already have open sell orders
protected = set()
for order in open_orders.values():
    desc = order.get("descr", {})
    if desc.get("type") == "sell":
        protected.add(desc.get("pair", ""))

print(f"\n  Open sell orders already covering: {protected or 'none'}\n")

found_any = False

for asset_key, (pair, name) in ASSET_PAIR_MAP.items():
    held = float(balances.get(asset_key, 0))
    if held <= 0:
        continue

    try:
        ticker = get_ticker(pair)
        if not ticker:
            print(f"  {name}: could not fetch price — skipping")
            continue
        price = float(ticker.get("a", [0])[0])
    except Exception as e:
        print(f"  {name}: price error ({e}) — skipping")
        continue

    usd_value = held * price
    if usd_value < MIN_USD:
        print(f"  {name}: {held:.6g} (${usd_value:.2f}) — too small, skipping")
        continue

    found_any = True

    if pair in protected:
        print(f"  {name}: {held:.6g} @ ${price:.4f} (~${usd_value:.2f}) — already protected")
        continue

    print(f"  {name}: {held:.6g} @ ${price:.4f} (~${usd_value:.2f}) — placing exit orders...")

    if DRY_RUN:
        tp = price * (1 + PROFIT_PCT)
        sl = price * (1 - STOP_PCT)
        print(f"    [DRY] Would set TP={tp:.4f} (+{PROFIT_PCT*100:.0f}%) / SL={sl:.4f} (-{STOP_PCT*100:.0f}%)")
    else:
        result = place_exit_orders(pair, held, price, PROFIT_PCT, STOP_PCT)
        if result.get("tp") or result.get("sl"):
            print(f"    Orders placed: TP={result.get('tp')}  SL={result.get('sl')}")
        else:
            print(f"    WARNING: both orders failed for {name}")

if not found_any:
    print("  No holdings found above minimum threshold.")

print("\nDone.")
