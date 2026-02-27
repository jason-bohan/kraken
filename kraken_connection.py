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
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.kraken.com"


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


# ─────────────────────────────────────────────
# ACCOUNT
# ─────────────────────────────────────────────

def get_balance() -> dict:
    """
    Returns dict of all asset balances, e.g. {"ZUSD": "1234.56", "XXBT": "0.5"}
    Kraken asset names: ZUSD = USD, XXBT = BTC, XETH = ETH, etc.
    """
    path = "/0/private/Balance"
    data = {}
    try:
        headers = get_kraken_headers(path, data)
        res = requests.post(BASE_URL + path, headers=headers, data=data, timeout=10)
        if res.status_code == 200:
            body = res.json()
            if body.get("error"):
                print(f"  ⚠️ Balance error: {body['error']}")
                return {}
            return body.get("result", {})
    except Exception as e:
        print(f"  ⚠️ Balance exception: {e}")
    return {}


def get_usd_balance() -> float:
    """Convenience: return USD balance as float."""
    balances = get_balance()
    return float(balances.get("ZUSD", 0))


def get_trade_balance(base_asset: str = "ZUSD") -> dict:
    """
    Extended balance info: equity, margin, free margin, etc.
    Useful for margin accounts.
    """
    path = "/0/private/TradeBalance"
    data = {"asset": base_asset}
    try:
        headers = get_kraken_headers(path, data)
        res = requests.post(BASE_URL + path, headers=headers, data=data, timeout=10)
        if res.status_code == 200:
            body = res.json()
            if body.get("error"):
                print(f"  ⚠️ TradeBalance error: {body['error']}")
                return {}
            return body.get("result", {})
    except Exception as e:
        print(f"  ⚠️ TradeBalance exception: {e}")
    return {}


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
    path = f"/0/public/Ticker?pair={pair}"
    try:
        res = requests.get(BASE_URL + path, timeout=8)
        if res.status_code == 200:
            body = res.json()
            if body.get("error"):
                print(f"  ⚠️ Ticker error: {body['error']}")
                return {}
            result = body.get("result", {})
            # Kraken sometimes returns the pair with a different key name
            # e.g. "XBTUSD" → "XXBTZUSD". Grab first value to be safe.
            return next(iter(result.values()), {})
    except Exception as e:
        print(f"  ⚠️ Ticker exception: {e}")
    return {}


def get_ohlc(pair: str, interval: int = 1) -> list:
    """
    Get OHLC candles. interval in minutes: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600
    Returns list of [time, open, high, low, close, vwap, volume, count]
    """
    path = f"/0/public/OHLC?pair={pair}&interval={interval}"
    try:
        res = requests.get(BASE_URL + path, timeout=8)
        if res.status_code == 200:
            body = res.json()
            result = body.get("result", {})
            # Remove the "last" key, keep only the candle list
            candles = [v for k, v in result.items() if k != "last"]
            return candles[0] if candles else []
    except Exception as e:
        print(f"  ⚠️ OHLC exception: {e}")
    return []


def get_asset_pairs() -> dict:
    """
    Get all tradable asset pairs with their minimum order sizes.
    Returns dict with pair info including 'ordermin', 'costmin'.
    """
    path = "/0/public/AssetPairs"
    try:
        res = requests.get(BASE_URL + path, timeout=8)
        if res.status_code == 200:
            body = res.json()
            if body.get("error"):
                print(f"  ⚠️ AssetPairs error: {body['error']}")
                return {}
            return body.get("result", {})
    except Exception as e:
        print(f"  ⚠️ AssetPairs exception: {e}")
    return {}


def get_min_order_info(pair: str) -> dict:
    """
    Get minimum order requirements for a specific pair.
    Returns {'ordermin': float, 'costmin': float} or empty dict.
    """
    pairs = get_asset_pairs()
    pair_info = pairs.get(pair, {})
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
        # Use the larger of minimum cost or cost from minimum volume
        cost_from_min_volume = min_volume * price
        required_cost = max(min_cost, cost_from_min_volume)
        
        if available_usd >= required_cost:
            volume = required_cost / price
            return {'volume': volume, 'cost': required_cost, 'can_afford': True}
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
                    "margin": float(trade_data.get("margin", 0))
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

