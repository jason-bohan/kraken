#!/usr/bin/env python3
"""
GooB Trade Master — Kraken Trading Dashboard

Usage:
    python3 goobtrademaster.py

Keys:
    R     — refresh data now
    Q     — quit (stops all running bots cleanly)
"""

import os
import sys
import queue
import subprocess
import threading
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Header, Footer, Static, RichLog, Button, Label, Rule
from textual.reactive import reactive

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kraken_connection import get_balance, get_ticker

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Bot registry ─────────────────────────────────────────────────────────────

BOTS = [
    {
        "id":        "btc_momentum",
        "name":      "BTC Momentum",
        "script":    "btc_momentum_bot.py",
        "live_args": ["--monitor"],
        "dry_args":  ["--dry"],
        "pair":      "XBTUSD",
        "desc":      "10% target / 5% stop  |  RSI + volume momentum",
    },
    {
        "id":        "btc_swing",
        "name":      "BTC Swing",
        "script":    "btc_swing_bot.py",
        "live_args": [],
        "dry_args":  ["--dry"],
        "pair":      "XBTUSD",
        "desc":      "8% target / 4% stop   |  dip + RSI entries",
    },
    {
        "id":        "eth_swing",
        "name":      "ETH Swing",
        "script":    "eth_swing_bot.py",
        "live_args": [],
        "dry_args":  ["--dry"],
        "pair":      "ETHUSD",
        "desc":      "10% target / 5% stop  |  dip + RSI entries",
    },
    {
        "id":        "sol_swing",
        "name":      "SOL Swing",
        "script":    "sol_swing_bot.py",
        "live_args": [],
        "dry_args":  ["--dry"],
        "pair":      "SOLUSD",
        "desc":      "10% target / 5% stop  |  dip + RSI entries",
    },
    {
        "id":        "dynamic_hft",
        "name":      "Dynamic HFT",
        "script":    "dynamic_hft_bot.py",
        "live_args": [],
        "dry_args":  ["--dry"],
        "pair":      "XBTUSD",
        "desc":      "8% target / 4% stop   |  top volatile pairs across all of Kraken",
    },
    {
        "id":        "correlation",
        "name":      "Correlation",
        "script":    "correlation_bot.py",
        "live_args": [],
        "dry_args":  ["--dry"],
        "pair":      "XBTUSD",
        "desc":      "8% target / 4% stop   |  buys when 2+ coins oversold simultaneously",
    },
    {
        "id":        "cheap_futures",
        "name":      "Cheap Futures",
        "script":    "cheap_futures_bot.py",
        "live_args": ["--scan"],
        "dry_args":  ["--scan", "--dry"],
        "pair":      "SOLUSD",
        "desc":      "15% target / 3% stop  |  SOL/ADA/DOGE volatility scanner",
    },
]

# ─── Bot process management ───────────────────────────────────────────────────

class BotProcess:
    """Wraps a running bot subprocess and drains its stdout into a queue."""

    def __init__(self, bot_id: str, proc: subprocess.Popen, mode: str):
        self.bot_id   = bot_id
        self.proc     = proc
        self.mode     = mode
        self.started  = datetime.now().strftime("%H:%M:%S")
        self.last_line = ""
        self.last_seen = ""
        self._q: queue.Queue = queue.Queue(maxsize=200)
        t = threading.Thread(target=self._drain, daemon=True)
        t.start()

    def _drain(self):
        for line in self.proc.stdout:
            stripped = line.rstrip()
            if stripped:
                self.last_line = stripped
                self.last_seen = datetime.now().strftime("%H:%M:%S")
                try:
                    self._q.put_nowait(stripped)
                except queue.Full:
                    try:
                        self._q.get_nowait()
                        self._q.put_nowait(stripped)
                    except queue.Empty:
                        pass

    def running(self) -> bool:
        return self.proc.poll() is None

    def pop_output(self) -> list:
        lines = []
        try:
            while True:
                lines.append(self._q.get_nowait())
        except queue.Empty:
            pass
        return lines

    def stop(self):
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# bot_id -> BotProcess | None
_procs = {b["id"]: None for b in BOTS}


