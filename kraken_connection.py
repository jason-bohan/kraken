#!/usr/bin/env python3
"""
kraken_connection.py — Kraken API auth + helpers.

Auth method: HMAC-SHA512
Signature = base64( HMAC-SHA512( uri_path + SHA256(nonce + post_data), base64decode(api_secret) ) )

.env keys needed:
    KRAKEN_API_KEY=your_public_api_key
    KRAKEN_API_SECRET=your_private_api_secret

Optional per-process keys (avoids "EAPI:Invalid nonce" when several bots run
at once): start a process with KRAKEN_KEY_PROFILE=NAME and it will use
KRAKEN_API_KEY_NAME / KRAKEN_API_SECRET_NAME instead of the defaults.

Conventions:
  - Prices and volumes sent to Kraken are formatted to the pair's precision
    (format_price / format_volume) so orders are not rejected with
    "EOrder:Invalid price".
  - get_open_orders() raises KrakenAPIError when the request fails. An empty
    dict means "no open orders", never "could not tell".
  - Kraken has no native OCO. Conditional close orders only activate after the
    primary order fills, so use a standalone stop-loss for protection
    (see position_guardian.place_exit_orders).
"""

import os
import time
import hmac
import base64
import hashlib
import threading
import urllib.parse
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, InvalidOperation

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


class KrakenAPIError(RuntimeError):
    """A Kraken request failed (network error, HTTP error, or API error list).

    Subclasses RuntimeError, which is also raised for missing credentials.
    """

    def __init__(self, path: str, error):
        self.path = path
        self.error = error
        super().__init__(f"{path}: {error}")


# Public GETs are idempotent, so they may be retried. Private calls are POSTs
# signed with a nonce: retrying one resends the same nonce (always rejected),
# and retrying AddOrder could place an order twice. They are never retried.
_public_session = requests.Session()
_public_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.4,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
)))
_private_session = requests.Session()


# ─────────────────────────────────────────────
# CORE AUTH
# ─────────────────────────────────────────────

_nonce_lock = threading.Lock()
_last_nonce = 0


def _next_nonce() -> str:
    """
    Strictly increasing millisecond nonce for this process.

    Threads in the same process (e.g. dynamic_hft_bot) can sign requests in
    the same millisecond; a plain timestamp would then repeat. Separate
    processes sharing one API key can still collide — give them separate keys
    (KRAKEN_KEY_PROFILE) or raise the key's nonce window on Kraken.
    """
    global _last_nonce
    with _nonce_lock:
        _last_nonce = max(int(time.time() * 1000), _last_nonce + 1)
        return str(_last_nonce)


def _api_credentials() -> tuple[str | None, str | None]:
    profile = os.getenv("KRAKEN_KEY_PROFILE", "").strip().upper()
    if profile:
        key = os.getenv(f"KRAKEN_API_KEY_{profile}")
        secret = os.getenv(f"KRAKEN_API_SECRET_{profile}")
        if key and secret:
            return key, secret
        raise RuntimeError(
            f"KRAKEN_KEY_PROFILE={profile} is set but "
            f"KRAKEN_API_KEY_{profile} / KRAKEN_API_SECRET_{profile} are missing"
        )
    return os.getenv("KRAKEN_API_KEY"), os.getenv("KRAKEN_API_SECRET")


def get_kraken_signature(uri_path: str, data: dict, secret: str) -> str:
    """
    Build the API-Sign header value.
    NOTE: nonce must be in data dict before calling this.
    """
    post_data = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + post_data).encode()
    message = uri_path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def get_kraken_headers(uri_path: str, data: dict) -> dict:
    """Build auth headers for a private call. Injects the nonce into data."""
    api_key, api_secret = _api_credentials()
    if not api_key or not api_secret:
        raise RuntimeError("Missing KRAKEN_API_KEY or KRAKEN_API_SECRET in .env")

    data["nonce"] = _next_nonce()
    return {
        "API-Key": api_key,
        "API-Sign": get_kraken_signature(uri_path, data, api_secret),
    }


def _private(path: str, data: dict | None = None, timeout: int = 10) -> dict:
    """POST a signed private request. Returns body['result'] or raises KrakenAPIError."""
    payload = dict(data or {})
    try:
        headers = get_kraken_headers(path, payload)
        res = _private_session.post(BASE_URL + path, headers=headers, data=payload, timeout=timeout)
        res.raise_for_status()
        body = res.json()
    except RuntimeError:
        raise
    except Exception as e:
        raise KrakenAPIError(path, str(e)) from e
    if body.get("error"):
        raise KrakenAPIError(path, body["error"])
    return body.get("result", {})


