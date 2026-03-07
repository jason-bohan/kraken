#!/usr/bin/env python3
"""
Position Guardian — server-side exit orders using Kraken's native order types.

Kraken supports two relevant native order types:
  stop-loss   : market sell triggers when price DROPS below `price`
  take-profit : market sell triggers when price RISES above `price`

These live on Kraken's servers — they fire even if the bot crashes.

Usage in any bot:

    from position_guardian import place_exit_orders, scan_and_protect, check_exit_orders

    # After a successful buy — place both orders on Kraken:
    exit_orders = place_exit_orders(pair, volume, entry_price, PROFIT_PCT, STOP_PCT)
    # exit_orders = {"tp": "TXID-TP", "sl": "TXID-SL"}  (None values if order failed)

    # In the monitor loop — check if Kraken already closed the position:
    if exit_orders:
        closed = check_exit_orders(exit_orders)
        if closed:
            position = None
            exit_orders = None

    # On bot startup — protect unprotected holdings automatically:
    scan_and_protect(
        {PAIR: {"asset": ASSET, "reserve": RESERVE}},
        PROFIT_PCT, STOP_PCT
    )
"""

from kraken_connection import (
    get_balance, get_ticker, get_open_orders, cancel_order, place_order, get_asset_pairs
)

_pair_decimals_cache: dict = {}

def _get_pair_decimals(pair: str) -> int:
    """Return the number of price decimal places Kraken requires for this pair."""
    if pair not in _pair_decimals_cache:
        try:
            pairs = get_asset_pairs()
            for key, info in pairs.items():
                _pair_decimals_cache[key] = int(info.get("pair_decimals", 2))
        except Exception:
            pass
    return _pair_decimals_cache.get(pair, 2)


def _fmt_price(price: float, pair: str) -> str:
    """Format price to Kraken's required decimal precision for this pair."""
    decimals = _get_pair_decimals(pair)
    return f"{price:.{decimals}f}"


def place_exit_orders(
    pair: str,
    volume: float,
    entry_price: float,
    profit_pct: float,
    stop_pct: float,
) -> dict:
    """
    Place an exchange-hosted stop-loss and let the bot handle take-profit logic.

    Kraken spot support for attached closes is reliable for stop protection, but this
    helper should not pretend both exits are simultaneously live as a native OCO pair.

    Returns dict: {"tp": None, "sl": txid_or_None}
    """
    tp_price_str = _fmt_price(entry_price * (1 + profit_pct), pair)
    sl_price_str = _fmt_price(entry_price * (1 - stop_pct), pair)
    vol_str = str(round(volume, 8))

    ok_sl, r_sl = place_order(pair, "sell", "stop-loss", volume=volume, price=sl_price_str)
    if ok_sl:
        txids = r_sl.get("txid", [])
        sl_id = txids[0] if txids else None
        print(f"  Exit protection set: {pair} sell {vol_str} | TP handled by bot @ {tp_price_str} | SL @ {sl_price_str} (-{stop_pct*100:.1f}%)  [{sl_id}]")
        return {"tp": None, "sl": sl_id}

    print(f"  WARNING: stop-loss protection failed for {pair}: {r_sl.get('error', r_sl)}")
    return {"tp": None, "sl": None}

def check_exit_orders(exit_orders: dict) -> bool:
    """
    Check open orders to see if the exchange-hosted stop-loss fired.

    Returns True if the stop-loss is no longer open, which implies the position was
    likely closed or the order was manually removed and needs attention.
    """
    if not exit_orders or not exit_orders.get("sl"):
        return False

    try:
        open_orders = get_open_orders()
    except Exception as e:
        print(f"  WARNING: could not check open orders: {e}")
        return False

    sl_txid = exit_orders.get("sl")
    sl_open = sl_txid in open_orders if sl_txid else False

    if sl_txid and not sl_open:
        print(f"  Stop-loss no longer open [{sl_txid}] — marking position closed")
        return True

    return False

def cancel_remaining_exit(exit_orders: dict) -> None:
    """Cancel whichever exit orders are still open (call when manually closing a position)."""
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
    place protective stop-loss orders automatically and keep take-profit logic in the bot.

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
        print(f"  WARNING: scan_and_protect failed: {e}")
        return

    # Pairs that already have an open sell order
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

        print(f"  {pair}: found {held:.6g} {asset} (~${price*held:.2f}) with no exit orders — protecting")
        place_exit_orders(pair, held, price, profit_pct, stop_pct)

