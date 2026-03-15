#!/usr/bin/env python3
"""
kraken_connection.py — Kraken API auth + helpers
Mirrors the structure of kalshi_connection.py for easy swapping.

Auth method: HMAC-SHA512
Signature = base64( HMAC-SHA512( uri_path + SHA256(nonce + post_data), base64decode(api_secret) ) )

To rediscover available fields on any response, add temporarily:
    print(f"Keys: {list(response.json().keys())}")
    print(f"Raw: {response.json()}")

.env keys needed:
    KRAKEN_API_KEY=your_public_api_key
    KRAKEN_API_SECRET=your_private_api_secret
"""

import os
import time
import hmac
import base64
import hashlib
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.kraken.com"
_PUBLIC_TTL_SECS = 300
_PRIVATE_TTL_SECS = 5
_asset_pairs_cache = {"ts": 0.0, "data": {}}
_balance_cache = {"ts": 0.0, "data": {}}

_session = requests.Session()
_retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.4,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET", "POST"}),
)
_adapter = HTTPAdapter(max_retries=_retry)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


# ─────────────────────────────────────────────
# CORE AUTH
# ─────────────────────────────────────────────

def get_kraken_signature(uri_path: str, data: dict, secret: str) -> str:
    """
    Build the API-Sign header value.
    Formula: base64( HMAC-SHA512( uri_path + SHA256(nonce + postdata), base64decode(secret) ) )
    
    NOTE: nonce must be in data dict before calling this.
    NOTE: secret is your KRAKEN_API_SECRET (base64-encoded string from Kraken dashboard).
    """
    post_data = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + post_data).encode()
    message = uri_path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def get_kraken_headers(uri_path: str, data: dict) -> dict:
    """
    Build auth headers for a private Kraken API call.
    Injects nonce into data dict automatically.
    
    Usage:
        data = {"pair": "XBTUSD"}
        headers = get_kraken_headers("/0/private/Balance", data)
        res = requests.post(BASE_URL + path, headers=headers, data=data)
    """
    api_key = os.getenv("KRAKEN_API_KEY")
    api_secret = os.getenv("KRAKEN_API_SECRET")

    if not api_key or not api_secret:
        raise RuntimeError("Missing KRAKEN_API_KEY or KRAKEN_API_SECRET in .env")

    # Nonce: always-increasing integer. Millisecond timestamp works perfectly.
    data["nonce"] = str(int(time.time() * 1000))

    sig = get_kraken_signature(uri_path, data, api_secret)

    return {
        "API-Key": api_key,
        "API-Sign": sig,
    }


def _request_json(method: str, path: str, *, data: dict | None = None, private: bool = False, timeout: int = 10) -> dict:
    """Send a Kraken request and return parsed JSON or {} on failure."""
    payload = dict(data or {})
    try:
        headers = get_kraken_headers(path, payload) if private else None
        if method == "GET":
            res = _session.get(BASE_URL + path, timeout=timeout)
        else:
            res = _session.post(BASE_URL + path, headers=headers, data=payload, timeout=timeout)
        res.raise_for_status()
        body = res.json()
        if body.get("error"):
            print(f"  ⚠️ Kraken API error on {path}: {body['error']}")
            return {}
        return body
    except Exception as e:
        print(f"  ⚠️ Kraken request failed on {path}: {e}")
        return {}


# ─────────────────────────────────────────────
# ACCOUNT
# ─────────────────────────────────────────────

def get_balance() -> dict:
    """
    Returns dict of all asset balances, e.g. {"ZUSD": "1234.56", "XXBT": "0.5"}
    Kraken asset names: ZUSD = USD, XXBT = BTC, XETH = ETH, etc.
    """
    now = time.time()
    if now - _balance_cache["ts"] <= _PRIVATE_TTL_SECS and _balance_cache["data"]:
        return dict(_balance_cache["data"])

    body = _request_json("POST", "/0/private/Balance", data={}, private=True, timeout=10)
    result = body.get("result", {}) if body else {}
    if result:
        _balance_cache["ts"] = now
        _balance_cache["data"] = dict(result)
    return result

def get_usd_balance() -> float:
    """Convenience: return USD balance as float."""
    balances = get_balance()
    return float(balances.get("ZUSD", 0))


def get_trade_balance(base_asset: str = "ZUSD") -> dict:
    """
    Extended balance info: equity, margin, free margin, etc.
    Useful for margin accounts.
    """
    body = _request_json("POST", "/0/private/TradeBalance", data={"asset": base_asset}, private=True, timeout=10)
    return body.get("result", {}) if body else {}