def start_bot(bot_id: str, dry: bool = False) -> bool:
    if _procs[bot_id] and _procs[bot_id].running():
        return False
    bot  = next(b for b in BOTS if b["id"] == bot_id)
    args = bot["dry_args"] if dry else bot["live_args"]
    cmd  = [sys.executable, os.path.join(SCRIPT_DIR, bot["script"])] + args
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=SCRIPT_DIR, text=True, bufsize=1,
    )
    _procs[bot_id] = BotProcess(bot_id, proc, "dry" if dry else "live")
    return True


def stop_bot(bot_id: str) -> bool:
    bp = _procs[bot_id]
    if not bp or not bp.running():
        return False
    bp.stop()
    _procs[bot_id] = None
    return True


def bot_running(bot_id: str) -> bool:
    bp = _procs[bot_id]
    return bool(bp and bp.running())


# ─── Widgets ──────────────────────────────────────────────────────────────────

class BotCard(Static):
    """Card showing one bot's status and controls."""

    def __init__(self, bot: dict, **kwargs):
        super().__init__(**kwargs)
        self.bot    = bot
        self.bot_id = bot["id"]

    def compose(self) -> ComposeResult:
        bid = self.bot_id
        yield Label("[bold]" + self.bot["name"] + "[/bold]  [dim]" + self.bot["desc"] + "[/dim]")
        yield Label("  Stopped", id="status_" + bid)
        yield Label("", id="heartbeat_" + bid, classes="heartbeat")
        with Horizontal(classes="btn-row"):
            yield Button("Live",    id="live_" + bid, variant="success")
            yield Button("Dry Run", id="dry_"  + bid, variant="primary")
            yield Button("Stop",    id="stop_" + bid, variant="error")

    def refresh_status(self):
        bp      = _procs[self.bot_id]
        status  = self.query_one("#status_"    + self.bot_id, Label)
        heartbeat = self.query_one("#heartbeat_" + self.bot_id, Label)
        if bp and bp.running():
            if bp.mode == "live":
                status.update("[green]  LIVE  since " + bp.started + "[/green]")
            else:
                status.update("[cyan]  DRY RUN  since " + bp.started + "[/cyan]")
            if bp.last_line:
                # Strip emoji/whitespace clutter, cap length
                clean = bp.last_line.strip().lstrip("  ").replace("  ", " ")
                heartbeat.update(
                    "[dim]" + bp.last_seen + "  " + clean[:65] + "[/dim]"
                )
        else:
            status.update("  Stopped")
            heartbeat.update("")


class MarketPanel(Static):
    """Live BTC market data."""

    def compose(self) -> ComposeResult:
        yield Label("[bold]MARKET[/bold]")
        yield Rule()
        yield Label("Fetching...", id="mkt_btc")

    def refresh_data(self):
        try:
            t = get_ticker("XBTUSD")
            if t:
                price    = float(t["c"][0])
                open_24h = float(t["o"])
                chg      = (price - open_24h) / open_24h * 100
                color    = "green" if chg >= 0 else "red"
                sign     = "+" if chg >= 0 else ""
                ask      = float(t["a"][0])
                bid_p    = float(t["b"][0])
                spread   = ask - bid_p
                self.query_one("#mkt_btc", Label).update(
                    "BTC  [bold]$" + f"{price:,.2f}" + "[/bold]  "
                    "[" + color + "]" + sign + f"{chg:.2f}%" + " 24h[/" + color + "]  "
                    "[dim]spread $" + f"{spread:.2f}" + "[/dim]"
                )
            else:
                self.query_one("#mkt_btc", Label).update("[red]No ticker data[/red]")
        except Exception as e:
            self.query_one("#mkt_btc", Label).update("[red]" + str(e) + "[/red]")


