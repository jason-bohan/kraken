#!/usr/bin/env python3
"""
GooB Terminal — a read-only trading terminal for the Kraken bots.

Four panels, one command line. Type a function code and press Enter to load
it into the selected panel:

    MKT   markets          POS   bot positions     BAL   balances
    MOVR  top movers       PNL   realized results  ORD   open orders
    CHRT  chart (CHRT SOL) BOTS  bot health        SENT  sentiment gate
    BT    backtests        LOG   bot log (LOG HFT) HELP  all codes

    F1–F4   select a panel (or prefix a code: "3 CHRT ETH")
    Ctrl+R  refresh everything     Ctrl+Q  quit

The terminal only reads: it never places or cancels orders and never starts
bots (bot_monitor_cron.py does that). Screens refresh on their own.

Usage:
    python3 goobtrademaster.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.widgets import DataTable, Footer, Input, Sparkline, Static

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terminal_data as td  # noqa: E402

LAYOUT_FILE = Path(__file__).resolve().parent / "data" / "terminal_layout.json"
DEFAULT_LAYOUT = [("MKT", ""), ("POS", ""), ("PNL", ""), ("SENT", "")]

AMBER = "#ffb000"
TONES = {"up": "bold #3ddc84", "down": "bold #ff5f5f", "warn": "#ffd479", "dim": "#7a6233"}


def cell(value) -> Text:
    if isinstance(value, tuple):
        text, tone = value
        return Text(str(text), style=TONES.get(tone, ""))
    return Text("" if value is None else str(value))


class Panel(Vertical):
    """One of the four screens. Shows a View from terminal_data."""

    def __init__(self, index: int, code: str, arg: str = ""):
        super().__init__(id=f"panel{index}")
        self.index = index
        self.code = code
        self.arg = arg
        self.busy = False
        self.loaded_at = 0.0

    def compose(self) -> ComposeResult:
        yield Static("", classes="ptitle")
        yield Sparkline([], classes="spark")
        yield DataTable(zebra_stripes=True, cursor_type="row", show_cursor=False)
        yield Static("", classes="note")

    def on_mount(self) -> None:
        self.query_one(".spark").display = False
        self.set_title("loading…")

    def set_title(self, status: str = "") -> None:
        label = f" {self.index}  {self.code}{(' ' + self.arg) if self.arg else ''}"
        self.query_one(".ptitle", Static).update(
            Text.assemble((label, "bold"), ("   " + status, "#7a6233")))

    def show(self, view: td.View) -> None:
        self.busy = False
        self.loaded_at = time.time()
        title = Text(f" {self.index}  ", style="bold")
        title.append(view.title, style="bold")
        title.append(f"   {datetime.now().strftime('%H:%M:%S')}", style="#7a6233")
        self.query_one(".ptitle", Static).update(title)

        spark = self.query_one(".spark", Sparkline)
        spark.display = bool(view.series)
        if view.series:
            spark.data = view.series

        table = self.query_one(DataTable)
        table.clear(columns=True)
        if view.columns:
            table.add_columns(*[Text(c, style="bold") for c in view.columns])
            table.add_rows([[cell(v) for v in row] for row in view.rows])
        self.query_one(".note", Static).update(Text(view.note, style="#a07c2a"))

    def on_click(self) -> None:
        self.app.select_panel(self.index)


class GoobTerminal(App):
    TITLE = "GooB Terminal"
    CSS = f"""
    Screen {{ background: #000000; color: {AMBER}; }}
    #topbar {{ height: 1; background: {AMBER}; color: #000000; text-style: bold; }}
    #cmd {{ height: 3; background: #000000; color: {AMBER}; border: tall #5a3e00; }}
    #cmd:focus {{ border: tall {AMBER}; }}
    #grid {{ grid-size: 2 2; grid-gutter: 0; }}
    Panel {{ border: round #4a3300; background: #000000; }}
    Panel.-active {{ border: heavy {AMBER}; }}
    .ptitle {{ height: 1; background: #1f1500; color: {AMBER}; }}
    Panel.-active .ptitle {{ background: #3a2700; }}
    .spark {{ height: 5; margin: 0 1; }}
    .spark > .sparkline--max-color {{ color: {AMBER}; }}
    .spark > .sparkline--min-color {{ color: #5a3e00; }}
    DataTable {{ background: #000000; color: {AMBER}; height: 1fr; scrollbar-size-vertical: 1; }}
    DataTable > .datatable--header {{ background: #1f1500; color: {AMBER}; }}
    DataTable > .datatable--even-row {{ background: #0d0900; }}
    .note {{ height: auto; max-height: 2; color: #a07c2a; padding: 0 1; }}
    Footer {{ background: #1f1500; }}
    """
    BINDINGS = [
        Binding("f1", "select_panel(1)", "Panel 1"),
        Binding("f2", "select_panel(2)", "Panel 2"),
        Binding("f3", "select_panel(3)", "Panel 3"),
        Binding("f4", "select_panel(4)", "Panel 4"),
        Binding("ctrl+r", "refresh_all", "Refresh"),
        Binding("escape", "clear_cmd", "Clear", show=False),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, layout: list | None = None, save_layout: bool = True):
        super().__init__()
        self.layout_codes = layout or self._load_layout()
        self.save_layout_enabled = save_layout
        self.active = 1
        self.status_msg = "Type HELP and press Enter for all function codes"

    # ── layout ──────────────────────────────────────────
    @staticmethod
    def _load_layout() -> list:
        try:
            saved = json.loads(LAYOUT_FILE.read_text())
            if len(saved) == 4 and all(code in td.SCREENS for code, _ in saved):
                return [tuple(x) for x in saved]
        except (OSError, ValueError, TypeError):
            pass
        return list(DEFAULT_LAYOUT)

    def _save_layout(self) -> None:
        if not self.save_layout_enabled:
            return
        try:
            LAYOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            LAYOUT_FILE.write_text(json.dumps([[p.code, p.arg] for p in self.panels]))
        except OSError:
            pass

    @property
    def panels(self) -> list:
        return [self.query_one(f"#panel{i}", Panel) for i in range(1, 5)]

    # ── UI ──────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        yield Input(placeholder="Function code, e.g.  MKT · POS · CHRT SOL · 3 BT · HELP", id="cmd")
        with Grid(id="grid"):
            for i, (code, arg) in enumerate(self.layout_codes, start=1):
                yield Panel(i, code, arg)
        yield Footer()

    def on_mount(self) -> None:
        self.select_panel(1)
        for p in self.panels:
            self.load_panel(p)
        self.set_interval(1, self._tick)
        self.query_one("#cmd", Input).focus()

    def _tick(self) -> None:
        now = datetime.now()
        utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
        bar = Text.assemble(
            (" GOOB TERMINAL ", "bold reverse"), ("  ", ""),
            (now.strftime("%a %d %b  %H:%M:%S"), "bold"), (f"  ({utc})", ""),
            (f"   │  panel {self.active}  │  ", ""), (self.status_msg, ""))
        self.query_one("#topbar", Static).update(bar)
        for p in self.panels:
            refresh = td.SCREENS[p.code][1]
            if refresh and not p.busy and time.time() - p.loaded_at >= refresh:
                self.load_panel(p)

    # ── data loading (in a thread: network calls never freeze the UI) ──
    def load_panel(self, panel: Panel) -> None:
        if panel.busy:
            return
        panel.busy = True
        panel.set_title("loading…")
        fn = td.SCREENS[panel.code][0]
        code, arg = panel.code, panel.arg

        def job():
            try:
                view = fn(arg) if arg else fn()
            except Exception as e:  # show the problem in the panel instead of crashing
                view = td.View(f"{code}  error", note=f"{type(e).__name__}: {e}")
            if (panel.code, panel.arg) == (code, arg):
                self.call_from_thread(panel.show, view)
            else:
                panel.busy = False

        self.run_worker(job, thread=True, exit_on_error=False)

    # ── commands ───────────────────────────────────────
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.execute_code(event.value)
        event.input.value = ""

    def execute_code(self, text: str) -> None:
        parts = text.strip().split()
        if not parts:
            return
        target = self.active
        if parts[0] in ("1", "2", "3", "4"):
            target = int(parts.pop(0))
        if not parts:
            self.select_panel(target)
            return
        code, arg = parts[0].upper(), " ".join(parts[1:])
        if code not in td.SCREENS:
            self.status_msg = f"Unknown code {code!r} — type HELP"
            return
        panel = self.query_one(f"#panel{target}", Panel)
        panel.code, panel.arg, panel.busy = code, arg, False
        self.select_panel(target)
        self.load_panel(panel)
        self.status_msg = f"{code} → panel {target}"
        self._save_layout()

    def select_panel(self, index: int) -> None:
        self.active = index
        for p in self.panels:
            p.set_class(p.index == index, "-active")

    def action_select_panel(self, index: int) -> None:
        self.select_panel(index)
        self.query_one("#cmd", Input).focus()

    def action_refresh_all(self) -> None:
        for p in self.panels:
            self.load_panel(p)
        self.status_msg = "Refreshing all panels"

    def action_clear_cmd(self) -> None:
        self.query_one("#cmd", Input).value = ""


if __name__ == "__main__":
    GoobTerminal().run()
