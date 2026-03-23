#!/usr/bin/env python3
"""
Market Sentiment Module — CoinMarketCap Integration

Provides market-wide sentiment signals to all bots.
Prevents buying into crashing markets and identifies accumulation zones.

Signals:
  - Fear & Greed Index (0-100)
  - 24h market cap change
  - BTC dominance trend

Decision matrix:
  EXTREME_FEAR (0-15):  BLOCK buys — market may still be crashing
  FEAR (15-30):         CAUTION — only buy strong signals, half size
  NEUTRAL (30-55):      NORMAL — trade normally
  GREED (55-80):        NORMAL — trade normally, tighter stops
  EXTREME_GREED (80+):  CAUTION — reduce buys, consider taking profits

Usage:
    from market_sentiment import get_market_sentiment, should_buy

    sentiment = get_market_sentiment()
    decision = should_buy(sentiment)
    if decision["allow"]:
        # proceed with buy, use decision["size_multiplier"]
"""

import os
import time
import json
import requests
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

CMC_API_KEY = os.getenv("CMC_API_KEY", "")
CACHE_FILE = Path(__file__).parent / ".sentiment_cache.json"
CACHE_TTL = 300  # 5 minutes — avoid burning API credits

HEADERS = {
    "X-CMC_PRO_API_KEY": CMC_API_KEY,
    "Accept": "application/json",
}


def _load_cache() -> dict | None:
    """Load cached sentiment if fresh enough."""
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
    """Save sentiment data to cache."""
    try:
        data["timestamp"] = time.time()
        CACHE_FILE.write_text(json.dumps(data))
    except Exception:
        pass


def get_market_sentiment() -> dict:
    """
    Fetch current market sentiment from CoinMarketCap.
    Returns cached data if fresh enough (5-min TTL).

    Returns dict with:
        fear_greed: int (0-100)
        fear_greed_label: str
        market_cap_change_24h: float (percentage)
        btc_dominance: float
        total_market_cap: float
        total_volume_24h: float
        timestamp: float
        source: str ("live" or "cache")
        error: str or None
    """
    # Check cache first
    cached = _load_cache()
    if cached and "fear_greed" in cached:
        cached["source"] = "cache"
        return cached

    result = {
        "fear_greed": 50,
        "fear_greed_label": "Neutral",
        "market_cap_change_24h": 0.0,
        "btc_dominance": 0.0,
        "total_market_cap": 0.0,
        "total_volume_24h": 0.0,
        "timestamp": time.time(),
        "source": "live",
        "error": None,
    }

    # Fetch Fear & Greed
    try:
        r = requests.get(
            "https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest",
            headers=HEADERS,
            timeout=10,
        )
        data = r.json()
        if data.get("data"):
            result["fear_greed"] = int(data["data"]["value"])
            result["fear_greed_label"] = data["data"].get("value_classification", "Unknown")
    except Exception as e:
        result["error"] = f"Fear&Greed fetch failed: {e}"

    # Fetch global metrics
    try:
        r = requests.get(
            "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest",
            headers=HEADERS,
            timeout=10,
        )
        data = r.json()
        if data.get("data"):
            d = data["data"]
            q = d.get("quote", {}).get("USD", {})
            result["btc_dominance"] = round(d.get("btc_dominance", 0), 2)
            result["total_market_cap"] = q.get("total_market_cap", 0)
            result["total_volume_24h"] = q.get("total_volume_24h", 0)
            result["market_cap_change_24h"] = round(
                q.get("total_market_cap_yesterday_percentage_change", 0), 2
            )
    except Exception as e:
        if result["error"]:
            result["error"] += f"; Global metrics failed: {e}"
        else:
            result["error"] = f"Global metrics failed: {e}"

    _save_cache(result)
    return result


def classify_sentiment(fear_greed: int) -> str:
    """Classify fear & greed score into trading zones."""
    if fear_greed <= 15:
        return "EXTREME_FEAR"
    elif fear_greed <= 30:
        return "FEAR"
    elif fear_greed <= 55:
        return "NEUTRAL"
    elif fear_greed <= 80:
        return "GREED"
    else:
        return "EXTREME_GREED"


