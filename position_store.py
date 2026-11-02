#!/usr/bin/env python3
"""
position_store.py — persist each bot's open positions to disk.

Bots used to keep positions only in memory, so a crash or restart lost the
entry price, the stop-loss order id and the learning-DB trade id. Each bot now
writes its open positions to state/<bot_name>.json on every change.

The files also tell bot_monitor_cron.py which pairs belong to a running bot,
so it does not place its own stop-loss on them or sell them at take-profit.

Usage:
    store = PositionStore("dynamic_hft_bot")
    store.set("SOLUSD", {"entry_price": 150.0, "volume": 0.1, "sl_txid": "O..."})
    store.all()            # {"SOLUSD": {...}}
    store.remove("SOLUSD")
"""

import json
import os
import threading
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent / "state"


class PositionStore:
    """Thread-safe JSON-backed {pair: position_dict} for one bot."""

    def __init__(self, bot_name: str, state_dir: Path | str | None = None):
        self.bot_name = bot_name
        self.dir = Path(state_dir) if state_dir else STATE_DIR
        self.path = self.dir / f"{bot_name}.json"
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data.get("positions", {}) if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as e:
            print(f"  ⚠️ Could not read {self.path}: {e}")
            return {}

    def _write(self, positions: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"bot": self.bot_name, "positions": positions}, indent=2, sort_keys=True))
        os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written file

    def all(self) -> dict:
        with self._lock:
            return self._read()

    def get(self, pair: str) -> dict | None:
        with self._lock:
            return self._read().get(pair)

    def set(self, pair: str, position: dict) -> None:
        with self._lock:
            positions = self._read()
            positions[pair] = position
            self._write(positions)

    def update(self, pair: str, **fields) -> None:
        with self._lock:
            positions = self._read()
            if pair in positions:
                positions[pair].update(fields)
                self._write(positions)

    def remove(self, pair: str) -> None:
        with self._lock:
            positions = self._read()
            if positions.pop(pair, None) is not None:
                self._write(positions)


def all_bot_positions(state_dir: Path | str | None = None) -> dict:
    """{bot_name: {pair: position}} for every bot state file."""
    d = Path(state_dir) if state_dir else STATE_DIR
    result = {}
    for f in sorted(d.glob("*.json")):
        result[f.stem] = PositionStore(f.stem, d).all()
    return result
