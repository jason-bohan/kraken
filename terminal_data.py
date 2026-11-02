#!/usr/bin/env python3
"""
terminal_data.py — data behind each terminal screen (goobtrademaster.py).

Every screen is a function returning a View: a title, table columns and rows,
an optional note and an optional price series for a chart. Cells are plain
values or (text, tone) pairs, tone being "up", "down", "warn" or "dim".
No UI code here, so screens can be tested without a terminal.

All screens only READ: nothing here places or cancels orders.
"""

import json
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import kraken_connection as kc
from bot_registry import BOTS
from position_store import all_bot_positions

ROOT = Path(__file__).resolve().parent
WATCHLIST = ["XBTUSD", "ETHUSD", "SOLUSD", "ADAUSD", "XDGUSD", "XRPUSD", "DOTUSD", "LINKUSD", "AVAXUSD"]
NAMES = {"XBTUSD": "BTC", "XDGUSD": "DOGE", "XXBT": "BTC", "XETH": "ETH", "XXDG": "DOGE", "XDG": "DOGE",
         "ZUSD": "USD", "XXRP": "XRP", "XLTC": "LTC", "XXLM": "XLM", "XZEC": "ZEC", "XXMR": "XMR"}


@dataclass
class View:
    title: str
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    note: str = ""
    series: list = field(default_factory=list)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def money(x: float) -> str:
    if x is None:
        return "—"
    a = abs(x)
    if a >= 1000:
        return f"{x:,.0f}"
    if a >= 1:
        return f"{x:,.2f}"
    if a >= 0.01:
        return f"{x:.4f}"
    return f"{x:.8f}".rstrip("0")


def signed_pct(x: float | None, digits: int = 2) -> tuple:
    if x is None:
        return ("—", "dim")
    return (f"{x*100:+.{digits}f}%", "up" if x > 0 else "down" if x < 0 else None)


def signed_usd(x: float) -> tuple:
    return (f"{'+' if x >= 0 else '-'}${abs(x):,.2f}", "up" if x > 0 else "down" if x < 0 else None)


def short_name(pair: str) -> str:
    alt = kc.pair_altname(pair)
    if alt in NAMES:
        return NAMES[alt]
    return alt[:-3] if alt.endswith("USD") else alt


def ago(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds/60:.0f}m"
    if seconds < 172800:
        return f"{seconds/3600:.0f}h"
    return f"{seconds/86400:.0f}d"


def _tickers_by_altname() -> dict:
    """{altname: ticker} for every pair."""
    pairs = kc.get_asset_pairs()
    return {pairs.get(k, {}).get("altname", k): v for k, v in kc.get_all_tickers().items()}


def _last(t: dict) -> float:
    return float(t.get("c", [0])[0] or 0)


def _day_change(t: dict) -> float | None:
    o = float(t.get("o", 0) or 0)
    return _last(t) / o - 1 if o else None


# ─────────────────────────────────────────────
# MARKET SCREENS
# ─────────────────────────────────────────────

def market(_arg: str = "") -> View:
    """MKT — watchlist plus anything a bot holds."""
    tickers = _tickers_by_altname()
    if not tickers:
        return View("MKT  Crypto markets", note="Kraken ticker unavailable (network?)")
    pairs = list(WATCHLIST)
    for positions in all_bot_positions().values():
        for p in positions:
            alt = kc.pair_altname(p)
            if alt not in pairs:
                pairs.append(alt)
    rows = []
    for p in pairs:
        t = tickers.get(p)
        if not t:
            continue
        last = _last(t)
        vol_usd = float(t["v"][1]) * float(t["p"][1])
        rows.append([short_name(p), money(last), signed_pct(_day_change(t)),
                     money(float(t["l"][1])), money(float(t["h"][1])), f"${vol_usd/1e6:,.1f}M"])
    return View("MKT  Crypto markets", ["Coin", "Last", "Day %", "24h low", "24h high", "24h vol"], rows,
                note="Day % is since 00:00 UTC (Kraken's daily open). Source: Kraken, live.")