# ─────────────────────────────────────────────
# MARKET DATA (public — no auth needed)
# ─────────────────────────────────────────────

def get_ticker(pair: str) -> dict:
    """
    Get current ticker for a pair, e.g. "XBTUSD", "ETHUSD", "SOLUSD"
    Returns dict with keys: a (ask), b (bid), c (last trade), v (volume), etc.
    
    NOTE: To rediscover ticker fields:
        print(get_ticker("XBTUSD"))
    """
    body = _request_json("GET", f"/0/public/Ticker?pair={pair}", timeout=8)
    result = body.get("result", {}) if body else {}
    return next(iter(result.values()), {})

def get_ohlc(pair: str, interval: int = 1) -> list:
    """
    Get OHLC candles. interval in minutes: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600
    Returns list of [time, open, high, low, close, vwap, volume, count]
    """
    body = _request_json("GET", f"/0/public/OHLC?pair={pair}&interval={interval}", timeout=8)
    result = body.get("result", {}) if body else {}
    candles = [v for k, v in result.items() if k != "last"]
    return candles[0] if candles else []

def get_asset_pairs() -> dict:
    """
    Get all tradable asset pairs with their minimum order sizes.
    Returns dict with pair info including 'ordermin', 'costmin'.
    """
    now = time.time()
    if now - _asset_pairs_cache["ts"] <= _PUBLIC_TTL_SECS and _asset_pairs_cache["data"]:
        return dict(_asset_pairs_cache["data"])

    body = _request_json("GET", "/0/public/AssetPairs", timeout=8)
    result = body.get("result", {}) if body else {}
    if result:
        _asset_pairs_cache["ts"] = now
        _asset_pairs_cache["data"] = dict(result)
    return result

def resolve_pair_assets(pair: str) -> dict:
    """
    Return Kraken pair metadata with stable base/quote asset codes.

    Looks up by canonical pair key, altname, or wsname.
    Returns {} if the pair is unknown.
    """
    pair_upper = pair.upper()
    for key, info in get_asset_pairs().items():
        names = {
            key.upper(),
            str(info.get("altname", "")).upper(),
            str(info.get("wsname", "")).replace("/", "").upper(),
        }
        if pair_upper in names:
            return {
                "pair_key": key,
                "altname": info.get("altname", key),
                "wsname": info.get("wsname", ""),
                "base": info.get("base"),
                "quote": info.get("quote"),
                "ordermin": float(info.get("ordermin", 0) or 0),
                "costmin": float(info.get("costmin", 0) or 0),
                "pair_decimals": int(info.get("pair_decimals", 2) or 2),
            }
    return {}


def get_balance_for_asset(asset_code: str, balances: dict | None = None) -> float:
    """Return a numeric balance for a Kraken asset code, handling common aliases."""
    balances = balances or get_balance()
    if asset_code in balances:
        return float(balances.get(asset_code, 0) or 0)

    aliases = {
        "USD": ("ZUSD", "USD"),
        "ZUSD": ("ZUSD", "USD"),
        "USDT": ("USDT",),
        "XBT": ("XXBT", "XBT"),
        "XXBT": ("XXBT", "XBT"),
        "ETH": ("XETH", "ETH"),
        "XETH": ("XETH", "ETH"),
        "DOGE": ("XDG", "DOGE"),
        "XDG": ("XDG", "DOGE"),
    }
    for alias in aliases.get(asset_code, (asset_code,)):
        if alias in balances:
            return float(balances.get(alias, 0) or 0)
    return 0.0


def get_pair_base_balance(pair: str, balances: dict | None = None) -> float:
    """Return the held base-asset balance for a Kraken pair."""
    meta = resolve_pair_assets(pair)
    if not meta.get("base"):
        return 0.0
    return get_balance_for_asset(meta["base"], balances=balances)


def get_pair_quote_balance(pair: str, balances: dict | None = None) -> float:
    """Return the held quote-asset balance for a Kraken pair."""
    meta = resolve_pair_assets(pair)
    if not meta.get("quote"):
        return 0.0
    return get_balance_for_asset(meta["quote"], balances=balances)


def get_min_order_info(pair: str) -> dict:
    """
    Get minimum order requirements for a specific pair.
    Returns {'ordermin': float, 'costmin': float} or empty dict.
    """
    pair_info = resolve_pair_assets(pair)
    return {
        'ordermin': float(pair_info.get('ordermin', 0)),
        'costmin': float(pair_info.get('costmin', 0))
    }


