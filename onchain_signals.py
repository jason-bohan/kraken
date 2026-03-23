#!/usr/bin/env python3
"""
On-Chain Signals Module — OKX Futures Data

Provides funding rates, long/short ratios, and open interest data
from OKX (free, no API key needed) to help bots avoid
overleveraged entries and spot reversal setups.

Signals:
  - Funding rate: positive = longs pay shorts, negative = shorts pay longs
  - Long/short ratio: >1 = more longs, <1 = more shorts
  - Open interest changes: rising OI + rising price = strong trend

Decision matrix:
  HIGH_FUNDING (>0.05%):   Longs overleveraged — reduce buys, expect pullback
  EXTREME_FUNDING (>0.1%): Squeeze incoming — BLOCK buys
  NEGATIVE_FUNDING (<0):   Shorts paying — good accumulation zone
  EXTREME_LS_LONG (>2.0):  Too many longs — reversal risk
  EXTREME_LS_SHORT (<0.5): Too many shorts — squeeze up likely, BUY signal

Usage:
    from onchain_signals import get_onchain_signals, should_buy_onchain

    signals = get_onchain_signals()
    decision = should_buy_onchain(signals)
"""

import time
import json
import requests
from pathlib import Path

CACHE_FILE = Path(__file__).parent / ".onchain_cache.json"
CACHE_TTL = 300  # 5 minutes

OKX_BASE = "https://www.okx.com"

# OKX swap instrument IDs (BTC and ETH drive the market)
INSTRUMENTS = {
    "BTC": "BTC-USDT-SWAP",
    "ETH": "ETH-USDT-SWAP",
}


def _load_cache() -> dict | None:
    try:
        if not CACHE_FILE.exists():
            return None
        data = json.loads(CACHE_FILE.read_text())
        if time.time() - data.get("timestamp", 0) < CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def _save_cache(data: dict):
    try:
        data["timestamp"] = time.time()
        CACHE_FILE.write_text(json.dumps(data))
    except Exception:
        pass


def _get_funding_rates() -> dict:
    """Get current funding rates from OKX."""
    rates = {}
    for name, inst_id in INSTRUMENTS.items():
        try:
            r = requests.get(
                f"{OKX_BASE}/api/v5/public/funding-rate",
                params={"instId": inst_id},
                timeout=10,
            )
            resp = r.json()
            if resp.get("code") == "0" and resp.get("data"):
                d = resp["data"][0]
                rates[name] = {
                    "rate": float(d["fundingRate"]),
                    "time": int(d["fundingTime"]),
                    "settled_rate": float(d.get("settFundingRate", 0)),
                }
            else:
                rates[name] = {"rate": 0, "time": 0, "error": resp.get("msg", "unknown")}
        except Exception as e:
            rates[name] = {"rate": 0, "time": 0, "error": str(e)}
    return rates


def _get_long_short_ratio() -> dict:
    """Get long/short account ratio from OKX."""
    ratios = {}
    for name in INSTRUMENTS:
        try:
            r = requests.get(
                f"{OKX_BASE}/api/v5/rubik/stat/contracts/long-short-account-ratio",
                params={"ccy": name, "period": "1H"},
                timeout=10,
            )
            resp = r.json()
            if resp.get("code") == "0" and resp.get("data"):
                # OKX returns [timestamp, ratio] pairs, newest first
                ratio = float(resp["data"][0][1])
                # Convert ratio to long/short percentages
                long_pct = ratio / (1 + ratio)
                short_pct = 1 / (1 + ratio)
                ratios[name] = {
                    "ratio": ratio,
                    "long_pct": round(long_pct, 4),
                    "short_pct": round(short_pct, 4),
                }
            else:
                ratios[name] = {"ratio": 1.0, "long_pct": 0.5, "short_pct": 0.5,
                                "error": resp.get("msg", "unknown")}
        except Exception as e:
            ratios[name] = {"ratio": 1.0, "long_pct": 0.5, "short_pct": 0.5, "error": str(e)}
    return ratios


def _get_open_interest() -> dict:
    """Get current open interest and 24h change from OKX."""
    oi = {}
    for name, inst_id in INSTRUMENTS.items():
        try:
            # Current OI
            r = requests.get(
                f"{OKX_BASE}/api/v5/public/open-interest",
                params={"instType": "SWAP", "instId": inst_id},
                timeout=10,
            )
            resp = r.json()
            if resp.get("code") == "0" and resp.get("data"):
                current_oi_usd = float(resp["data"][0]["oiUsd"])
            else:
                current_oi_usd = 0

            # Historical OI for 24h change (returns newest first)
            r2 = requests.get(
                f"{OKX_BASE}/api/v5/rubik/stat/contracts/open-interest-volume",
                params={"ccy": name, "period": "1H"},
                timeout=10,
            )
            resp2 = r2.json()
            oi_change = 0
            if resp2.get("code") == "0" and resp2.get("data") and len(resp2["data"]) >= 24:
                # data[0] = newest, data[23] = ~24h ago
                # Format: [timestamp, oi_usd, volume_usd]
                oi_now = float(resp2["data"][0][1])
                oi_24h_ago = float(resp2["data"][23][1])
                if oi_24h_ago > 0:
                    oi_change = round((oi_now - oi_24h_ago) / oi_24h_ago * 100, 2)

            oi[name] = {
                "current_usd": current_oi_usd,
                "change_24h": oi_change,
            }
        except Exception as e:
            oi[name] = {"current_usd": 0, "change_24h": 0, "error": str(e)}
    return oi


