#!/usr/bin/env python3
"""
paper_momentum.py — forward paper trading for RESEARCH.md idea #7.

Weekly cross-coin momentum with a BTC filter, using the same signal code as
momentum_backtest.py, on live Kraken prices. No orders are ever placed: the
trades are imaginary and recorded in paper/momentum_ledger.json, which is
committed to git so the record is permanent.

Usage:
    python3 paper_momentum.py            # mark to market; rebalance if a week has passed
    python3 paper_momentum.py --status   # show the record without changing it
    python3 paper_momentum.py --demo     # see the rules buy, on past prices (nothing recorded)

Run it weekly (GitHub Actions does this: .github/workflows/paper-momentum.yml).
Running more often is harmless: it only rebalances once 7 days have passed.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import kraken_connection as kc
import strategies
from backtester import COST_PRESETS

LEDGER = Path(__file__).resolve().parent / "paper" / "momentum_ledger.json"
UNIVERSE = ["XBTUSD", "ETHUSD", "SOLUSD", "ADAUSD", "XDGUSD", "LINKUSD", "AVAXUSD", "XRPUSD",
            "DOTUSD", "LTCUSD", "BCHUSD", "UNIUSD", "AAVEUSD", "XLMUSD", "ATOMUSD", "NEARUSD"]
BTC = "XBTUSD"
PARAMS = {"lookback": 28, "top_k": 3, "filter_sma": 100, "rebalance_days": 7}  # fixed: RESEARCH.md #7
START_CASH = 1000.0
COSTS = COST_PRESETS["taker"]
DAY = 86400


def now_ts() -> int:
    return int(time.time())


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ─────────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────────

def completed_closes(pair: str, now: int) -> list:
    """Daily closes of completed candles only (Kraken's last daily candle is still forming)."""
    candles = kc.get_ohlc(pair, interval=1440)
    return [float(c[4]) for c in candles if int(c[0]) + DAY <= now]


def last_price(pair: str) -> float | None:
    t = kc.get_ticker(pair)
    try:
        return float(t["c"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


# ─────────────────────────────────────────────
# LEDGER
# ─────────────────────────────────────────────

def load() -> dict | None:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return None


def save(ledger: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2))
    tmp.replace(LEDGER)


def value(ledger: dict, prices: dict) -> float:
    total = ledger["cash"]
    for pair, units in ledger["holdings"].items():
        px = prices.get(pair) or ledger["last_prices"].get(pair, 0.0)
        total += units * px
    return total


# ─────────────────────────────────────────────
# ONE RUN
# ─────────────────────────────────────────────

def run(now: int | None = None) -> dict:
    """Mark to market and, if due, rebalance. Returns the updated ledger."""
    now = now or now_ts()
    ledger = load()
    prices = {p: last_price(p) for p in UNIVERSE}
    if not prices.get(BTC):
        raise SystemExit("Could not get the BTC price from Kraken; nothing changed.")

    if ledger is None:
        ledger = {
            "idea": "RESEARCH.md #7 — momentum + BTC filter, forward paper trading",
            "params": PARAMS, "costs": {"fee": COSTS.fee, "slippage": COSTS.slippage},
            "started": now, "start_value": START_CASH, "btc_start_price": prices[BTC],
            "cash": START_CASH, "holdings": {}, "last_prices": {}, "last_rebalance": None,
            "trades": [], "weeks": [],
        }
        print(f"  Started paper record at {iso(now)} with ${START_CASH:,.0f}; BTC ${prices[BTC]:,.0f}")

    ledger["last_prices"].update({p: px for p, px in prices.items() if px})
    # Weekly: rebalance once at least 6 days have passed. The scheduled run is
    # always 7 days after the previous one, while extra runs in between (or a
    # first run at a different time of day) only mark to market.
    due = ledger["last_rebalance"] is None or now - ledger["last_rebalance"] >= (PARAMS["rebalance_days"] - 1) * DAY

    decision = None
    if due:
        closes = {p: completed_closes(p, now) for p in UNIVERSE}
        btc_closes = closes.get(BTC) or []
        market_ok = strategies.market_filter_ok(btc_closes, PARAMS["filter_sma"])
        scores = {p: strategies.momentum_score(c, PARAMS["lookback"]) for p, c in closes.items()
                  if c and prices.get(p)}
        picks = strategies.momentum_picks(scores, PARAMS["top_k"], market_ok)
        btc_sma = strategies.sma(btc_closes, PARAMS["filter_sma"]) if len(btc_closes) >= PARAMS["filter_sma"] else None
        decision = {"market_ok": market_ok, "btc_close": btc_closes[-1] if btc_closes else None,
                    "btc_sma": btc_sma, "picks": picks,
                    "top_scores": sorted(((round(s, 4), p) for p, s in scores.items() if s is not None),
                                         reverse=True)[:6]}
        rebalance(ledger, picks, prices, now)
        ledger["last_rebalance"] = now

    v = value(ledger, prices)
    btc_v = ledger["start_value"] * prices[BTC] / ledger["btc_start_price"]
    snapshot = {"time": now, "date": iso(now), "value": round(v, 2), "btc_buy_hold": round(btc_v, 2),
                "holdings": sorted(ledger["holdings"]), "rebalanced": due}
    if decision:
        snapshot["decision"] = decision
    if due:
        ledger["weeks"].append(snapshot)
    else:
        ledger.setdefault("marks", []).append(snapshot)
    save(ledger)
    return ledger


def rebalance(ledger: dict, picks: list, prices: dict, now: int) -> None:
    """Move to equal weights in `picks` (1/top_k each, rest cash), paying fees + slippage."""
    total = value(ledger, prices)
    target = {p: total / PARAMS["top_k"] for p in picks}
    holdings = ledger["holdings"]
    diffs = {}
    for pair in set(holdings) | set(target):
        px = prices.get(pair)
        if px:
            diffs[pair] = target.get(pair, 0.0) - holdings.get(pair, 0.0) * px
    # sells first, so the cash is there for the buys
    for pair, diff in sorted(diffs.items(), key=lambda kv: kv[1]):
        px = prices[pair]
        if abs(diff) < 0.01:
            continue
        side = "buy" if diff > 0 else "sell"
        fill = px * (1 + COSTS.slippage) if side == "buy" else px * (1 - COSTS.slippage)
        units = abs(diff) / px
        fee = units * fill * COSTS.fee
        if side == "buy":
            ledger["cash"] -= units * fill + fee
            holdings[pair] = holdings.get(pair, 0.0) + units
        else:
            units = min(units, holdings.get(pair, 0.0))
            ledger["cash"] += units * fill - fee
            holdings[pair] = holdings.get(pair, 0.0) - units
        ledger["trades"].append({"time": now, "pair": pair, "side": side, "units": units,
                                 "price": round(fill, 8), "fee": round(fee, 4)})
    ledger["holdings"] = {p: u for p, u in holdings.items() if u * (prices.get(p) or 0) >= 0.01}


# ─────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────

def summary(ledger: dict) -> dict:
    marks = ledger["weeks"] + ledger.get("marks", [])
    last = max(marks, key=lambda m: m["time"]) if marks else None
    weeks = (last["time"] - ledger["started"]) / (7 * DAY) if last else 0.0
    v = last["value"] if last else ledger["start_value"]
    b = last["btc_buy_hold"] if last else ledger["start_value"]
    return {"weeks": weeks, "value": v, "btc_buy_hold": b,
            "return": v / ledger["start_value"] - 1, "btc_return": b / ledger["start_value"] - 1,
            "trades": len(ledger["trades"]), "holdings": sorted(ledger["holdings"]),
            "last_decision": next((w.get("decision") for w in reversed(ledger["weeks"]) if w.get("decision")), None),
            "as_of": last["date"] if last else None}


def print_status(ledger: dict) -> None:
    s = summary(ledger)
    print(f"\n  ══ PAPER MOMENTUM (#7) · started {iso(ledger['started'])} · {s['weeks']:.1f} weeks ══")
    print(f"  Paper portfolio ${s['value']:,.2f} ({s['return']*100:+.2f}%) vs BTC buy & hold "
          f"${s['btc_buy_hold']:,.2f} ({s['btc_return']*100:+.2f}%) · as of {s['as_of']}")
    print(f"  Holding: {', '.join(s['holdings']) or 'cash'} · {s['trades']} paper trades so far")
    d = s["last_decision"]
    if d:
        f = "ON (BTC above its 100-day average)" if d["market_ok"] else "OFF — all cash (BTC below its 100-day average)"
        print(f"  Last rebalance: market filter {f}; picks {', '.join(d['picks']) or 'none'}")
        print(f"  Top 28-day returns: " + ", ".join(f"{p} {sc*100:+.1f}%" for sc, p in d["top_scores"]))
    if s["weeks"] >= 13 and s["weeks"] < 26:
        behind = s["btc_return"] - s["return"]
        stop = behind > 0.10 or s["return"] < -0.20
        print(f"  Week-13 check: {'STOP — ' if stop else 'continue — '}"
              f"{behind*100:+.1f} pts vs BTC, {s['return']*100:+.1f}% overall")
    elif s["weeks"] >= 26:
        ok = s["return"] > s["btc_return"] and s["return"] > 0
        print(f"  Week-26 decision: {'PASS — consider a small, capped real test' if ok else 'FAIL — do not trade it'}")
    else:
        print(f"  Decision points: week 13 (early stop) and week 26 (decision) — see RESEARCH.md #7")


# ─────────────────────────────────────────────
# DEMO (never touches the real ledger)
# ─────────────────────────────────────────────

def demo(now: int | None = None, max_days_back: int = 600) -> dict | None:
    """
    Find the most recent past day on which the #7 rules would have bought,
    run the same rebalance on a throwaway ledger at that day's Kraken closing
    prices, and value it at today's prices. Nothing is saved.
    """
    now = now or now_ts()
    history = {}
    for p in UNIVERSE:
        candles = kc.get_ohlc(p, interval=1440)
        history[p] = [(int(c[0]), float(c[4])) for c in candles if int(c[0]) + DAY <= now]
    btc_hist = history.get(BTC) or []
    if not btc_hist:
        raise SystemExit("Could not get BTC history from Kraken.")

    for back in range(1, max_days_back):
        day_close = btc_hist[-1][0] - (back - 1) * DAY   # candle open time of the day we decide on
        upto = {p: [c for t, c in h if t <= day_close] for p, h in history.items()}
        btc = upto[BTC]
        if len(btc) < PARAMS["filter_sma"] + 1:
            break
        market_ok = strategies.market_filter_ok(btc, PARAMS["filter_sma"])
        scores = {p: strategies.momentum_score(c, PARAMS["lookback"]) for p, c in upto.items() if c}
        picks = strategies.momentum_picks(scores, PARAMS["top_k"], market_ok)
        if not picks:
            continue
        then_prices = {p: c[-1] for p, c in upto.items() if c}
        ledger = {"cash": START_CASH, "holdings": {}, "last_prices": dict(then_prices), "trades": []}
        rebalance(ledger, picks, then_prices, day_close + DAY)
        today = {p: last_price(p) for p in UNIVERSE}
        start_value = value(ledger, then_prices)
        end_value = value(ledger, today)
        btc_change = today[BTC] / then_prices[BTC] - 1 if today.get(BTC) else None
        return {"date": iso(day_close + DAY), "days_ago": back, "picks": picks, "trades": ledger["trades"],
                "scores": sorted(((round(s, 4), p) for p, s in scores.items() if s is not None), reverse=True)[:6],
                "btc_vs_avg": (btc[-1], strategies.sma(btc, PARAMS["filter_sma"])),
                "value_after_buy": start_value, "value_today": end_value, "btc_change": btc_change,
                "holdings_today": {p: (u, today.get(p)) for p, u in ledger["holdings"].items()}}
    return None


def print_demo(d: dict | None) -> None:
    print("\n  ══ DEMO — same rules and code as the paper record, NOT recorded, no real orders ══")
    if not d:
        print("  The rules would not have bought on any day in the available history.")
        return
    close, avg = d["btc_vs_avg"]
    print(f"  Most recent buy signal: {d['date']} ({d['days_ago']} day(s) ago)")
    print(f"  BTC {close:,.0f} above its 100-day average {avg:,.0f}; top 28-day returns: "
          + ", ".join(f"{p} {s*100:+.1f}%" for s, p in d["scores"]))
    print(f"  Paper buys with $1,000 (fee 0.40% + slippage 0.05%):")
    for t in d["trades"]:
        print(f"    {t['side'].upper():4} {t['units']:.6g} {t['pair']} @ ${t['price']:,.6g}  (fee ${t['fee']:.2f})")
    change = d["value_today"] / START_CASH - 1
    btc = f"{d['btc_change']*100:+.2f}%" if d["btc_change"] is not None else "n/a"
    print(f"  Worth after buying: ${d['value_after_buy']:,.2f} · worth today: ${d['value_today']:,.2f} "
          f"({change*100:+.2f}%) · BTC over the same days: {btc}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true", help="show the record without changing it")
    ap.add_argument("--demo", action="store_true",
                    help="show what the rules last bought, on a throwaway ledger (nothing is recorded)")
    args = ap.parse_args()
    if args.demo:
        print_demo(demo())
        return
    if args.status:
        ledger = load()
        if not ledger:
            raise SystemExit("No paper record yet. Run: python3 paper_momentum.py")
        print_status(ledger)
        return
    print_status(run())


if __name__ == "__main__":
    main()
