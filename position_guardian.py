#!/usr/bin/env python3
"""
Position Guardian — server-side exit protection using standalone stop-loss orders.

Places a standalone stop-loss sell order on Kraken for downside protection.
Bots handle take-profit in their monitoring loops (market sell when TP reached,
cancel the SL first). The SL fires automatically on Kraken even if the bot crashes.

Previous approach (OTO brackets) was broken: the conditional SL only activates
AFTER the primary TP limit order fills — it does NOT fire concurrently. So when
price dropped below SL while TP was unfilled, the SL never triggered.

New approach:
  - place_exit_orders() places a standalone stop-loss sell order
  - Bot loop handles TP: detects price >= TP → cancel SL → market sell
  - If bot crashes: SL sits on Kraken, fires when price hits trigger

Usage in any bot:

    from position_guardian import place_exit_orders, scan_and_protect, check_exit_orders

    # After a successful buy — place SL on Kraken:
    exit_orders = place_exit_orders(pair, volume, entry_price, PROFIT_PCT, STOP_PCT)
    # exit_orders = {"tp": None, "sl": "TXID-SL"}

    # In the monitor loop — check if SL fired:
    if exit_orders:
        closed = check_exit_orders(exit_orders)
        if closed:
            position = None
            exit_orders = None

    # When bot detects TP in its loop:
    if pnl_pct >= PROFIT_PCT:
        cancel_remaining_exit(exit_orders)  # cancel the SL
        place_order(pair, "sell", "market", volume=volume)  # market sell

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
    Place a standalone stop-loss sell order on Kraken for downside protection.

    Bots handle take-profit in their monitoring loops. The SL fires automatically
    on Kraken even if the bot crashes.

    Returns dict: {"tp": None, "sl": txid_or_None, "tp_price": float, "sl_price": float}
    tp_price is stored so bots know the TP level for their loop checks.
    """
    tp_price = entry_price * (1 + profit_pct)
    sl_price = entry_price * (1 - stop_pct)
    sl_price_str = _fmt_price(sl_price, pair)
    vol_str      = str(round(volume, 8))

    ok, r = place_order(pair, "sell", "stop-loss", volume=volume, price=sl_price_str)

    if ok:
        txids = r.get("txid", [])
        sl_id = txids[0] if txids else None
        print(f"  SL protection set: {pair} sell {vol_str} | SL @ {sl_price_str} (-{stop_pct*100:.1f}%) | TP target @ {_fmt_price(tp_price, pair)} (+{profit_pct*100:.1f}%)  [{sl_id}]")
        return {"tp": None, "sl": sl_id, "tp_price": tp_price, "sl_price": sl_price}
    else:
        error = r.get("error", r) if isinstance(r, dict) else r
        print(f"  WARNING: SL protection failed for {pair}: {error}")
        return {"tp": None, "sl": None, "tp_price": tp_price, "sl_price": sl_price}


def check_exit_orders(exit_orders: dict) -> bool:
    """
    Check if the stop-loss has fired (position was closed by Kraken).

    Returns True if SL filled (no longer in open orders), False if still open.
    """
    if not exit_orders:
        return False

    sl_txid = exit_orders.get("sl")
    if not sl_txid:
        return False

    try:
        open_orders = get_open_orders()
    except Exception as e:
        print(f"  WARNING: could not check open orders: {e}")
        return False

    if sl_txid not in open_orders:
        print(f"  Stop-loss fired [{sl_txid}] — position closed by Kraken")
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
    place standalone stop-loss orders for downside protection.

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