def get_onchain_signals() -> dict:
    """
    Fetch all on-chain signals. Returns cached data if fresh.

    Returns dict with:
        funding: {BTC: {rate, time}, ETH: {rate, time}}
        long_short: {BTC: {ratio, long_pct, short_pct}, ...}
        open_interest: {BTC: {current_usd, change_24h}, ...}
        avg_funding: float (average across symbols, as percentage)
        avg_ls_ratio: float
        avg_oi_change: float
        timestamp: float
        source: str
        error: str or None
    """
    cached = _load_cache()
    if cached and "funding" in cached:
        cached["source"] = "cache"
        return cached

    result = {
        "funding": {},
        "long_short": {},
        "open_interest": {},
        "avg_funding": 0,
        "avg_ls_ratio": 1.0,
        "avg_oi_change": 0,
        "timestamp": time.time(),
        "source": "live",
        "error": None,
    }

    try:
        result["funding"] = _get_funding_rates()
        rates = [v["rate"] for v in result["funding"].values() if "error" not in v]
        result["avg_funding"] = round(sum(rates) / len(rates) * 100, 4) if rates else 0
    except Exception as e:
        result["error"] = f"Funding: {e}"

    try:
        result["long_short"] = _get_long_short_ratio()
        ratios = [v["ratio"] for v in result["long_short"].values() if "error" not in v]
        result["avg_ls_ratio"] = round(sum(ratios) / len(ratios), 3) if ratios else 1.0
    except Exception as e:
        result["error"] = (result["error"] or "") + f"; L/S: {e}"

    try:
        result["open_interest"] = _get_open_interest()
        changes = [v["change_24h"] for v in result["open_interest"].values() if "error" not in v]
        result["avg_oi_change"] = round(sum(changes) / len(changes), 2) if changes else 0
    except Exception as e:
        result["error"] = (result["error"] or "") + f"; OI: {e}"

    _save_cache(result)
    return result


def should_buy_onchain(signals: dict = None) -> dict:
    """
    Decide whether to buy based on on-chain data.

    Returns:
        allow: bool
        size_multiplier: float (1.0 = normal, 0.5 = half, etc.)
        reason: str
        funding_signal: str (HIGH/EXTREME/NEGATIVE/NORMAL)
    """
    if signals is None:
        signals = get_onchain_signals()

    avg_funding = signals.get("avg_funding", 0)  # percentage
    avg_ls = signals.get("avg_ls_ratio", 1.0)
    avg_oi_change = signals.get("avg_oi_change", 0)

    # Extreme funding = overleveraged longs, crash risk
    if avg_funding > 0.1:
        return {
            "allow": False,
            "size_multiplier": 0.0,
            "reason": f"Extreme funding {avg_funding:.3f}% — longs overleveraged, blocking buys",
            "funding_signal": "EXTREME",
        }

    # High funding = caution
    if avg_funding > 0.05:
        return {
            "allow": True,
            "size_multiplier": 0.5,
            "reason": f"High funding {avg_funding:.3f}% — longs stretched, half-size",
            "funding_signal": "HIGH",
        }

    # Extreme long/short imbalance
    if avg_ls > 2.5:
        return {
            "allow": True,
            "size_multiplier": 0.5,
            "reason": f"L/S ratio {avg_ls:.2f} — too many longs, half-size",
            "funding_signal": "NORMAL",
        }

    # Negative funding = shorts paying, good for longs
    if avg_funding < -0.01:
        multiplier = 1.0
        return {
            "allow": True,
            "size_multiplier": multiplier,
            "reason": f"Negative funding {avg_funding:.3f}% — shorts paying, good entry zone",
            "funding_signal": "NEGATIVE",
        }

    # Normal conditions
    multiplier = 1.0
    # If OI dropping fast, market is deleveraging — be cautious
    if avg_oi_change < -10:
        multiplier = 0.75

    return {
        "allow": True,
        "size_multiplier": multiplier,
        "reason": f"Normal funding {avg_funding:.3f}%, L/S {avg_ls:.2f}, OI {avg_oi_change:+.1f}%",
        "funding_signal": "NORMAL",
    }


def format_onchain(signals: dict = None) -> str:
    """Format on-chain signals for log output."""
    if signals is None:
        signals = get_onchain_signals()

    avg_ls = signals.get("avg_ls_ratio", 1.0)
    avg_oi = signals.get("avg_oi_change", 0)
    src = signals.get("source", "?")

    btc_f = signals.get("funding", {}).get("BTC", {}).get("rate", 0) * 100
    eth_f = signals.get("funding", {}).get("ETH", {}).get("rate", 0) * 100

    return (
        f"Funding: BTC {btc_f:.3f}% ETH {eth_f:.3f}% | "
        f"L/S: {avg_ls:.2f} | OI 24h: {avg_oi:+.1f}% [{src}]"
    )


if __name__ == "__main__":
    print("=== On-Chain Signals (OKX) ===")
    s = get_onchain_signals()
    print(format_onchain(s))
    print()
    d = should_buy_onchain(s)
    print(f"Should buy: {d['allow']}")
    print(f"Size mult:  {d['size_multiplier']}")
    print(f"Reason:     {d['reason']}")
    print(f"Funding:    {d['funding_signal']}")
    if s.get("error"):
        print(f"Errors:     {s['error']}")

    # Details
    print()
    for name, inst_id in INSTRUMENTS.items():
        f = s["funding"].get(name, {})
        ls = s["long_short"].get(name, {})
        oi = s["open_interest"].get(name, {})
        print(f"{name}:")
        print(f"  Funding: {f.get('rate',0)*100:.4f}%")
        print(f"  L/S ratio: {ls.get('ratio',0):.3f} (long {ls.get('long_pct',0)*100:.1f}% / short {ls.get('short_pct',0)*100:.1f}%)")
        print(f"  OI: ${oi.get('current_usd',0):,.0f} | 24h change: {oi.get('change_24h',0):+.1f}%")