def movers(_arg: str = "", min_volume_usd: float = 1_000_000, n: int = 10) -> View:
    """MOVR — biggest gainers and losers among liquid USD pairs."""
    tickers = _tickers_by_altname()
    usd = []
    for alt, t in tickers.items():
        if not alt.endswith("USD") or alt.endswith(("USDUSD", "USDTUSD", "USDCUSD")):
            continue
        try:
            vol_usd = float(t["v"][1]) * float(t["p"][1])
            chg = _day_change(t)
        except (KeyError, ValueError, IndexError):
            continue
        if chg is not None and vol_usd >= min_volume_usd:
            usd.append((alt, chg, _last(t), vol_usd))
    usd.sort(key=lambda r: r[1], reverse=True)
    picks = usd[:n] + [("", None, None, None)] + usd[-n:] if len(usd) > 2 * n else usd
    rows = [["", "", "", ""] if not alt else
            [short_name(alt), money(last), signed_pct(chg), f"${vol/1e6:,.1f}M"]
            for alt, chg, last, vol in picks]
    return View("MOVR  Top movers (USD pairs, >$1M volume)", ["Coin", "Last", "Day %", "24h vol"], rows,
                note=f"{len(usd)} liquid USD pairs. Source: Kraken, live.")


def chart(arg: str = "XBTUSD") -> View:
    """CHRT <pair> — 7 days of hourly closes."""
    pair = (arg or "XBTUSD").upper()
    if not pair.endswith("USD"):
        pair = {"BTC": "XBTUSD", "DOGE": "XDGUSD"}.get(pair, pair + "USD")
    candles = kc.get_ohlc(pair, interval=60)[-168:]
    if not candles:
        return View(f"CHRT  {pair}", note=f"No candles for {pair}")
    closes = [float(c[4]) for c in candles]
    hi, lo = max(float(c[2]) for c in candles), min(float(c[3]) for c in candles)
    rows = [["Last", money(closes[-1])], ["7d change", signed_pct(closes[-1] / closes[0] - 1)],
            ["7d high", money(hi)], ["7d low", money(lo)],
            ["From high", signed_pct(closes[-1] / hi - 1)]]
    return View(f"CHRT  {short_name(pair)} · 7 days, hourly", ["", ""], rows, series=closes,
                note="Source: Kraken OHLC.")


# ─────────────────────────────────────────────
# BOT SCREENS
# ─────────────────────────────────────────────

def positions(_arg: str = "") -> View:
    """POS — every open bot position with live P&L and distance to its stop."""
    all_pos = all_bot_positions()
    tickers = _tickers_by_altname()
    rows, total_pnl, total_cost = [], 0.0, 0.0
    for bot, pos_map in sorted(all_pos.items()):
        for pair, p in pos_map.items():
            alt = kc.pair_altname(pair)
            price = _last(tickers[alt]) if alt in tickers else None
            entry, vol = p.get("entry_price", 0), p.get("volume", 0)
            eo = p.get("exit_orders") or {}
            stop = p.get("stop_loss", eo.get("sl_price"))
            target = p.get("take_profit", eo.get("tp_price"))
            protected = bool(p.get("sl_txid") or eo.get("sl"))
            if price:
                pnl = (price - entry) * vol - p.get("fees", 0)
                total_pnl += pnl
                total_cost += entry * vol
            rows.append([
                bot.replace("stock_swing_bot_", "swing ").replace("dynamic_hft_bot", "hft"),
                short_name(alt), money(vol), money(entry), money(price) if price else "—",
                signed_pct(price / entry - 1 if price and entry else None),
                signed_usd(pnl) if price else ("—", "dim"),
                money(stop) if stop else "—", money(target) if target else "—",
                ("yes", "up") if protected else ("NO STOP", "down"),
            ])
    note = (f"{len(rows)} open · unrealized {signed_usd(total_pnl)[0]} on ${total_cost:,.2f}"
            if rows else "No open bot positions (state/*.json).")
    return View("POS  Bot positions", ["Bot", "Coin", "Size", "Entry", "Last", "P&L %", "P&L $",
                                       "Stop", "Target", "SL on Kraken"], rows, note=note)