def _public(path: str, timeout: int = 8) -> dict:
    """GET a public endpoint. Returns body['result'] or {} on failure (logged)."""
    try:
        res = _public_session.get(BASE_URL + path, timeout=timeout)
        res.raise_for_status()
        body = res.json()
        if body.get("error"):
            print(f"  ⚠️ Kraken API error on {path}: {body['error']}")
            return {}
        return body.get("result", {})
    except Exception as e:
        print(f"  ⚠️ Kraken request failed on {path}: {e}")
        return {}


def _invalidate_balance_cache() -> None:
    _balance_cache["ts"] = 0.0
    _balance_cache["data"] = {}


# ─────────────────────────────────────────────
# ACCOUNT
# ─────────────────────────────────────────────

def get_balance() -> dict:
    """
    Returns dict of all asset balances, e.g. {"ZUSD": "1234.56", "XXBT": "0.5"}.
    Returns {} on failure (logged).
    """
    now = time.time()
    if now - _balance_cache["ts"] <= _PRIVATE_TTL_SECS and _balance_cache["data"]:
        return dict(_balance_cache["data"])

    try:
        result = _private("/0/private/Balance")
    except RuntimeError as e:  # includes KrakenAPIError
        print(f"  ⚠️ Balance request failed: {e}")
        return {}
    if result:
        _balance_cache["ts"] = now
        _balance_cache["data"] = dict(result)
    return result


def get_usd_balance() -> float:
    """Convenience: return USD balance as float."""
    return float(get_balance().get("ZUSD", 0))


def get_trade_balance(base_asset: str = "ZUSD") -> dict:
    """Extended balance info (equity, margin, free margin). {} on failure."""
    try:
        return _private("/0/private/TradeBalance", {"asset": base_asset})
    except RuntimeError as e:  # includes KrakenAPIError
        print(f"  ⚠️ TradeBalance request failed: {e}")
        return {}


# ─────────────────────────────────────────────
# MARKET DATA (public — no auth needed)
# ─────────────────────────────────────────────

def get_ticker(pair: str) -> dict:
    """
    Current ticker for a pair, e.g. "XBTUSD".
    Keys: a (ask), b (bid), c (last trade), v (volume), h, l, o.
    """
    result = _public(f"/0/public/Ticker?pair={pair}")
    return next(iter(result.values()), {})


def get_ohlc(pair: str, interval: int = 1) -> list:
    """
    OHLC candles (up to 720). interval in minutes: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600.
    Each candle: [time, open, high, low, close, vwap, volume, count]
    """
    result = _public(f"/0/public/OHLC?pair={pair}&interval={interval}")
    candles = [v for k, v in result.items() if k != "last"]
    return candles[0] if candles else []


def get_orderbook(pair: str, count: int = 10) -> dict:
    """Order book: {"asks": [[price, vol, ts], ...], "bids": [...]}. {} on failure."""
    result = _public(f"/0/public/Depth?pair={pair}&count={count}")
    return next(iter(result.values()), {})


def get_asset_pairs() -> dict:
    """All tradable asset pairs with precision and minimums (cached 5 minutes)."""
    now = time.time()
    if now - _asset_pairs_cache["ts"] <= _PUBLIC_TTL_SECS and _asset_pairs_cache["data"]:
        return dict(_asset_pairs_cache["data"])

    result = _public("/0/public/AssetPairs")
    if result:
        _asset_pairs_cache["ts"] = now
        _asset_pairs_cache["data"] = dict(result)
    return result


def resolve_pair_assets(pair: str) -> dict:
    """
    Return Kraken pair metadata. Looks up by canonical key ("XXBTZUSD"),
    altname ("XBTUSD") or wsname ("XBT/USD"). Returns {} if unknown.
    """
    pair_upper = pair.upper().replace("/", "")
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
                "pair_decimals": int(info.get("pair_decimals", 2) or 0),
                "lot_decimals": int(info.get("lot_decimals", 8) or 0),
                "tick_size": info.get("tick_size"),
            }
    return {}


# ─────────────────────────────────────────────
# PRECISION + MINIMUMS
# ─────────────────────────────────────────────