class BalancesPanel(Static):
    """Account balances with USD equivalent totals."""

    ASSETS = [
        ("ZUSD",  "USD",  None),
        ("XXBT",  "BTC",  "XBTUSD"),
        ("XETH",  "ETH",  "ETHUSD"),
        ("SOL",   "SOL",  "SOLUSD"),
        ("XDG",   "DOGE", "XDGUSD"),
        ("DOT",   "DOT",  "DOTUSD"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("[bold]BALANCES[/bold]")
        yield Rule()
        yield Label("Fetching...", id="bal_data")

    def refresh_data(self):
        try:
            balances = get_balance()
            if not balances:
                self.query_one("#bal_data", Label).update("[red]Auth error — check .env[/red]")
                return

            lines      = []
            total_usd  = 0.0
            price_cache = {}

            for kraken_key, display, pair in self.ASSETS:
                amt = float(balances.get(kraken_key, 0))
                if amt < 1e-8:
                    continue

                if pair is None:
                    price = 1.0
                else:
                    if pair not in price_cache:
                        try:
                            tk = get_ticker(pair)
                            price_cache[pair] = float(tk["c"][0]) if tk else 0.0
                        except Exception:
                            price_cache[pair] = 0.0
                    price = price_cache[pair]

                usd_val    = amt * price
                total_usd += usd_val

                if display == "USD":
                    lines.append("USD   [bold]$" + f"{amt:,.2f}" + "[/bold]")
                else:
                    lines.append(
                        display.ljust(5) + " " + f"{amt:.6f}" +
                        "  [dim]approx $" + f"{usd_val:,.2f}" + "[/dim]"
                    )

            lines.append("")
            color = "green" if total_usd > 0 else "white"
            lines.append("[bold][" + color + "]Total  $" + f"{total_usd:,.2f}" + "[/" + color + "][/bold]")
            self.query_one("#bal_data", Label).update("\n".join(lines))

        except Exception as e:
            self.query_one("#bal_data", Label).update("[red]" + str(e) + "[/red]")


class PnlPanel(Static):
    """Realized P&L from Kraken trade history."""

    def compose(self) -> ComposeResult:
        yield Label("[bold]P&L  (recent trades)[/bold]")
        yield Rule()
        yield Label("Fetching...", id="pnl_data")

    def refresh_data(self):
        try:
            from kraken_connection import get_trade_history
            trades = get_trade_history(count=50)
            if not trades:
                self.query_one("#pnl_data", Label).update("[dim]No trade history[/dim]")
                return

            open_buys = {}
            realized  = []

            for t in sorted(trades, key=lambda x: x["time"]):
                pair = t["pair"]
                if t["type"] == "buy":
                    open_buys.setdefault(pair, []).append(t)
                elif t["type"] == "sell" and open_buys.get(pair):
                    buy = open_buys[pair].pop(0)
                    pnl = t["cost"] - t["fee"] - buy["cost"] - buy["fee"]
                    realized.append((pair, pnl, t["time"]))

            if not realized:
                self.query_one("#pnl_data", Label).update("[dim]No completed round-trips yet[/dim]")
                return

            total = sum(p for _, p, _ in realized)
            wins  = sum(1 for _, p, _ in realized if p > 0)
            lines = []

            for pair, pnl, ts in reversed(realized[-8:]):
                color = "green" if pnl >= 0 else "red"
                sign  = "+" if pnl >= 0 else ""
                dt    = datetime.fromtimestamp(ts).strftime("%m/%d %H:%M")
                lines.append(
                    "[" + color + "]" + sign + "$" + f"{pnl:.2f}" + "[/" + color + "]"
                    "  " + pair + "  [dim]" + dt + "[/dim]"
                )

            lines.append("")
            color_t = "green" if total >= 0 else "red"
            sign_t  = "+" if total >= 0 else ""
            lines.append(
                "[bold][" + color_t + "]" + sign_t + "$" + f"{total:.2f}" +
                " total[/" + color_t + "]  [dim]" +
                str(wins) + "/" + str(len(realized)) + " wins[/dim][/bold]"
            )
            self.query_one("#pnl_data", Label).update("\n".join(lines))

        except Exception as e:
            self.query_one("#pnl_data", Label).update("[red]" + str(e) + "[/red]")


# ─── App ──────────────────────────────────────────────────────────────────────

class GooB(App):

    TITLE = "GooB Trade Master"

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("q", "quit",    "Quit"),
    ]

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    #top_row {
        layout: horizontal;
        height: 1fr;
    }

    #left_col {
        width: 55;
        padding: 0 1;
        border-right: solid $primary-darken-2;
    }

    #bot_scroll {
        height: 1fr;
    }

    #right_col {
        width: 1fr;
        layout: vertical;
        padding: 0 1;
    }

    BotCard {
        border: solid $primary-darken-3;
        padding: 0 1 1 1;
        margin-bottom: 1;
        height: auto;
    }

    .heartbeat {
        color: $text-muted;
        height: 1;
        overflow: hidden;
    }

    .btn-row {
        height: 3;
        margin-top: 1;
    }

    Button {
        min-width: 10;
        margin-right: 1;
    }

    ScrollBar {
        width: 1;
    }

    ScrollBar > .scrollbar--vertical-bar {
        color: $primary-darken-2;
    }

    MarketPanel {
        height: auto;
        margin-bottom: 1;
    }

    BalancesPanel {
        height: auto;
        margin-bottom: 1;
    }

    PnlPanel {
        height: 1fr;
    }

    #log {
        height: 9;
        border-top: solid $primary-darken-2;
        padding: 0 1;
        background: $surface-darken-1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="top_row"):
            with Vertical(id="left_col"):
                yield Label("[bold]BOTS[/bold]")
                yield Rule()
                with ScrollableContainer(id="bot_scroll"):
                    for bot in BOTS:
                        yield BotCard(bot, id="card_" + bot["id"])
            with Vertical(id="right_col"):
                yield MarketPanel(id="market_panel")
                yield BalancesPanel(id="bal_panel")
                yield PnlPanel(id="pnl_panel")
        yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self._log("GooB Trade Master ready")
        self.refresh_all()
        self.set_interval(15, self.refresh_all)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.query_one("#log", RichLog).write("[dim]" + ts + "[/dim]  " + msg)

    # Lines containing these keywords bubble up to the log; everything else
    # is silently consumed and shown only in the card heartbeat.
    LOG_KEYWORDS = {
        "buy", "sell", "order", "profit", "stop loss", "target",
        "signal", "momentum", "pump", "position", "error", "failed",
        "exception", "cancelled", "filled",
    }

    def refresh_all(self):
        self.query_one("#market_panel", MarketPanel).refresh_data()
        self.query_one("#bal_panel",    BalancesPanel).refresh_data()
        self.query_one("#pnl_panel",    PnlPanel).refresh_data()
        for bot in BOTS:
            card = self.query_one("#card_" + bot["id"], BotCard)
            bp   = _procs[bot["id"]]
            if bp:
                for line in bp.pop_output():
                    lower = line.lower()
                    if any(kw in lower for kw in self.LOG_KEYWORDS):
                        self._log("[dim]" + bot["name"] + ":[/dim] " + line.strip())
            card.refresh_status()

    def action_refresh(self):
        self._log("Manual refresh")
        self.refresh_all()

    def action_quit(self):
        for bot in BOTS:
            stop_bot(bot["id"])
        self.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid_str = event.button.id

        for bot in BOTS:
            bid = bot["id"]

            if bid_str == "live_" + bid:
                if bot_running(bid):
                    self._log("[yellow]" + bot["name"] + " already running[/yellow]")
                else:
                    start_bot(bid, dry=False)
                    self._log("[green]" + bot["name"] + " started  LIVE[/green]")
                    self.query_one("#card_" + bid, BotCard).refresh_status()
                return

            if bid_str == "dry_" + bid:
                if bot_running(bid):
                    self._log("[yellow]" + bot["name"] + " running — stop first[/yellow]")
                else:
                    start_bot(bid, dry=True)
                    self._log("[cyan]" + bot["name"] + " started  DRY RUN[/cyan]")
                    self.query_one("#card_" + bid, BotCard).refresh_status()
                return

            if bid_str == "stop_" + bid:
                if stop_bot(bid):
                    self._log("[red]" + bot["name"] + " stopped[/red]")
                else:
                    self._log("[dim]" + bot["name"] + " wasn't running[/dim]")
                self.query_one("#card_" + bid, BotCard).refresh_status()
                return


if __name__ == "__main__":
    GooB().run()