def get_closed_orders(count: int = 50) -> dict:
    """Get closed orders from Kraken."""
    path = "/0/private/ClosedOrders"
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
                print(f"  ⚠️ Closed orders error: {body['error']}")
                return {}
            
            result = body.get("result", {})
            orders = result.get("closed", {})
            
            # Convert order dict to list
            order_list = []
            for order_id, order_data in orders.items():
                order_list.append({
                    "id": order_id,
                    "time": order_data.get("closetm"),
                    "pair": order_data.get("pair"),
                    "type": order_data.get("type"),
                    "order_type": order_data.get("ordertype"),
                    "price": float(order_data.get("price", 0)),
                    "cost": float(order_data.get("cost", 0)),
                    "fee": float(order_data.get("fee", 0)),
                    "vol": float(order_data.get("vol", 0)),
                    "vol_exec": float(order_data.get("vol_exec", 0)),
                    "status": order_data.get("status")
                })
            
            # Sort by time (newest first)
            order_list.sort(key=lambda x: x["time"], reverse=True)
            return order_list
            
        else:
            print(f"  ⚠️ Closed orders HTTP error: {res.status_code}")
            return {}
            
    except Exception as e:
        print(f"  ⚠️ Closed orders exception: {e}")
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
    validate: bool = False     # True = dry run, won't actually place
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
        data["price"] = str(price)
    if validate:
        data["validate"] = "true"  # dry run — Kraken won't execute

    try:
        headers = get_kraken_headers(path, data)
        res = requests.post(BASE_URL + path, headers=headers, data=data, timeout=10)
        body = res.json()
        if body.get("error"):
            print(f"  ❌ Order error: {body['error']}")
            return False, body
        print(f"  ✅ Order placed: {body.get('result', {})}")
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
        return True
    except Exception as e:
        print(f"  ⚠️ Cancel exception: {e}")
        return False


def place_oco_order(
    pair: str,
    side: str,           # "buy" or "sell"
    volume: float,       # amount of base currency
    stop_price: float,   # stop-loss trigger price
    limit_price: float,  # take-profit limit price
    validate: bool = False
) -> tuple[bool, dict]:
    """
    Place an OCO (One-Cancels-Other) order on Kraken.
    
    This places two orders simultaneously: a stop-loss and a take-profit limit.
    When one fills, the other is automatically cancelled.
    
    Note: Kraken doesn't have native OCO, so we simulate it by:
    1. Placing the first order (stop-loss)
    2. Placing the second order (take-profit)
    3. Returning both order IDs - user must manually cancel the other when one fills
    
    Returns (success: bool, dict with both order IDs)
    """
    # First, place the stop-loss order
    stop_result, stop_info = place_order(
        pair=pair,
        side=side,
        order_type="stop-loss",
        volume=volume,
        price=stop_price,
        validate=validate
    )
    
    if not stop_result:
        return False, {"error": f"Stop-loss failed: stop_info", "stop_order": None, "limit_order": None}
    
    stop_order_id = stop_info.get('txid', [None])[0]
    print(f"  ✅ Stop-loss order placed: {stop_order_id}")
    
    # Second, place the take-profit limit order
    limit_result, limit_info = place_order(
        pair=pair,
        side=side,
        order_type="limit",
        volume=volume,
        price=limit_price,
        validate=validate
    )
    
    if not limit_result:
        # If limit fails, cancel the stop-loss and return error
        print(f"  ⚠️ Take-profit failed, canceling stop-loss: {stop_order_id}")
        cancel_order(stop_order_id)
        return False, {"error": f"Limit failed: {limit_info}", "stop_order": stop_order_id, "limit_order": None}
    
    limit_order_id = limit_info.get('txid', [None])[0]
    print(f"  ✅ Take-profit order placed: {limit_order_id}")
    
    # Return both order IDs so the bot can cancel one when the other fills
    return True, {
        "stop_order": stop_order_id,
        "stop_price": stop_price,
        "limit_order": limit_order_id,
        "limit_price": limit_price,
        "pair": pair,
        "side": side,
        "volume": volume
    }


def place_oco_order(pair: str, side: str, volume: str, price: str, price2: str, validate: bool = True) -> tuple[bool, dict]:
    """
    Place OCO (One-Cancels-Other) order - combines stop-loss and take-profit.
    
    OCO orders place two conditions in a single order:
    - price: Take-profit limit order
    - price2: Stop-loss trigger order
    - Only one executes, other cancels automatically
    
    Args:
        pair: Trading pair (e.g., "XBTUSD", "SOLUSD")
        side: "buy" or "sell"
        volume: Amount to trade
        price: Take-profit price (for limit orders)
        price2: Stop-loss price (for stop-loss orders)
        validate: True = dry run, False = live execution
    
    Returns:
        (success: bool, result_dict)
    """
    path = "/0/private/AddOrder"
    data = {
        "pair": pair,
        "type": side,
        "ordertype": "oco",  # One-Cancels-Other
        "volume": volume,
        "price": price,      # Take-profit limit
        "price2": price2,     # Stop-loss trigger
        "oflags": "fciq"   # Standard flags
    }
    
    if validate:
        data["validate"] = "true"  # dry run — Kraken won't execute
    
    try:
        headers = get_kraken_headers(path, data)
        res = requests.post(BASE_URL + path, headers=headers, data=data, timeout=10)
        body = res.json()
        
        if body.get("error"):
            error_msg = body["error"][0] if isinstance(body["error"], list) else str(body["error"])
            print(f"  ❌ OCO Order failed: {error_msg}")
            return False, body
        
        # Extract order info
        order_info = body.get("result", {})
        order_ids = order_info.get("txid", [])
        
        if order_ids:
            print(f"  ✅ OCO Order placed: {order_ids}")
            print(f"  📊 Take-profit: ${price} | Stop-loss: ${price2}")
            return True, order_info
        else:
            print(f"  ❌ No order ID returned")
            return False, response
            
    except Exception as e:
        print(f"  ❌ OCO Order exception: {e}")
        return False, str(e)

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
