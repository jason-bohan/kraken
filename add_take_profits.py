#!/usr/bin/env python3
"""
add_take_profits.py — One-shot: place TP+SL orders for all unprotected holdings.

For each asset balance with no open sell orders, places a limit take-profit
with a stop-loss attached as a conditional close, based on current market price.

Usage:
    python add_take_profits.py --dry     # preview only
    python add_take_profits.py           # live
"""

import argparse
from collections import defaultdict
from kraken_connection import get_balance, get_ticker, get_open_orders, place_order

STOP_PCT   = 0.04   # stop-loss % below current price
PROFIT_PCT = 0.08   # take-profit % above current price
USERREF    = 10

# Minimum holding value in USD to bother protecting
MIN_USD_VALUE = 0.10

# Kraken asset name -> trading pair
ASSET_TO_PAIR = {
    "RIVER":  "RIVERUSD",
    "WAR":    "WARUSD",
    "HYPE":   "HYPEUSD",
    "SUI":    "SUIUSD",
    "PUMP":   "PUMPUSD",
    "ICP":    "ICPUSD",
    "ZEC":    "ZECUSD",
    "XZEC":   "ZECUSD",
    "DOT":    "DOTUSD",
    "PLUME":  "PLUMEUSD",
    "ZRO":    "ZROUSD",
    "TAO":    "TAOUSD",
    "SOL":    "SOLUSD",
    "ADA":    "ADAUSD",
    "DOGE":   "XDGUSD",
    "XDG":    "XDGUSD",
    "XXDG":   "XDGUSD",
    "ETH":    "ETHUSD",
    "XETH":   "ETHUSD",
    "BTC":    "XBTUSD",
    "XXBT":   "XBTUSD",
    "XBT":    "XBTUSD",
    "AVAX":   "AVAXUSD",
    "IDOS":   "IDOSUSD",
    "LINK":   "LINKUSD",
    "XPL":    "XPLUSD",
}

# Per-pair max decimal places Kraken allows on price
PAIR_DECIMALS = {
    "RIVERUSD": 3,
    "HYPEUSD":  2,
    "SUIUSD":   4,
    "ICPUSD":   3,
    "ZROUSD":   3,
    "WARUSD":   5,
    "PUMPUSD":  5,
    "ZECUSD":   2,
    "DOTUSD":   4,
    "PLUMEUSD": 5,
    "TAOUSD":   2,
    "SOLUSD":   2,
    "ADAUSD":   5,
    "XDGUSD":   6,
    "ETHUSD":   2,
    "XBTUSD":   1,
    "AVAXUSD":  2,
    "IDOSUSD":  5,
    "LINKUSD":  3,
    "XPLUSD":   4,
}

# Skip these (quote currencies, staking tokens, etc.)
SKIP_ASSETS = {"ZUSD", "USD", "USDC", "USDT", "KFEE", "NFTS"}


def rp(pair: str, price: float) -> float:
    return round(price, PAIR_DECIMALS.get(pair, 4))


def fmt(price: float) -> str:
    if price < 0.001:
        return f"{price:.8f}"
    if price < 1:
        return f"{price:.5f}"
    if price < 100:
        return f"{price:.4f}"
    return f"{price:.2f}"


def run(dry: bool):
    print("Fetching balances...")
    balances = get_balance()

    print("Fetching open orders...")
    open_orders = get_open_orders()

    # Find pairs that already have any open sell order
    protected_pairs: set[str] = set()
    for txid, order in open_orders.items():
        descr = order.get("descr", {})
        if descr.get("type") == "sell":
            protected_pairs.add(descr.get("pair", ""))

    print(f"Already protected pairs: {protected_pairs}\n")

    placed = 0
    skipped = 0
    failed = 0

    for asset, balance_str in balances.items():
        volume = float(balance_str)
        if volume <= 0:
            continue
        if asset in SKIP_ASSETS:
            continue

        pair = ASSET_TO_PAIR.get(asset)
        if not pair:
            print(f"  {asset}: no known pair mapping — skipping")
            skipped += 1
            continue

        if pair in protected_pairs:
            print(f"  {asset} ({pair}): already has open sell order — skipping")
            skipped += 1
            continue

        # Get current price
        ticker = get_ticker(pair)
        if not ticker:
            print(f"  {asset} ({pair}): could not fetch price — skipping")
            skipped += 1
            continue

        current_price = float(ticker.get("c", [0])[0])
        if current_price <= 0:
            print(f"  {asset}: price is 0 — skipping")
            skipped += 1
            continue

        usd_value = volume * current_price
        if usd_value < MIN_USD_VALUE:
            print(f"  {asset}: value ${usd_value:.2f} below minimum — skipping")
            skipped += 1
            continue

        tp_price = rp(pair, current_price * (1 + PROFIT_PCT))
        sl_price = rp(pair, current_price * (1 - STOP_PCT))

        print(f"  {asset} ({pair}):")
        print(f"    Current price:  {fmt(current_price)}")
        print(f"    Take-profit:    {fmt(tp_price)}  (+{PROFIT_PCT*100:.0f}%)")
        print(f"    Stop-loss:      {fmt(sl_price)}  (-{STOP_PCT*100:.0f}%)")
        print(f"    Volume:         {volume}")
        print(f"    Value:          ${usd_value:.2f}")

        if dry:
            print(f"    [DRY] Would place limit sell {volume} @ {fmt(tp_price)} with SL @ {fmt(sl_price)}")
        else:
            ok, result = place_order(
                pair=pair,
                side="sell",
                order_type="limit",
                volume=volume,
                price=tp_price,
                userref=USERREF,
                close_ordertype="stop-loss",
                close_price=sl_price,
            )
            if ok:
                txid = result.get("txid", [])
                print(f"    ✅ TP+SL placed: {txid}")
                placed += 1
            else:
                err = result.get("error", result)
                print(f"    ❌ Failed: {err}")
                failed += 1

        print()

    print(f"Done. Placed: {placed} | Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    run(dry=args.dry)