def performance(_arg: str = "", db_path: Path | None = None) -> View:
    """PNL — realized results per bot from learning.db, plus recent trades."""
    db = db_path or ROOT / "learning.db"
    if not db.exists():
        return View("PNL  Realized performance", note="No learning.db yet.")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        summary = con.execute(
            """SELECT bot_name, COUNT(*), SUM(pnl_amount > 0), SUM(pnl_amount), AVG(pnl_pct), MAX(exit_time)
               FROM trade_log WHERE status='closed' GROUP BY bot_name ORDER BY SUM(pnl_amount) DESC"""
        ).fetchall()
        recent = con.execute(
            """SELECT exit_time, bot_name, symbol, pnl_pct, pnl_amount, exit_reason
               FROM trade_log WHERE status='closed' ORDER BY exit_time DESC LIMIT 12"""
        ).fetchall()
    finally:
        con.close()
    rows = [[bot, n, f"{(wins or 0)/n*100:.0f}%", signed_usd(total or 0), signed_pct(avg or 0),
             (last or "")[:10]] for bot, n, wins, total, avg, last in summary]
    if recent:
        rows.append(["", "", "", "", "", ""])
        rows.append([("Recent trades", "dim"), "", "", "", "", ""])
        rows += [[(ex or "")[:16].replace("T", " "), f"{bot[:12]} {short_name(sym) if sym.endswith('USD') else sym}",
                  "", signed_usd(amt or 0), signed_pct(pct or 0), reason or ""]
                 for ex, bot, sym, pct, amt, reason in recent]
    return View("PNL  Realized performance", ["Bot / time", "Trades", "Win %", "Net $", "Avg %", "Last"],
                rows, note="Trades closed before Nov 2026 were recorded before fees.")


def _running_screens() -> set | None:
    try:
        out = subprocess.run(["screen", "-ls"], capture_output=True, text=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    names = set()
    for line in out.splitlines():
        line = line.strip()
        if "." in line and ("Detached" in line or "Attached" in line):
            names.add(line.split(".", 1)[1].split()[0])
    return names


def _last_line(path: Path) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4096))
            lines = [ln for ln in f.read().decode(errors="replace").splitlines() if ln.strip()]
        return lines[-1].strip() if lines else ""
    except OSError:
        return ""


def bots(_arg: str = "") -> View:
    """BOTS — is each bot running, and what did it last say?"""
    running = _running_screens()
    now = time.time()
    rows = []
    for name, b in BOTS.items():
        log = ROOT / b["log"]
        age = now - log.stat().st_mtime if log.exists() else None
        if running is None:
            status = ("?", "dim")
        else:
            status = ("RUNNING", "up") if name in running else ("DOWN", "down")
        stale = age is not None and age > 600
        rows.append([b["label"], status, (ago(age), "warn" if stale else None) if age is not None else ("no log", "dim"),
                     _last_line(log)[:70] if log.exists() else ""])
    note = "screen is not installed here, so run state is unknown." if running is None else \
        "Restarts are handled by bot_monitor_cron.py. LOG <bot> shows a full log."
    return View("BOTS  Bot health", ["Bot", "Status", "Last log", "Last line"], rows, note=note)


def log_tail(arg: str = "", lines: int = 40) -> View:
    """LOG <bot> — the end of a bot's log (e.g. LOG HFT, LOG BTC)."""
    key = (arg or "HFT").upper()
    name = next((n for n in BOTS if n.upper() in (key, f"BOT_{key}")), None)
    if not name:
        return View("LOG", note=f"Unknown bot {arg!r}. Try: " + ", ".join(n.replace("bot_", "") for n in BOTS))
    path = ROOT / BOTS[name]["log"]
    if not path.exists():
        return View(f"LOG  {BOTS[name]['label']}", note=f"{path.name} does not exist yet.")
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 16384))
        text = f.read().decode(errors="replace").splitlines()[-lines:]
    return View(f"LOG  {BOTS[name]['label']}", ["Line"], [[ln] for ln in text], note=str(path.relative_to(ROOT)))