def calculate_order_size(pair: str, price: float, available_usd: float = None, available_asset: float = None) -> dict:
    """
    Calculate optimal order size based on minimum requirements and available balance.
    
    For buying: provide available_usd (USD balance)
    For selling: provide available_asset (asset balance)
    
    Returns dict with:
        - volume: float (amount of base asset)
        - cost: float (total cost in quote currency)
        - can_afford: bool (if balance is sufficient)
    """
    min_info = get_min_order_info(pair)
    if not min_info:
        return {'volume': 0, 'cost': 0, 'can_afford': False, 'error': 'No min order info'}
    
    min_volume = min_info['ordermin']
    min_cost = min_info['costmin']
    
    if available_usd is not None:  # Buying
        # Ensure volume is at least ordermin so the position can be sold later
        cost_from_min_volume = min_volume * price
        required_cost = max(min_cost, cost_from_min_volume)

        if available_usd >= required_cost:
            volume = max(min_volume, required_cost / price)
            actual_cost = volume * price
            return {'volume': volume, 'cost': actual_cost, 'can_afford': True}
        else:
            return {'volume': 0, 'cost': 0, 'can_afford': False, 'error': 'Insufficient USD'}
    
    elif available_asset is not None:  # Selling
        if available_asset >= min_volume:
            cost = available_asset * price
            return {'volume': available_asset, 'cost': cost, 'can_afford': True}
        else:
            return {'volume': 0, 'cost': 0, 'can_afford': False, 'error': 'Insufficient asset balance'}
    
    return {'volume': 0, 'cost': 0, 'can_afford': False, 'error': 'Invalid parameters'}


def get_trade_history(count: int = 50) -> dict:
    """Get recent trade history from Kraken."""
    path = "/0/private/TradesHistory"
    nonce = str(int(time.time() * 1000))
    
    data = {
        "nonce": nonce,
        "count": str(count)
    }
    
    try:
        signature = get_kraken_signature(path, data, os.getenv("KRAKEN_API_SECRET", ""))
        headers = {
            "API-Key": os.getenv("KRAKEN_API_KEY", ""),
            "API-Sign": signature
        }
        
        res = requests.post(BASE_URL + path, data=data, headers=headers, timeout=10)
        
        if res.status_code == 200:
            body = res.json()
            if body.get("error"):
                print(f"  ⚠️ Trade history error: {body['error']}")
                return {}
            
            result = body.get("result", {})
            trades = result.get("trades", {})
            
            # Convert trade dict to list and sort by time
            trade_list = []
            for trade_id, trade_data in trades.items():
                trade_list.append({
                    "id": trade_id,
                    "time": trade_data.get("time"),
                    "pair": trade_data.get("pair"),
                    "type": trade_data.get("type"),
                    "order_type": trade_data.get("ordertype"),
                    "price": float(trade_data.get("price", 0)),
                    "cost": float(trade_data.get("cost", 0)),
                    "fee": float(trade_data.get("fee", 0)),
                    "vol": float(trade_data.get("vol", 0)),
                    "margin": float(trade_data.get("margin", 0)),
                    "ordertxid": trade_data.get("ordertxid", ""),
                })
            
            # Sort by time (newest first)
            trade_list.sort(key=lambda x: x["time"], reverse=True)
            return trade_list
            
        else:
            print(f"  ⚠️ Trade history HTTP error: {res.status_code}")
            return {}
            
    except Exception as e:
        print(f"  ⚠️ Trade history exception: {e}")
        return {}

def get_orderbook(pair: str, count: int = 10) -> dict:
    path = f"/0/public/Depth?pair={pair}&count={count}"
    try:
        res = requests.get(BASE_URL + path, timeout=8)
        if res.status_code == 200:
            body = res.json()
            result = body.get("result", {})
            return next(iter(result.values()), {})
    except Exception as e:
        print(f"  ⚠️ Orderbook exception: {e}")
    return {}


# ─────────────────────────────────────────────
# ORDERS
# ─────────────────────────────────────────────

