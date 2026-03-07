#!/usr/bin/env python3
"""
Position Guardian — server-side exit protection using Kraken's OTO bracket orders.

Places a limit take-profit as the primary order with a stop-loss attached as a
conditional close. Kraken manages both server-side — they fire even if the bot
crashes. Kraken shows these as OTO (One-Triggers-Other) in the UI.

Behavior:
  TP fills         → conditional SL is void (position already closed)
  TP cancelled     → SL activates as a live order
  Price hits SL    → Kraken fires the SL and cancels the TP

Only the TP order reserves coins — no 'Insufficient funds' errors.

Usage in any bot:

    from position_guardian import place_exit_orders, scan_and_protect, check_exit_orders

    # After a successful buy — place bracket TP+SL on Kraken:
    exit_orders = place_exit_orders(pair, volume, entry_price, PROFIT_PCT, STOP_PCT)
    # exit_orders = {"tp": "TXID-TP", "sl": None}
    # Note: sl txid is None because the SL is a conditional close, not a separate order.
    # Kraken manages the SL internally — it fires if TP is cancelled or price hits trigger.

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
    get_balance, get_ticker, get_open_orders, cancel_order, place_order, place_bracket_order, get_asset_pairs
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
    Place a Kraken OTO bracket: limit TP with conditional close SL.

    A single order reserves the coins once — avoids 'Insufficient funds' from
    two separate sell orders. Kraken manages the SL as a conditional close
    attached to the primary TP limit order.

    Returns dict: {"tp": txid_or_None, "sl": txid_or_None}
    The SL txid may mirror the TP txid since Kraken tracks the conditional
    close internally rather than as a separate order.
    """
    tp_price_str = _fmt_price(entry_price * (1 + profit_pct), pair)
    sl_price_str = _fmt_price(entry_price * (1 - stop_pct),  pair)
    vol_str      = str(round(volume, 8))

    ok, r = place_bracket_order(
        pair=pair,
        side="sell",
        volume=vol_str,
        price=tp_price_str,   # take-profit limit (primary order)
        price2=sl_price_str,  # stop-loss trigger (conditional close)
    )

    if ok:
        txids = r.get("txid", [])
        result = {
            "tp": txids[0] if len(txids) > 0 else None,
            "sl": txids[1] if len(txids) > 1 else txids[0] if txids else None,
        }
        print(f"  Bracket exit set: {pair} sell {vol_str} | TP @ {tp_price_str} (+{profit_pct*100:.1f}%) | SL @ {sl_price_str} (-{stop_pct*100:.1f}%)  {txids}")
        return result
    else:
        error = r.get("error", r) if isinstance(r, dict) else r
        print(f"  WARNING: bracket exit failed for {pair}: {error}")
        # Fallback: place standalone SL to at least protect the position
        ok_sl, r_sl = place_order(pair, "sell", "stop-loss", volume=volume, price=sl_price_str)
        if ok_sl:
            txids = r_sl.get("txid", [])
            sl_id = txids[0] if txids else None
            print(f"  Fallback SL set: {pair} sell {vol_str} @ {sl_price_str} (-{stop_pct*100:.1f}%)  [{sl_id}]")
            return {"tp": None, "sl": sl_id}
        print(f"  WARNING: fallback SL also failed for {pair}: {r_sl.get('error', r_sl)}")
        return {"tp": None, "sl": None}


def check_exit_orders(exit_orders: dict) -> bool:
    """
    Check if the bracket exit has closed the position.

    With Kraken's OTO model the conditional close (SL) is managed internally,
    so normally only the TP txid appears in open orders. If the TP disappears,
    either it filled (SL is void) or Kraken fired the SL (TP was cancelled).

    Returns True if position was closed, False if TP is still open.
    """
    if not exit_orders or (not exit_orders.get("tp") and not exit_orders.get("sl")):
        return False

    try:
        open_orders = get_open_orders()
    except Exception as e:
        print(f"  WARNING: could not check open orders: {e}")
        return False

    tp_txid = exit_orders.get("tp")
    sl_txid = exit_orders.get("sl")

    tp_open = tp_txid in open_orders if tp_txid else False
    sl_open = sl_txid in open_orders if sl_txid else False

    # Take-profit filled — cancel the stop-loss
    if tp_txid and not tp_open and sl_txid and sl_open:
        print(f"  Take-profit filled [{tp_txid}] — cancelling stop-loss [{sl_txid}]")
        cancel_order(sl_txid)
        return True

    # Stop-loss filled — cancel the take-profit
    if sl_txid and not sl_open and tp_txid and tp_open:
        print(f"  Stop-loss fired [{sl_txid}] — cancelling take-profit [{tp_txid}]")
        cancel_order(tp_txid)
        return True

    # Both gone (edge case: both filled or both cancelled)
    if tp_txid and not tp_open and sl_txid and not sl_open:
        print(f"  Both exit orders gone for position — marking closed")
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
    place protective OTO bracket orders (TP + conditional SL) automatically.

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