def _plain(d: Decimal) -> str:
    """Decimal → plain string without exponent or trailing zeros."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def format_price(pair: str, price) -> str:
    """
    Format a price the way Kraken accepts it for this pair: rounded to the
    pair's tick size / pair_decimals. Strings are passed through unchanged.
    Falls back to up to 8 decimals if the pair metadata is unavailable.
    """
    if isinstance(price, str):
        return price
    meta = resolve_pair_assets(pair)
    try:
        d = Decimal(str(price))
    except InvalidOperation:
        return str(price)
    if not meta:
        return _plain(d.quantize(Decimal("1e-8"), rounding=ROUND_HALF_UP))

    tick = meta.get("tick_size")
    if tick:
        try:
            t = Decimal(str(tick))
            if t > 0:
                d = (d / t).to_integral_value(rounding=ROUND_HALF_UP) * t
        except InvalidOperation:
            pass
    q = Decimal(1).scaleb(-meta["pair_decimals"])
    return format(d.quantize(q, rounding=ROUND_HALF_UP), "f")


def format_volume(pair: str, volume) -> str:
    """
    Format a volume to the pair's lot_decimals, rounding DOWN so a sell never
    asks for more than is held. Strings are passed through unchanged.
    """
    if isinstance(volume, str):
        return volume
    meta = resolve_pair_assets(pair)
    decimals = meta.get("lot_decimals", 8) if meta else 8
    d = Decimal(str(volume)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)
    return _plain(d)


def below_minimum(pair: str, volume: float, price: float | None = None) -> str | None:
    """
    Return a reason string if this order would be under Kraken's minimum order
    volume (ordermin) or minimum cost (costmin), else None. Returns None if the
    pair metadata is unavailable (Kraken will then decide).
    """
    meta = resolve_pair_assets(pair)
    if not meta:
        return None
    vol = float(format_volume(pair, volume))
    if meta["ordermin"] and vol < meta["ordermin"]:
        return f"volume {vol} below {pair} minimum {meta['ordermin']}"
    if price and meta["costmin"] and vol * float(price) < meta["costmin"]:
        return f"cost ${vol * float(price):.4f} below {pair} minimum ${meta['costmin']}"
    return None


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
        "DOGE": ("XXDG", "XDG", "DOGE"),
        "XDG": ("XXDG", "XDG", "DOGE"),
        "XXDG": ("XXDG", "XDG", "DOGE"),
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
    """{'ordermin': float, 'costmin': float}, or {} if the pair is unknown."""
    pair_info = resolve_pair_assets(pair)
    if not pair_info:
        return {}
    return {
        "ordermin": float(pair_info.get("ordermin", 0)),
        "costmin": float(pair_info.get("costmin", 0)),
    }


def calculate_order_size(pair: str, price: float, available_usd: float = None, available_asset: float = None) -> dict:
    """
    Calculate an order size that satisfies Kraken's minimums.

    For buying: provide available_usd. For selling: provide available_asset.
    Returns {'volume', 'cost', 'can_afford'} (+ 'error' when not affordable).
    """
    min_info = get_min_order_info(pair)
    if not min_info:
        return {"volume": 0, "cost": 0, "can_afford": False, "error": "No min order info"}

    min_volume = min_info["ordermin"]
    min_cost = min_info["costmin"]

    if available_usd is not None:  # Buying
        # Buy at least 2x minimum so the position stays sellable after a price drop
        safe_min_volume = min_volume * 2
        required_cost = max(min_cost, safe_min_volume * price)

        if available_usd >= required_cost:
            volume = max(safe_min_volume, required_cost / price)
            return {"volume": volume, "cost": volume * price, "can_afford": True}
        return {"volume": 0, "cost": 0, "can_afford": False,
                "error": f"Need ${required_cost:.2f} for 2x min order, have ${available_usd:.2f}"}

    if available_asset is not None:  # Selling
        reason = below_minimum(pair, available_asset, price)
        if reason is None:
            return {"volume": available_asset, "cost": available_asset * price, "can_afford": True}
        return {"volume": 0, "cost": 0, "can_afford": False, "error": reason}

    return {"volume": 0, "cost": 0, "can_afford": False, "error": "Invalid parameters"}


# ─────────────────────────────────────────────
# HISTORY
# ─────────────────────────────────────────────

def get_trade_history(count: int = 50, ofs: int = 0) -> list:
    """
    Recent trades, newest first. Kraken returns 50 per page; use ofs to page.
    Returns [] on failure (logged).
    """
    data = {"count": str(count)}
    if ofs:
        data["ofs"] = str(ofs)
    try:
        result = _private("/0/private/TradesHistory", data)
    except RuntimeError as e:  # includes KrakenAPIError
        print(f"  ⚠️ Trade history request failed: {e}")
        return []

    trade_list = []
    for trade_id, t in result.get("trades", {}).items():
        trade_list.append({
            "id": trade_id,
            "time": t.get("time"),
            "pair": t.get("pair"),
            "type": t.get("type"),
            "order_type": t.get("ordertype"),
            "price": float(t.get("price", 0)),
            "cost": float(t.get("cost", 0)),
            "fee": float(t.get("fee", 0)),
            "vol": float(t.get("vol", 0)),
            "margin": float(t.get("margin", 0)),
            "ordertxid": t.get("ordertxid", ""),
        })
    trade_list.sort(key=lambda x: x["time"], reverse=True)
    return trade_list


def get_ledger(asset: str = None, ltype: str = None, ofs: int = 0) -> list:
    """
    Ledger entries (deposits, withdrawals, trades, fees, ...), sorted by time.
    ltype: 'deposit', 'withdrawal', 'trade', 'margin', 'rollover', 'transfer', 'all'.
    Returns [] on failure (logged).
    """
    data = {}
    if asset:
        data["asset"] = asset
    if ltype:
        data["type"] = ltype
    if ofs:
        data["ofs"] = str(ofs)
    try:
        result = _private("/0/private/Ledgers", data)
    except RuntimeError as e:  # includes KrakenAPIError
        print(f"  ⚠️ Ledger request failed: {e}")
        return []

    entries = []
    for lid, info in result.get("ledger", {}).items():
        entries.append({
            "id": lid,
            "type": info.get("type", ""),
            "asset": info.get("asset", ""),
            "amount": float(info.get("amount", 0)),
            "fee": float(info.get("fee", 0)),
            "time": float(info.get("time", 0)),
            "balance": float(info.get("balance", 0)),
        })
    return sorted(entries, key=lambda x: x["time"])


# ─────────────────────────────────────────────
# ORDERS
# ─────────────────────────────────────────────

def place_order(
    pair: str,
    side: str,                    # "buy" or "sell"
    order_type: str,              # "market", "limit", "stop-loss", ...
    volume: float = None,         # amount of base currency
    price: float = None,          # limit price, or trigger price for stop-loss
    cost: float = None,           # USD amount; only used to derive volume when volume is None
    validate: bool = False,       # True = Kraken validates but does not place the order
    userref: int = 0,             # bot identifier (see BOT_USERREFS in portfolio_analyzer)
    close_ordertype: str = None,  # conditional close: only activates AFTER this order fills
    close_price: float = None,
    close_price2: float = None,
) -> tuple[bool, dict]:
    """
    Place a spot order on Kraken. Returns (success, result_dict).

    Prices and volume are formatted to the pair's precision. Orders under
    Kraken's minimum volume are refused locally (result has "below_minimum":
    True) instead of being sent and rejected.

    Examples:
        place_order("SOLUSD", "buy", "market", volume=0.5)
        place_order("XBTUSD", "buy", "limit", volume=0.001, price=50000)
        place_order("SOLUSD", "buy", "market", cost=10.0, price=150.0)  # ~$10 worth
        place_order("ETHUSD", "sell", "stop-loss", volume=0.1, price=3200)
    """
    path = "/0/private/AddOrder"

    if volume is None:
        if cost is not None and price:
            volume = float(cost) / float(price)
        else:
            return False, {"error": ["Local: volume is required (or cost together with price)"]}

    ref_price = None
    if price is not None:
        try:
            ref_price = float(price)
        except (TypeError, ValueError):
            ref_price = None
    reason = below_minimum(pair, float(volume), ref_price)
    if reason:
        print(f"  ❌ Order not sent: {reason}")
        return False, {"error": [f"Local: {reason}"], "below_minimum": True}

    data = {
        "pair": pair,
        "type": side,
        "ordertype": order_type,
        "volume": format_volume(pair, volume),
    }
    if price is not None:
        data["price"] = format_price(pair, price)
    if validate:
        data["validate"] = "true"
    if userref:
        data["userref"] = str(userref)
    if close_ordertype:
        data["close[ordertype]"] = close_ordertype
    if close_price is not None:
        data["close[price]"] = format_price(pair, close_price)
    if close_price2 is not None:
        data["close[price2]"] = format_price(pair, close_price2)

    try:
        result = _private(path, data)
    except KrakenAPIError as e:
        print(f"  ❌ Order error: {e.error}")
        return False, {"error": e.error if isinstance(e.error, list) else [str(e.error)]}
    except Exception as e:
        print(f"  ⚠️ Order exception: {e}")
        return False, {"error": [str(e)]}

    print(f"  ✅ Order placed: {result}")
    _invalidate_balance_cache()
    return True, result


def cancel_order(txid: str) -> bool:
    """Cancel an open order by transaction ID. Returns True on success."""
    try:
        _private("/0/private/CancelOrder", {"txid": txid})
    except RuntimeError as e:  # includes KrakenAPIError
        print(f"  ❌ Cancel error for {txid}: {e}")
        return False
    print(f"  ✅ Cancelled: {txid}")
    _invalidate_balance_cache()
    return True


def validate_order(order_data: dict) -> str | None:
    """Basic sanity checks on an order dict. Returns an error string or None."""
    if not order_data.get("pair"):
        return "Missing pair"
    if not order_data.get("type"):
        return "Missing order type"
    if not order_data.get("ordertype"):
        return "Missing order type (ordertype)"
    try:
        if float(order_data.get("volume", "0")) <= 0:
            return "Volume must be positive"
    except ValueError:
        return "Invalid volume format"
    if order_data.get("ordertype") in ["limit", "stop-loss"]:
        try:
            if float(order_data.get("price", "0")) <= 0:
                return "Price must be positive"
        except ValueError:
            return "Invalid price format"
    return None


def get_open_orders() -> dict:
    """
    All open orders, keyed by transaction ID.

    Raises KrakenAPIError if the request fails. Callers must not treat a
    failed request as "no open orders" — that is how bots used to conclude a
    stop-loss had fired when it had not.
    """
    return _private("/0/private/OpenOrders").get("open", {})


def query_orders(txids: list[str]) -> dict:
    """Look up orders by transaction ID. Returns {txid: order_info}; {} on failure."""
    if not txids:
        return {}
    try:
        return _private("/0/private/QueryOrders", {"txid": ",".join(txids)})
    except RuntimeError as e:  # includes KrakenAPIError
        print(f"  ⚠️ QueryOrders request failed: {e}")
        return {}


def get_order_status(txid: str) -> str | None:
    """
    Status of one order: "pending", "open", "closed" (filled), "canceled",
    "expired" — or None if it could not be determined.
    """
    info = query_orders([txid]).get(txid)
    return info.get("status") if info else None


def get_fill(txid: str, attempts: int = 3, delay: float = 1.0) -> dict | None:
    """
    Executed volume, average price and fee of an order:
        {"status", "vol_exec", "avg_price", "cost", "fee"}
    Retries briefly while a market order is still settling. Returns None if
    the order could not be looked up.
    """
    info = None
    for i in range(attempts):
        info = query_orders([txid]).get(txid)
        if info and info.get("status") in ("closed", "canceled", "expired"):
            break
        if i < attempts - 1:
            time.sleep(delay)
    if not info:
        return None
    vol_exec = float(info.get("vol_exec", 0) or 0)
    cost = float(info.get("cost", 0) or 0)
    avg_price = float(info.get("price", 0) or 0) or (cost / vol_exec if vol_exec else 0.0)
    return {
        "status": info.get("status"),
        "vol_exec": vol_exec,
        "avg_price": avg_price,
        "cost": cost,
        "fee": float(info.get("fee", 0) or 0),
    }


def pair_altname(pair: str) -> str:
    """Normalise any pair name ("XXBTZUSD", "XBT/USD", "XBTUSD") to Kraken's altname."""
    meta = resolve_pair_assets(pair)
    return meta.get("altname", pair) if meta else pair