def place_order(
    pair: str,
    side: str,           # "buy" or "sell"
    order_type: str,     # "market" or "limit"
    volume: float = None,       # amount of base currency
    price: float = None,      # required for limit orders
    cost: float = None,       # for USD-based orders (e.g., $1 = cost="1")
    validate: bool = False,    # True = dry run, won't actually place
    userref: int = 0,          # bot identifier (see BOT_USERREFS in portfolio_analyzer)
    close_ordertype: str = None,  # conditional close order type (e.g. "stop-loss")
    close_price: float = None,    # conditional close price (stop-loss trigger)
    close_price2: float = None,   # conditional close price2 (for stop-loss-limit)
) -> tuple[bool, dict]:
    """
    Place a spot order on Kraken.
    
    Examples:
        place_order("SOLUSD", "buy", "market", volume=0.5)
        place_order("XBTUSD", "buy", "limit", volume=0.001, price=50000)
        place_order("SOLUSD", "buy", "market", cost=1.00)  # Buy $1 worth
        place_order("ETHUSD", "sell", "limit", volume=0.1, price=3500, validate=True)
    
    Use 'cost' for dollar-based orders, 'volume' for amount-based.
    For market orders, specify BOTH cost and a minimum volume estimate.
    Returns (success: bool, result_dict)
    """
    path = "/0/private/AddOrder"
    data = {
        "pair": pair,
        "type": side,
        "ordertype": order_type,
    }
    
    # Kraken requires BOTH cost AND volume for market orders
    if cost is not None:
        data["cost"] = str(cost)
    if volume is not None:
        data["volume"] = str(volume)
    else:
        # If only cost provided, estimate volume (rough)
        if cost is not None and price:
            data["volume"] = str(float(cost) / price)
        elif cost is not None:
            return False, {"error": "For cost-based orders without price, must also provide volume"}
    
    if price is not None:
        # Accept pre-formatted strings (e.g. "95.97") to preserve Kraken's required precision
        data["price"] = price if isinstance(price, str) else str(price)
    if validate:
        data["validate"] = "true"  # dry run — Kraken won't execute
    if userref:
        data["userref"] = str(userref)
    if close_ordertype:
        data["close[ordertype]"] = close_ordertype
    if close_price is not None:
        data["close[price]"] = str(close_price)
    if close_price2 is not None:
        data["close[price2]"] = str(close_price2)

    try:
        headers = get_kraken_headers(path, data)
        res = requests.post(BASE_URL + path, headers=headers, data=data, timeout=10)
        body = res.json()
        if body.get("error"):
            print(f"  ❌ Order error: {body['error']}")
            return False, body
        print(f"  ✅ Order placed: {body.get('result', {})}")
        _balance_cache["ts"] = 0.0
        _balance_cache["data"] = {}
        return True, body.get("result", {})
    except Exception as e:
        print(f"  ⚠️ Order exception: {e}")
        return False, {"error": str(e)}


def cancel_order(txid: str) -> bool:
    """Cancel an open order by transaction ID."""
    path = "/0/private/CancelOrder"
    data = {"txid": txid}
    try:
        headers = get_kraken_headers(path, data)
        res = requests.post(BASE_URL + path, headers=headers, data=data, timeout=10)
        body = res.json()
        if body.get("error"):
            print(f"  ❌ Cancel error: {body['error']}")
            return False
        print(f"  ✅ Cancelled: {txid}")
        _balance_cache["ts"] = 0.0
        _balance_cache["data"] = {}
        return True
    except Exception as e:
        print(f"  ⚠️ Cancel exception: {e}")
        return False


def place_bracket_order(pair: str, side: str, volume: str, price: str, price2: str, validate: bool = False) -> tuple[bool, dict]:
    """
    Place a limit order with a conditional close (stop-loss) attached — Kraken OTO bracket.

    Kraken does not support native OCO. Two separate sell orders on the same
    holding fail because the first reserves the asset. Instead we place a
    single limit take-profit as the primary order with a stop-loss attached
    as a conditional close:

    - price:  take-profit limit price (primary, live immediately)
    - price2: stop-loss trigger (conditional close, managed by Kraken)

    Behavior:
      TP fills  → conditional SL is void (position already closed)
      TP cancelled → SL activates as a live order
      Price hits SL trigger while TP is open → Kraken fires the SL and cancels TP

    Kraken shows this as an OTO (One-Triggers-Other) order in the UI.

    Returns (success: bool, result_dict).
    """
    try:
        ok, result = place_order(
            pair=pair,
            side=side,
            order_type="limit",
            volume=float(volume),
            price=float(price),
            validate=validate,
            close_ordertype="stop-loss",
            close_price=float(price2),
        )
        if ok:
            txid = result.get("txid", [])
            print(f"  ✅ TP+SL bracket placed: {txid} | TP @ {price} | SL @ {price2}")
        else:
            err = result.get("error", result)
            print(f"  ❌ Bracket order failed: {err}")
        return ok, result
    except Exception as e:
        print(f"  ❌ Bracket order exception: {e}")
        return False, {"error": str(e)}


