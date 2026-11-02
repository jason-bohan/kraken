#!/usr/bin/env python3
"""
strategies.py — the trading maths, shared by the live bots and the backtester.

Everything here is a pure function of candle data and parameters: no API
calls, no globals. The bots fetch candles and call these; backtester.py
replays history through the same functions, so a backtest tests exactly
the code that trades.

Candles are Kraken-style lists: [time, open, high, low, close, vwap, volume, count]
"""

# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────

def rsi(closes: list, period: int = 14) -> float:
    """RSI from simple averages of the last `period` gains/losses (0-100)."""
    if len(closes) < period + 1:
        return 50.0
    window = closes[-(period + 1):]
    gains = losses = 0.0
    for a, b in zip(window, window[1:]):
        d = b - a
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def ema_series(values: list, period: int) -> list:
    """EMA seeded with the SMA of the first `period` values; one value per input from index period-1."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    out = [ema]
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD line (EMA fast - EMA slow), signal line (EMA of MACD) and histogram."""
    if len(closes) < slow + signal:
        return {"macd": 0, "signal": 0, "histogram": 0}
    fast_ema = ema_series(closes, fast)[slow - fast:]  # align with the slow EMA
    slow_ema = ema_series(closes, slow)
    line = [f - s for f, s in zip(fast_ema, slow_ema)]
    sig = ema_series(line, signal)
    return {"macd": line[-1], "signal": sig[-1], "histogram": line[-1] - sig[-1]}


def atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Average True Range (simple average of the last `period` true ranges)."""
    if len(highs) < 2:
        return 0.0
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(max(1, len(highs) - period), len(highs))
    ]
    return sum(trs) / len(trs) if trs else 0.0


def sma(values: list, period: int) -> float:
    if not values:
        return 0.0
    window = values[-period:]
    return sum(window) / len(window)


def volume_profile(volumes: list, period: int = 20) -> dict:
    """Latest volume vs the average of the previous period-1 candles."""
    if len(volumes) < period:
        return {"volume_ratio": 1.0, "trend": "neutral"}
    avg = sum(volumes[-period:-1]) / (period - 1)
    ratio = volumes[-1] / avg if avg > 0 else 1.0
    if ratio > 1.5:
        trend = "high"
    elif ratio > 1.2:
        trend = "above_average"
    elif ratio < 0.8:
        trend = "low"
    else:
        trend = "average"
    return {"volume_ratio": ratio, "trend": trend, "current": volumes[-1], "average": avg}


# ─────────────────────────────────────────────
# SWING STRATEGY (stock_swing_bot.py)
# ─────────────────────────────────────────────

SWING_TIMEFRAMES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}
SWING_TF_WEIGHTS = {"5m": 1, "15m": 2, "1h": 3, "4h": 4}
SWING_MIN_CANDLES = 50


def analyze_timeframe(candles: list) -> dict | None:
    """Indicators for one timeframe, or None if there are too few candles."""
    if not candles or len(candles) < SWING_MIN_CANDLES:
        return None
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    volumes = [float(c[6]) for c in candles]  # index 6 = volume (5 is vwap)
    price = closes[-1]
    change_20 = (price - closes[-20]) / closes[-20] if len(closes) > 20 and closes[-20] else 0.0
    return {
        "price": price,
        "rsi": rsi(closes),
        "macd": macd(closes),
        "atr": atr(highs, lows, closes),
        "support": min(closes[-20:]),
        "resistance": max(closes[-20:]),
        "volume": volume_profile(volumes),
        "trend": "bullish" if change_20 > 0.02 else "bearish" if change_20 < -0.02 else "sideways",
        "price_change_20": change_20,
    }


def swing_signal(analysis: dict, config: dict) -> dict:
    """
    Multi-timeframe confluence score → "buy" / "sell" / "hold" with a confidence.

    Per timeframe (weighted 5m=1 … 4h=4): RSI oversold +2w / overbought -2w,
    MACD histogram ±1w, 20-candle trend ±0.5w, high volume ±0.3w in the
    direction already leading. Buy when the score exceeds 30% of total weight.
    """
    if not analysis:
        return {"signal": "hold", "confidence": 0, "reason": "No data", "price": 0, "analysis": analysis}

    bullish = bearish = total_weight = score = 0.0
    details = []
    for tf, data in analysis.items():
        w = SWING_TF_WEIGHTS.get(tf, 1)
        total_weight += w
        if data["rsi"] < config["rsi_oversold"]:
            bullish += w
            score += 2 * w
            details.append(f"{tf}: RSI oversold ({data['rsi']:.1f})")
        elif data["rsi"] > config["rsi_overbought"]:
            bearish += w
            score -= 2 * w
            details.append(f"{tf}: RSI overbought ({data['rsi']:.1f})")
        hist = data["macd"]["histogram"]
        if hist > 0:
            bullish += w
            score += w
            details.append(f"{tf}: MACD bullish")
        elif hist < 0:
            bearish += w
            score -= w
            details.append(f"{tf}: MACD bearish")
        if data["trend"] == "bullish":
            bullish += 0.5 * w
            score += 0.5 * w
        elif data["trend"] == "bearish":
            bearish += 0.5 * w
            score -= 0.5 * w
        if data["volume"]["trend"] in ("high", "above_average"):
            if bullish > bearish:
                score += 0.3 * w
            elif bearish > bullish:
                score -= 0.3 * w

    confidence = abs(score) / (total_weight * 2) if total_weight else 0.0
    if score > total_weight * 0.3:
        signal = "buy"
    elif score < -total_weight * 0.3:
        signal = "sell"
    else:
        signal = "hold"

    price = analysis.get("1h", {}).get("price", 0)
    if signal != "hold":
        atr_1h = analysis.get("1h", {}).get("atr", 0)
        if atr_1h > 0 and price and atr_1h / price > config["volatility_threshold"]:
            confidence *= 0.7
            details.append("High volatility warning")

    return {"signal": signal, "confidence": confidence, "reason": "; ".join(details[:4]),
            "price": price, "analysis": analysis}


def swing_stop_and_target(entry: float, atr_1h: float, config: dict) -> tuple[float, float]:
    """Stop 1.5×ATR(1h) below entry (or config stop %), target config profit %."""
    stop = entry - atr_1h * 1.5 if atr_1h > 0 else entry * (1 - config["stop_loss"])
    return stop, entry * (1 + config["profit_target"])


# ─────────────────────────────────────────────
# HFT STRATEGY (dynamic_hft_bot.py)
# ─────────────────────────────────────────────

HFT_DEFAULTS = {
    "rsi_period": 14,
    "rsi_oversold": 40,     # buy when RSI(1m) is at or below this ...
    "rsi_overbought": 70,   # ... never when above this
    "dip_min": 0.02,        # ... or when price is 2-15% below the 30-min high
    "dip_max": 0.15,
    "high_lookback": 30,    # candles for the "recent high"
    "ma_period": 20,        # trend filter (bear regime only)
    "min_confidence": 0.30,
    "profit_pct": 0.10,
    "stop_pct": 0.08,
}


def hft_snapshot(candles: list, params: dict = HFT_DEFAULTS) -> dict | None:
    """RSI, recent high and MA from 1-minute candles."""
    if not candles or len(candles) < params["rsi_period"] + 5:
        return None
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    return {
        "rsi": rsi(closes, params["rsi_period"]),
        "recent_high": max(highs[-params["high_lookback"]:]),
        "ma": sma(closes, params["ma_period"]),
    }


def hft_entry(price: float, snap: dict, regime: str = "neutral", params: dict = HFT_DEFAULTS) -> dict:
    """Entry decision for one pair. Returns {"enter", "confidence", "dip", "reason"}."""
    r = snap["rsi"]
    high = snap["recent_high"]
    dip = (high - price) / high if high else 0.0
    rsi_signal = r <= params["rsi_oversold"]
    dip_signal = params["dip_min"] <= dip <= params["dip_max"]
    not_overbought = r < params["rsi_overbought"]
    uptrend = price > snap["ma"] if regime == "bear" else True
    confidence = min(1.0, (params["rsi_oversold"] - r) / params["rsi_oversold"] + dip)

    enter = (rsi_signal or dip_signal) and not_overbought and uptrend and confidence >= params["min_confidence"]
    return {"enter": enter, "confidence": confidence, "dip": dip,
            "reason": f"RSI {r:.0f} + dip -{dip*100:.1f}%"}


# ─────────────────────────────────────────────
# COST MATHS
# ─────────────────────────────────────────────

def breakeven_win_rate(profit_pct: float, stop_pct: float, round_trip_cost: float) -> float:
    """
    Win rate needed to break even with a fixed take-profit / stop-loss after costs:
        p * (profit - cost) = (1 - p) * (stop + cost)  →  p = (stop + cost) / (profit + stop)
    """
    return (stop_pct + round_trip_cost) / (profit_pct + stop_pct)
