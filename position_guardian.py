#!/usr/bin/env python3
"""
Position Guardian — server-side exit protection using standalone stop-loss orders.

Places a standalone stop-loss sell order on Kraken for downside protection.
Bots handle take-profit in their monitoring loops (market sell when TP reached,
cancel the SL first). The SL fires automatically on Kraken even if the bot crashes.

Why not an OTO bracket (limit TP with a conditional-close SL)? On Kraken the
conditional close only activates AFTER the primary order fills, so the SL never
protects the position while the TP is waiting.

Usage in any bot:

    from position_guardian import place_exit_orders, scan_and_protect, check_exit_orders

    # After a successful buy — place SL on Kraken:
    exit_orders = place_exit_orders(pair, volume, entry_price, PROFIT_PCT, STOP_PCT)
    # exit_orders = {"tp": None, "sl": "TXID-SL", "tp_price": ..., "sl_price": ...}

    # In the monitor loop — check if SL fired:
    if exit_orders and check_exit_orders(exit_orders):
        position = None
        exit_orders = None

    # When the bot detects TP in its loop:
    if pnl_pct >= PROFIT_PCT:
        cancel_remaining_exit(exit_orders)  # cancel the SL
        place_order(pair, "sell", "market", volume=volume)

    # On bot startup — protect unprotected holdings automatically:
    scan_and_protect({PAIR: {"asset": ASSET, "reserve": RESERVE}}, PROFIT_PCT, STOP_PCT)
"""

from kraken_connection import (
    get_balance, get_ticker, get_open_orders, cancel_order, place_order,
    get_order_status, format_price, below_minimum,
)


def _fmt_price(price: float, pair: str) -> str:
    """Format price to Kraken's required precision for this pair (kept for existing callers)."""
    return format_price(pair, price)


def place_exit_orders(
    pair: str,
    volume: float,
    entry_price: float,
    profit_pct: float,
    stop_pct: float,
    userref: int = 0,
) -> dict:
    """
    Place a standalone stop-loss sell order on Kraken for downside protection.
    userref tags the order with the owning bot's id.

    Returns dict: {"tp": None, "sl": txid_or_None, "tp_price": float, "sl_price": float}
    tp_price is stored so bots know the TP level for their loop checks.
    If the volume is below Kraken's minimum, no order is placed and the result
    also contains "below_minimum": True.
    """
    tp_price = entry_price * (1 + profit_pct)
    sl_price = entry_price * (1 - stop_pct)
    sl_price_str = format_price(pair, sl_price)
    result = {"tp": None, "sl": None, "tp_price": tp_price, "sl_price": sl_price}

    reason = below_minimum(pair, volume, sl_price)
    if reason:
        print(f"  WARNING: no SL for {pair}: {reason}")
        result["below_minimum"] = True
        return result

    ok, r = place_order(pair, "sell", "stop-loss", volume=volume, price=sl_price_str, userref=userref)
    if ok:
        txids = r.get("txid", [])
        result["sl"] = txids[0] if txids else None
        print(f"  SL protection set: {pair} sell {volume:.8g} | SL @ {sl_price_str} (-{stop_pct*100:.1f}%) "
              f"| TP target @ {format_price(pair, tp_price)} (+{profit_pct*100:.1f}%)  [{result['sl']}]")
    else:
        error = r.get("error", r) if isinstance(r, dict) else r
        print(f"  WARNING: SL protection failed for {pair}: {error}")
    return result


def check_exit_orders(exit_orders: dict) -> bool:
    """
    Return True only if the stop-loss order actually FILLED on Kraken.

    - "closed"            → filled, position is gone → True
    - "canceled"/"expired"→ the position is still held but no longer protected:
                            clears exit_orders["sl"] and returns False
    - open / unknown      → False (an API failure is never treated as a fill)
    """
    if not exit_orders:
        return False
    sl_txid = exit_orders.get("sl")
    if not sl_txid:
        return False

    status = get_order_status(sl_txid)
    if status == "closed":
        print(f"  Stop-loss fired [{sl_txid}] — position closed by Kraken")
        return True
    if status in ("canceled", "expired"):
        print(f"  WARNING: stop-loss [{sl_txid}] was {status} without filling — position is unprotected")
        exit_orders["sl"] = None
    return False


def cancel_remaining_exit(exit_orders: dict) -> None:
    """
    Cancel whichever exit orders are still open (call before manually closing a position).
    Raises KrakenAPIError if open orders can't be fetched, so the caller doesn't
    try to sell coins that are still reserved by the stop-loss.
    """
    if not exit_orders:
        return
    open_orders = get_open_orders()
    for key in ("tp", "sl"):
        txid = exit_orders.get(key)
        if txid and txid in open_orders:
            cancel_order(txid)


def scan_and_protect(
    pairs_config: dict,
    profit_pct: float,
    stop_pct: float,
    min_usd: float = 5.0,
) -> None:
    """
    On bot startup: find held coins with no open sell orders and
    place standalone stop-loss orders for downside protection.

    pairs_config format:
        {
            "SOLUSD": {"asset": "SOL",  "reserve": 0.05},
            "XBTUSD": {"asset": "XXBT", "reserve": 0.00001},
        }
    """
    print("  Scanning portfolio for unprotected positions...")

    try:
        balances    = get_balance()
        open_orders = get_open_orders()
    except Exception as e:
        print(f"  WARNING: scan_and_protect failed: {e}")
        return

    # Pairs that already have an open sell order (SL or limit)
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
            continue

        if pair in protected_pairs:
            print(f"  {pair}: already has open sell order — skipping")
            continue

        print(f"  {pair}: found {held:.6g} {asset} (~${price*held:.2f}) with no SL — protecting")
        place_exit_orders(pair, held, price, profit_pct, stop_pct)