# ─────────────────────────────────────────────
# ACCOUNT SCREENS (need the Kraken API key)
# ─────────────────────────────────────────────

def balances(_arg: str = "") -> View:
    """BAL — account balances valued in USD."""
    bal = kc.get_balance()
    if not bal:
        return View("BAL  Balances", note="Balance unavailable (API key missing or request failed).")
    tickers = _tickers_by_altname()
    pairs = kc.get_asset_pairs()
    usd_price = {info.get("base"): _last(tickers.get(info.get("altname"), {}))
                 for info in pairs.values() if info.get("quote") == "ZUSD"}
    rows, total = [], 0.0
    for asset, amount in bal.items():
        amt = float(amount)
        if amt <= 0:
            continue
        base = asset.split(".")[0]  # staked/earn variants like "SOL.F"
        price = 1.0 if base in ("ZUSD", "USD", "USDC", "USDT") else usd_price.get(base) or usd_price.get("X" + base)
        value = amt * price if price else None
        total += value or 0
        rows.append([NAMES.get(asset, asset), money(amt), money(price) if price else "—",
                     f"${value:,.2f}" if value is not None else "—"])
    rows.sort(key=lambda r: -float(r[3].strip("$").replace(",", "")) if r[3] != "—" else 0)
    return View("BAL  Balances", ["Asset", "Amount", "Price", "Value"], rows, note=f"Total ≈ ${total:,.2f}")


def orders(_arg: str = "") -> View:
    """ORD — open orders on Kraken."""
    try:
        open_orders = kc.get_open_orders()
    except Exception as e:
        return View("ORD  Open orders", note=f"Unavailable: {e}")
    owners = {1: "swing", 8: "hft"}
    rows = []
    for txid, o in sorted(open_orders.items(), key=lambda kv: kv[1].get("opentm", 0), reverse=True):
        d = o.get("descr", {})
        rows.append([short_name(d.get("pair", "")), (d.get("type", ""), "up" if d.get("type") == "buy" else "down"),
                     d.get("ordertype", ""), money(float(o.get("vol", 0))), d.get("price", ""),
                     owners.get(o.get("userref"), "manual/cron"), ago(time.time() - o.get("opentm", time.time())),
                     txid])
    return View("ORD  Open orders", ["Coin", "Side", "Type", "Volume", "Price", "Owner", "Age", "Order id"], rows,
                note=f"{len(rows)} open order(s).")


# ─────────────────────────────────────────────
# SENTIMENT + RESEARCH
# ─────────────────────────────────────────────

def sentiment(_arg: str = "") -> View:
    """SENT — the market filters the bots use before buying."""
    from market_sentiment import get_market_sentiment, should_buy
    from onchain_signals import get_onchain_signals
    s = get_market_sentiment()
    oc = get_onchain_signals()
    d = should_buy(s)
    fg = s.get("fear_greed", 50)
    rows = [
        ["Fear & Greed", (f"{fg} {s.get('fear_greed_label', '')}", "down" if fg < 30 else "up" if fg > 55 else None)],
        ["Market cap 24h", signed_pct(s.get("market_cap_change_24h", 0) / 100)],
        ["BTC dominance", f"{s.get('btc_dominance', 0):.1f}%"],
        ["Funding BTC", f"{oc.get('funding', {}).get('BTC', {}).get('rate', 0)*100:+.4f}%"],
        ["Funding ETH", f"{oc.get('funding', {}).get('ETH', {}).get('rate', 0)*100:+.4f}%"],
        ["Long/short ratio", f"{oc.get('avg_ls_ratio', 1):.2f}"],
        ["Open interest 24h", signed_pct(oc.get("avg_oi_change", 0) / 100)],
        ["", ""],
        ["Bots may buy?", ("YES", "up") if d["allow"] else ("BLOCKED", "down")],
        ["Size multiplier", f"{d['size_multiplier']:.2f}x"],
        ["Reason", d["reason"]],
    ]
    note = f"Sentiment: {s.get('source')}" + (f" — {s['error']}" if s.get("error") else "") + \
        f" · Derivatives: OKX ({oc.get('source')})"
    return View("SENT  Sentiment & derivatives", ["Signal", "Value"], rows, note=note)