def _sentiment_decision(sentiment: dict) -> dict:
    """Pure sentiment-based buy decision (without on-chain)."""
    fg = sentiment.get("fear_greed", 50)
    mkt_change = sentiment.get("market_cap_change_24h", 0)
    zone = classify_sentiment(fg)

    if mkt_change < -5.0:
        return {
            "allow": False, "size_multiplier": 0.0,
            "reason": f"Market crash: {mkt_change:+.1f}% 24h — waiting for stability",
            "zone": zone, "fear_greed": fg,
        }

    if zone == "EXTREME_FEAR":
        return {
            "allow": False, "size_multiplier": 0.0,
            "reason": f"Extreme Fear ({fg}) — market may still be crashing, blocking buys",
            "zone": zone, "fear_greed": fg,
        }

    if zone == "FEAR":
        return {
            "allow": True, "size_multiplier": 0.5,
            "reason": f"Fear ({fg}) — half-size buys only, proceed with caution",
            "zone": zone, "fear_greed": fg,
        }

    if zone == "EXTREME_GREED":
        return {
            "allow": True, "size_multiplier": 0.5,
            "reason": f"Extreme Greed ({fg}) — half-size buys, reversal risk",
            "zone": zone, "fear_greed": fg,
        }

    multiplier = 1.0
    if mkt_change < -2.0:
        multiplier = 0.75

    return {
        "allow": True, "size_multiplier": multiplier,
        "reason": f"{zone.title()} ({fg}), market {mkt_change:+.1f}% 24h — normal trading",
        "zone": zone, "fear_greed": fg,
    }


def should_buy(sentiment: dict = None) -> dict:
    """
    Combined buy decision using sentiment + on-chain signals.

    Takes the most conservative signal from both:
    - If either blocks, block.
    - Size multiplier = minimum of both.

    Returns:
        allow: bool — whether to allow the buy
        size_multiplier: float — scale position size (0.5 = half, 1.0 = normal)
        reason: str — human-readable explanation
        zone: str — sentiment zone name
        funding_signal: str — on-chain signal (NORMAL/HIGH/EXTREME/NEGATIVE)
    """
    if sentiment is None:
        sentiment = get_market_sentiment()

    # Sentiment decision
    sent_dec = _sentiment_decision(sentiment)

    # On-chain decision (funding rates, L/S, OI)
    try:
        from onchain_signals import should_buy_onchain, get_onchain_signals
        onchain = get_onchain_signals()
        chain_dec = should_buy_onchain(onchain)
    except Exception:
        chain_dec = {"allow": True, "size_multiplier": 1.0, "reason": "on-chain unavailable",
                     "funding_signal": "UNKNOWN"}

    # Combine: most conservative wins
    if not sent_dec["allow"]:
        sent_dec["funding_signal"] = chain_dec.get("funding_signal", "UNKNOWN")
        return sent_dec

    if not chain_dec["allow"]:
        return {
            "allow": False,
            "size_multiplier": 0.0,
            "reason": chain_dec["reason"],
            "zone": sent_dec["zone"],
            "fear_greed": sent_dec["fear_greed"],
            "funding_signal": chain_dec["funding_signal"],
        }

    # Both allow — take the lower multiplier
    final_mult = min(sent_dec["size_multiplier"], chain_dec["size_multiplier"])

    # Build combined reason
    if final_mult < sent_dec["size_multiplier"]:
        reason = f"{sent_dec['reason']} | On-chain: {chain_dec['reason']}"
    elif final_mult < chain_dec["size_multiplier"]:
        reason = f"{sent_dec['reason']}"
    else:
        reason = sent_dec["reason"]

    return {
        "allow": True,
        "size_multiplier": final_mult,
        "reason": reason,
        "zone": sent_dec["zone"],
        "fear_greed": sent_dec["fear_greed"],
        "funding_signal": chain_dec.get("funding_signal", "NORMAL"),
    }


def format_sentiment(sentiment: dict = None) -> str:
    """Format sentiment for log output."""
    if sentiment is None:
        sentiment = get_market_sentiment()

    fg = sentiment.get("fear_greed", 50)
    label = sentiment.get("fear_greed_label", "?")
    mkt = sentiment.get("market_cap_change_24h", 0)
    btc_dom = sentiment.get("btc_dominance", 0)
    src = sentiment.get("source", "?")

    return (
        f"F&G: {fg} ({label}) | Market 24h: {mkt:+.1f}% | "
        f"BTC dom: {btc_dom:.1f}% [{src}]"
    )


# ─────────────────────────────────────────────
# CLI test
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Market Sentiment ===")
    s = get_market_sentiment()
    print(format_sentiment(s))
    print()
    d = should_buy(s)
    print(f"Should buy: {d['allow']}")
    print(f"Size multiplier: {d['size_multiplier']}")
    print(f"Reason: {d['reason']}")
    if s.get("error"):
        print(f"Errors: {s['error']}")