def get_closed_orders(trades: bool = True) -> dict:
    """Closed/filled orders keyed by txid. {} on failure (logged)."""
    try:
        result = _private("/0/private/ClosedOrders", {"trades": "true" if trades else "false"})
    except RuntimeError as e:  # includes KrakenAPIError
        print(f"  ⚠️ ClosedOrders request failed: {e}")
        return {}
    return result.get("closed", {})


# ─────────────────────────────────────────────
# CONNECTION TEST
# ─────────────────────────────────────────────

def test_connection() -> dict:
    """Test public + private access. Run directly: python3 kraken_connection.py"""
    print("=" * 50)
    print(" 🔌 Testing Kraken connection...")
    print("=" * 50)

    ticker = get_ticker("XBTUSD")
    if ticker:
        print(f" ✅ Public API working | BTC ask: ${ticker.get('a', ['?'])[0]} bid: ${ticker.get('b', ['?'])[0]}")
    else:
        print(" ❌ Public API failed")

    balances = get_balance()
    if balances:
        print(" ✅ Auth working | Balances:")
        for asset, amount in balances.items():
            if float(amount) > 0:
                print(f"    {asset}: {amount}")
    else:
        print(" ❌ Auth failed — check KRAKEN_API_KEY and KRAKEN_API_SECRET in .env")

    print("=" * 50)
    return balances


if __name__ == "__main__":
    test_connection()
