#!/usr/bin/env python3
"""
market_data.py — historical candles for backtesting, cached on disk.

Sources (all public, no API key):
  - "coinbase": Coinbase Exchange candles, years of history at 1m/5m/15m/1h/6h/1d.
  - "kraken":   Kraken OHLC, but only the most recent 720 candles per interval.
  - load_csv(): Kraken's downloadable OHLCVT history files
    (timestamp,open,high,low,close,volume,trades), for full Kraken history.

All candles use Kraken's layout: [time, open, high, low, close, vwap, volume, count]
with time in UNIX seconds at the candle open. Cache: data/candles/*.csv
"""

import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent / "data" / "candles"
COINBASE_URL = "https://api.exchange.coinbase.com"
KRAKEN_URL = "https://api.kraken.com"
COINBASE_GRANULARITIES = {1: 60, 5: 300, 15: 900, 60: 3600, 360: 21600, 1440: 86400}

# Kraken altname → Coinbase product, where the base symbol differs
_COINBASE_PRODUCTS = {"XBTUSD": "BTC-USD", "XDGUSD": "DOGE-USD", "XXBTZUSD": "BTC-USD",
                      "XETHZUSD": "ETH-USD", "XXDGZUSD": "DOGE-USD"}


def coinbase_product(pair: str) -> str:
    pair = pair.upper()
    if pair in _COINBASE_PRODUCTS:
        return _COINBASE_PRODUCTS[pair]
    base = pair[:-3] if pair.endswith("USD") else pair
    return f"{base}-USD"


# ─────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────

def _cache_path(source: str, pair: str, interval: int) -> Path:
    return DATA_DIR / f"{source}_{pair.upper()}_{interval}m.csv"


def _read_cache(path: Path) -> list:
    if not path.exists():
        return []
    with path.open() as f:
        return [[int(r[0]), *map(float, r[1:7]), int(float(r[7]))] for r in csv.reader(f) if r]


def _write_cache(path: Path, candles: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        csv.writer(f).writerows(candles)
    tmp.replace(path)


def _merge(*lists: list) -> list:
    by_time = {}
    for lst in lists:
        for c in lst:
            by_time[int(c[0])] = c
    return [by_time[t] for t in sorted(by_time)]


# ─────────────────────────────────────────────
# SOURCES
# ─────────────────────────────────────────────

def _fetch_coinbase(pair: str, interval: int, start: int, end: int, progress: bool = True) -> list:
    gran = COINBASE_GRANULARITIES.get(interval)
    if not gran:
        raise ValueError(f"Coinbase has no {interval}m candles; use one of {sorted(COINBASE_GRANULARITIES)}")
    product = coinbase_product(pair)
    out, t = [], start
    step = gran * 300  # max 300 candles per request
    total = max(1, (end - start) // step + 1)
    n = 0
    while t < end:
        chunk_end = min(t + step, end)
        params = {
            "granularity": gran,
            "start": datetime.fromtimestamp(t, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(chunk_end, timezone.utc).isoformat(),
        }
        for attempt in range(4):
            res = requests.get(f"{COINBASE_URL}/products/{product}/candles", params=params, timeout=15)
            if res.status_code == 429:
                time.sleep(1 + attempt)
                continue
            res.raise_for_status()
            break
        rows = res.json()
        # Coinbase row: [time, low, high, open, close, volume], newest first
        for r in rows:
            o, h, lo, c, v = float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])
            out.append([int(r[0]), o, h, lo, c, c, v, 0])
        n += 1
        if progress and (n % 25 == 0 or n == total):
            print(f"    {product} {interval}m: {n}/{total} requests")
        t = chunk_end
        time.sleep(0.12)  # stay under the public rate limit
    return _merge(out)


def _fetch_kraken(pair: str, interval: int) -> list:
    res = requests.get(f"{KRAKEN_URL}/0/public/OHLC", params={"pair": pair, "interval": interval}, timeout=15)
    res.raise_for_status()
    body = res.json()
    if body.get("error"):
        raise RuntimeError(f"Kraken OHLC {pair}: {body['error']}")
    rows = next((v for k, v in body["result"].items() if k != "last"), [])
    return [[int(r[0]), *map(float, r[1:7]), int(r[7])] for r in rows]


def get_candles(pair: str, interval: int, days: float, source: str = "coinbase",
                end: int | None = None, progress: bool = True) -> list:
    """
    Candles for the last `days` days (ending at `end`, default now), using the
    on-disk cache and fetching only what is missing. The newest, still-forming
    candle is dropped.
    """
    end = int(end or time.time())
    start = end - int(days * 86400)
    path = _cache_path(source, pair, interval)
    cached = _read_cache(path)

    if source == "kraken":
        fresh = _fetch_kraken(pair, interval)
        candles = _merge(cached, fresh)
    elif source == "coinbase":
        have_start = cached[0][0] if cached else None
        have_end = cached[-1][0] if cached else None
        parts = [cached]
        if have_start is None:
            parts.append(_fetch_coinbase(pair, interval, start, end, progress))
        else:
            if start < have_start:
                parts.append(_fetch_coinbase(pair, interval, start, have_start, progress))
            if end > have_end + interval * 60:
                parts.append(_fetch_coinbase(pair, interval, have_end, end, progress))
        candles = _merge(*parts)
    else:
        raise ValueError(f"Unknown source {source!r}")

    _write_cache(path, candles)
    now_bucket = (int(time.time()) // (interval * 60)) * interval * 60
    return [c for c in candles if start <= c[0] <= end and c[0] < now_bucket]


def load_csv(path: str | Path) -> list:
    """
    Load Kraken's downloadable OHLCVT file (timestamp,open,high,low,close,volume,trades)
    or a cache file in Kraken candle layout.
    """
    out = []
    with Path(path).open() as f:
        for r in csv.reader(f):
            if not r or not r[0].strip().lstrip("-").isdigit():
                continue  # header / blank
            if len(r) >= 8:
                out.append([int(r[0]), *map(float, r[1:7]), int(float(r[7]))])
            else:
                t, o, h, lo, c, v = int(r[0]), *map(float, r[1:6])
                n = int(float(r[6])) if len(r) > 6 else 0
                out.append([t, o, h, lo, c, c, v, n])
    return _merge(out)


# ─────────────────────────────────────────────
# TRANSFORMS
# ─────────────────────────────────────────────

def resample(candles: list, minutes: int) -> list:
    """
    Combine candles into `minutes`-long candles aligned to UNIX time
    (e.g. 5m → 1h). Buckets with missing candles (no trades) are kept; only a
    trailing bucket that has not finished yet is dropped.
    """
    if not candles:
        return []
    size = minutes * 60
    base = candles[1][0] - candles[0][0] if len(candles) > 1 else size
    groups: dict[int, list] = {}
    for c in candles:
        groups.setdefault((c[0] // size) * size, []).append(c)
    out = [_combine(t, g) for t, g in sorted(groups.items())]
    if out and out[-1][0] + size > candles[-1][0] + base:
        out.pop()  # last bucket still forming
    return out


def _combine(t: int, group: list) -> list:
    vol = sum(c[6] for c in group)
    vwap = sum(c[5] * c[6] for c in group) / vol if vol else group[-1][4]
    return [t, group[0][1], max(c[2] for c in group), min(c[3] for c in group),
            group[-1][4], vwap, vol, sum(c[7] for c in group)]