def backtests(arg: str = "", report_dir: Path | None = None) -> View:
    """BT — results of the latest backtest run (python3 backtester.py ...)."""
    d = report_dir or ROOT / "data" / "backtests"
    files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime) if d.exists() else []
    if arg:
        files = [f for f in files if f.name.startswith(arg.lower())]
    if not files:
        return View("BT  Backtests", note="No reports yet. Run e.g.  python3 backtester.py hft --days 30")
    rep = json.loads(files[-1].read_text())
    rows, series = [], []
    for pair, r in rep.get("pairs", {}).items():
        m = r["metrics"]
        wf = (r.get("walk_forward") or {}).get("out_of_sample", {})
        rp = r.get("random_percentile")
        rows.append([short_name(pair), m["trades"], f"{m['win_rate']*100:.0f}%", signed_pct(m["total_return"]),
                     signed_pct(m["buy_hold"]), f"{rp*100:.0f}%" if rp is not None else "—",
                     signed_pct(wf.get("total_return")) if wf else ("—", "dim"),
                     signed_pct(r["costs_doubled"]["total_return"]),
                     (r["verdict"], "up" if r["verdict"].startswith("Passes") else "warn")])
        if not series and r.get("equity"):
            series = [pt[1] for pt in r["equity"]]
    c = rep.get("costs", {})
    note = (f"{files[-1].name} · {rep.get('strategy')} · {rep.get('days')}d · fee {c.get('fee', 0)*100:.2f}% "
            f"+ slip {c.get('slippage', 0)*100:.2f}% per side · chart = first pair's equity")
    return View("BT  Backtest results", ["Coin", "Trades", "Win", "Net", "Buy&hold", "Beats random",
                                         "Walk-fwd", "2x costs", "Verdict"], rows, note=note, series=series)


# ─────────────────────────────────────────────
# REGISTRY
# ─────────────────────────────────────────────

SCREENS = {
    # code: (function, refresh seconds, description)
    "MKT":  (market, 10, "Crypto watchlist: price, day change, 24h range and volume"),
    "MOVR": (movers, 30, "Top gainers and losers among liquid USD pairs"),
    "CHRT": (chart, 60, "7-day hourly chart, e.g. CHRT SOL"),
    "POS":  (positions, 10, "Open bot positions with live P&L, stop and target"),
    "PNL":  (performance, 60, "Realized results per bot and recent trades"),
    "BOTS": (bots, 15, "Is each bot running, and its last log line"),
    "LOG":  (log_tail, 5, "End of a bot's log, e.g. LOG HFT"),
    "BAL":  (balances, 30, "Account balances in USD (API key)"),
    "ORD":  (orders, 15, "Open orders on Kraken (API key)"),
    "SENT": (sentiment, 300, "Fear & Greed, funding and the bots' buy gate"),
    "BT":   (backtests, 60, "Latest backtest report, e.g. BT hft"),
}


def help_view(_arg: str = "") -> View:
    rows = [[code, desc] for code, (_, _, desc) in SCREENS.items()]
    rows += [["", ""], ["F1–F4", "Pick the panel a code loads into (or type  2 MKT)"],
             ["Ctrl+R", "Refresh all panels"], ["Ctrl+Q", "Quit"]]
    return View("HELP  Function codes", ["Code", "What it shows"], rows,
                note="Type a code and press Enter. Read-only: the terminal never places orders.")


SCREENS["HELP"] = (help_view, 0, "This list")
