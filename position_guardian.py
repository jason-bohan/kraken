#!/usr/bin/env python3
"""
Position Guardian — shared helpers for OCO exit orders.

Usage in any bot:

    from position_guardian import place_exit_oco, scan_and_protect

    # On startup — protect any existing holdings with no open sell orders:
    scan_and_protect(PAIRS_CONFIG, PROFIT_PCT, STOP_PCT)

    # After a successful buy — place OCO exit on Kraken's servers:
    oco_txid = place_exit_oco(pair, volume, entry_price, PROFIT_PCT, STOP_PCT)

    # In monitor loop — check if OCO already closed the position:
    if oco_txid and oco_txid not in get_open_orders():
        position = None   # Kraken handled the exit
        oco_txid = None
"""

from kraken_connection import (
    get_balance, get_ticker, get_open_orders, place_oco_order
)


def _fmt_price(price: float) -> str:
    """Format price with enough decimal places for any pair."""
    if price >= 1000:
        return f"{price:.2f}"
    elif price >= 1:
        return f"{price:.4f}"
    else:
        return f"{price:.6f}"


def place_exit_oco(
    pair: str,
    volume: float,
    entry_price: float,
    profit_pct: float,
    stop_pct: float,
) -> str | None:
    """
    Place an OCO sell order to exit a long position.

    price  = take-profit limit (entry * (1 + profit_pct))
    price2 = stop-loss trigger (entry * (1 - stop_pct))

    Returns the txid string on success, None on failure.
    """
    take_profit = entry_price * (1 + profit_pct)
    stop_loss   = entry_price * (1 - stop_pct)

    ok, result = place_oco_order(
        pair    = pair,
        side    = "sell",
        volume  = str(round(volume, 8)),
        price   = _fmt_price(take_profit),
        price2  = _fmt_price(stop_loss),
    )

    if ok:
        txids = result.get("txid", [])
        txid  = txids[0] if txids else None
        print(
            f"  OCO exit set for {pair}: "
            f"TP={_fmt_price(take_profit)} (+{profit_pct*100:.1f}%)  "
            f"SL={_fmt_price(stop_loss)} (-{stop_pct*100:.1f}%)"
            + (f"  [{txid}]" if txid else "")
        )
        return txid

    print(f"  WARNING: OCO failed for {pair} — bot will poll manually as fallback")
    return None


def scan_and_protect(
    pairs_config: dict,
    profit_pct: float,
    stop_pct: float,
    min_usd: float = 5.0,
) -> None:
    """
    On bot startup: find held coins that have no open sell orders and
    place protective OCO orders for them automatically.

    pairs_config format:
        {
            "SOLUSD": {"asset": "SOL",  "reserve": 0.05},
            "XBTUSD": {"asset": "XXBT", "reserve": 0.00001},
            "ETHUSD": {"asset": "XETH", "reserve": 0.005},
        }
    """
    print("  Scanning portfolio for unprotected positions...")

    try:
        balances    = get_balance()
        open_orders = get_open_orders()
    except Exception as e:
        print(f"  WARNING: scan_and_protect failed to fetch data: {e}")
        return

    # Build set of pairs that already have an open sell order
    protected_pairs = set()
    for order in open_orders.values():
        desc = order.get("descr", {})
        if desc.get("type") == "sell":
            protected_pairs.add(desc.get("pair", ""))

    for pair, cfg in pairs_config.items():
        asset   = cfg["asset"]
        reserve = cfg.get("reserve", 0)

        held = float(balances.get(asset, 0)) - reserve
        if held <= 0:
            continue

        try:
            ticker = get_ticker(pair)
            if not ticker:
                continue
            price = float(ticker.get("a", [0])[0])
        except Exception:
            continue

        if price * held < min_usd:
            continue  # too small to bother protecting

        if pair in protected_pairs:
            print(f"  {pair}: {held:.6g} {asset} already has open sell order — skipping")
            continue

        print(f"  {pair}: found {held:.6g} {asset} (~${price*held:.2f}) with no exit order")
        place_exit_oco(pair, held, price, profit_pct, stop_pct)