# Keep old name as alias for backwards compatibility
place_oco_order = place_bracket_order

def validate_order(order_data: dict) -> str:
    """Validate order data before placing."""
    try:
        # Basic validation checks
        if not order_data.get("pair"):
            return "Missing pair"
        
        if not order_data.get("type"):
            return "Missing order type"
        
        if not order_data.get("ordertype"):
            return "Missing order type (ordertype)"
        
        # Validate volume
        volume = order_data.get("volume", "0")
        try:
            volume_float = float(volume)
            if volume_float <= 0:
                return "Volume must be positive"
        except ValueError:
            return "Invalid volume format"
        
        # Validate prices for limit/stop orders
        if order_data.get("ordertype") in ["limit", "stop-loss", "oco"]:
            price = order_data.get("price", "0")
            try:
                price_float = float(price)
                if price_float <= 0:
                    return "Price must be positive"
            except ValueError:
                return "Invalid price format"
        
        # For OCO orders, validate both prices
        if order_data.get("ordertype") == "oco":
            price2 = order_data.get("price2", "0")
            try:
                price2_float = float(price2)
                if price2_float <= 0:
                    return "Stop price must be positive"
            except ValueError:
                return "Invalid stop price format"
        
        return None  # Validation passed
        
    except Exception as e:
        return f"Validation error: {e}"

def get_open_orders() -> dict:
    """Return all open orders. Keys are transaction IDs."""
    path = "/0/private/OpenOrders"
    data = {}
    try:
        headers = get_kraken_headers(path, data)
        res = requests.post(BASE_URL + path, headers=headers, data=data, timeout=10)
        body = res.json()
        if body.get("error"):
            print(f"  ⚠️ OpenOrders error: {body['error']}")
            return {}
        return body.get("result", {}).get("open", {})
    except Exception as e:
        print(f"  ⚠️ OpenOrders exception: {e}")
    return {}


def query_orders(txids: list[str]) -> dict:
    """Look up orders by transaction ID. Returns {txid: order_info} with userref."""
    if not txids:
        return {}
    path = "/0/private/QueryOrders"
    data = {"txid": ",".join(txids)}
    try:
        headers = get_kraken_headers(path, data)
        res = requests.post(BASE_URL + path, headers=headers, data=data, timeout=10)
        body = res.json()
        if body.get("error"):
            print(f"  ⚠️ QueryOrders error: {body['error']}")
            return {}
        return body.get("result", {})
    except Exception as e:
        print(f"  ⚠️ QueryOrders exception: {e}")
    return {}


def get_closed_orders(trades: bool = True) -> dict:
    """Return closed/filled orders."""
    path = "/0/private/ClosedOrders"
    data = {"trades": "true" if trades else "false"}
    try:
        headers = get_kraken_headers(path, data)
        res = requests.post(BASE_URL + path, headers=headers, data=data, timeout=10)
        body = res.json()
        if body.get("error"):
            print(f"  ⚠️ ClosedOrders error: {body['error']}")
            return {}
        return body.get("result", {}).get("closed", {})
    except Exception as e:
        print(f"  ⚠️ ClosedOrders exception: {e}")
    return {}


# ─────────────────────────────────────────────
# CONNECTION TEST
# ─────────────────────────────────────────────

def test_connection() -> dict:
    """
    Test auth by fetching balance. Prints debug info.
    Run directly: python3 kraken_connection.py
    """
    print("=" * 50)
    print(" 🔌 Testing Kraken connection...")
    print("=" * 50)

    # Public endpoint first (no auth needed)
    ticker = get_ticker("XBTUSD")
    if ticker:
        ask = ticker.get("a", ["?"])[0]
        bid = ticker.get("b", ["?"])[0]
        print(f" ✅ Public API working | BTC ask: ${ask} bid: ${bid}")
    else:
        print(" ❌ Public API failed")

    # Private endpoint
    balances = get_balance()
    if balances:
        print(f" ✅ Auth working | Balances:")
        for asset, amount in balances.items():
            if float(amount) > 0:
                print(f"    {asset}: {amount}")
    else:
        print(" ❌ Auth failed — check KRAKEN_API_KEY and KRAKEN_API_SECRET in .env")

    print("=" * 50)
    return balances


if __name__ == "__main__":
    test_connection()







