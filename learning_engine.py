#!/usr/bin/env python3
"""
SQLite-backed learning helper for rule-based trading bots.

Logs entry conditions and trade outcomes, then suggests bounded parameter
adjustments once there is enough completed trade history.
"""

import json
import sqlite3
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


class LearningEngine:
    """Persist trade observations and derive conservative parameter updates."""

    def __init__(self, db_path: str = "learning.db"):
        self.db_path = Path(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_name      TEXT    NOT NULL,
                    symbol        TEXT    NOT NULL,
                    side          TEXT    NOT NULL,
                    status        TEXT    NOT NULL DEFAULT 'open',
                    entry_time    TEXT    NOT NULL,
                    exit_time     TEXT,
                    entry_price   REAL    NOT NULL,
                    exit_price    REAL,
                    position_size REAL,
                    confidence    REAL,
                    pnl_amount    REAL,
                    pnl_pct       REAL,
                    exit_reason   TEXT,
                    features_json TEXT    NOT NULL,
                    config_json   TEXT    NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS parameter_snapshots (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_name          TEXT NOT NULL,
                    symbol            TEXT NOT NULL,
                    created_at        TEXT NOT NULL,
                    base_config_json  TEXT NOT NULL,
                    tuned_config_json TEXT NOT NULL,
                    metrics_json      TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trade_log_lookup
                ON trade_log (bot_name, symbol, status, entry_time)
                """
            )

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_entry(
        self,
        bot_name: str,
        symbol: str,
        side: str,
        entry_price: float,
        position_size: float,
        confidence: float,
        features: dict[str, Any],
        config: dict[str, Any],
    ) -> int:
        entry_time = datetime.utcnow().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trade_log
                    (bot_name, symbol, side, status, entry_time,
                     entry_price, position_size, confidence,
                     features_json, config_json)
                VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
                """,
                (
                    bot_name,
                    symbol,
                    side,
                    entry_time,
                    entry_price,
                    position_size,
                    confidence,
                    json.dumps(features, sort_keys=True),
                    json.dumps(config, sort_keys=True),
                ),
            )
            return int(cursor.lastrowid)

    def record_exit(
        self,
        trade_id: int,
        exit_price: float,
        pnl_amount: float,
        pnl_pct: float,
        exit_reason: str,
    ) -> None:
        exit_time = datetime.utcnow().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE trade_log
                SET status      = 'closed',
                    exit_time   = ?,
                    exit_price  = ?,
                    pnl_amount  = ?,
                    pnl_pct     = ?,
                    exit_reason = ?
                WHERE id = ?
                """,
                (exit_time, exit_price, pnl_amount, pnl_pct, exit_reason, trade_id),
            )

    def recover_open_trade(self, bot_name: str, symbol: str) -> int | None:
        """Return the DB id of an unfinished open trade, or None.

        Call this at bot startup after confirming a position is still live on
        the exchange. If the returned id is not None, assign it to active_trade_id
        so the eventual exit can close the correct row.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM trade_log
                WHERE bot_name = ? AND symbol = ? AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (bot_name, symbol),
            ).fetchone()
        return int(row["id"]) if row else None

    def close_orphaned_trades(self, bot_name: str, symbol: str) -> int:
        """Mark all open trade rows as 'orphaned' when no live position exists.

        Returns the number of rows updated.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE trade_log
                SET status = 'orphaned',
                    exit_reason = 'no_position_at_restart'
                WHERE bot_name = ? AND symbol = ? AND status = 'open'
                """,
                (bot_name, symbol),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def summarize(
        self,
        bot_name: str,
        symbol: str,
        lookback: int = 50,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT confidence, pnl_pct, features_json, exit_reason
                FROM trade_log
                WHERE bot_name = ? AND symbol = ? AND status = 'closed'
                ORDER BY id DESC
                LIMIT ?
                """,
                (bot_name, symbol, lookback),
            ).fetchall()

        if not rows:
            return {
                "sample_size": 0,
                "win_rate": 0.0,
                "avg_pnl_pct": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "avg_confidence_win": 0.0,
                "avg_confidence_loss": 0.0,
                "avg_rsi_win": None,
                "avg_rsi_loss": None,
                "stop_loss_rate": 0.0,
            }

        def avg(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        def extract_rsi(records) -> list[float]:
            result = []
            for rec in records:
                rsi = json.loads(rec["features_json"]).get("rsi_1h")
                if isinstance(rsi, (int, float)):
                    result.append(float(rsi))
            return result

        wins = [r for r in rows if (r["pnl_pct"] or 0) > 0]
        losses = [r for r in rows if (r["pnl_pct"] or 0) <= 0]
        stops = [r for r in rows if r["exit_reason"] == "stop_loss"]

        return {
            "sample_size": len(rows),
            "win_rate": len(wins) / len(rows),
            "avg_pnl_pct": avg([float(r["pnl_pct"] or 0) for r in rows]),
            "avg_win_pct": avg([float(r["pnl_pct"] or 0) for r in wins]),
            "avg_loss_pct": avg([float(r["pnl_pct"] or 0) for r in losses]),
            "avg_confidence_win": avg([float(r["confidence"] or 0) for r in wins]),
            "avg_confidence_loss": avg([float(r["confidence"] or 0) for r in losses]),
            "avg_rsi_win": avg(extract_rsi(wins)) if wins else None,
            "avg_rsi_loss": avg(extract_rsi(losses)) if losses else None,
            "stop_loss_rate": len(stops) / len(rows),
        }

    # ------------------------------------------------------------------
    # Tuning
    # ------------------------------------------------------------------

    def tune_config(
        self,
        bot_name: str,
        symbol: str,
        base_config: dict[str, Any],
        lookback: int = 50,
        min_closed_trades: int = 12,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Return (tuned_config, metrics).
        Adjustments are small and bounded so no single bad batch can cause harm.
        """
        metrics = self.summarize(bot_name, symbol, lookback=lookback)
        tuned = deepcopy(base_config)
        tuned["learning_enabled"] = True

        if metrics["sample_size"] < min_closed_trades:
            metrics["tuning_applied"] = False
            metrics["tuning_reason"] = f"need {min_closed_trades} closed trades, have {metrics['sample_size']}"
            return tuned, metrics

        cur_conf = float(tuned.get("min_confidence", 0.60))
        cur_rsi = float(tuned.get("rsi_oversold", 35))
        cur_vol = float(tuned.get("volatility_threshold", 0.05))
        cur_profit = float(tuned.get("profit_target", 0.06))
        cur_stop = float(tuned.get("stop_loss", 0.03))

        # Confidence / volatility gate
        if metrics["win_rate"] < 0.45 or metrics["avg_pnl_pct"] < 0:
            # Losing more than winning — be more selective
            tuned["min_confidence"] = min(0.85, round(cur_conf + 0.05, 2))
            tuned["volatility_threshold"] = max(0.02, round(cur_vol - 0.005, 3))
        elif metrics["win_rate"] > 0.60 and metrics["avg_pnl_pct"] > 0.01:
            # Performing well — can relax slightly
            tuned["min_confidence"] = max(0.50, round(cur_conf - 0.03, 2))
            tuned["volatility_threshold"] = min(0.12, round(cur_vol + 0.005, 3))

        # RSI entry point
        rsi_win = metrics["avg_rsi_win"]
        rsi_loss = metrics["avg_rsi_loss"]
        if rsi_win is not None and rsi_loss is not None:
            if rsi_win + 2 < rsi_loss:
                tuned["rsi_oversold"] = max(20, int(round(cur_rsi - 2)))
            elif rsi_loss + 2 < rsi_win:
                tuned["rsi_oversold"] = min(45, int(round(cur_rsi + 2)))

        # Profit target: step toward 75% of typical win, max 0.005 per cycle
        if metrics["avg_win_pct"] > 0:
            target = min(0.15, max(0.02, metrics["avg_win_pct"] * 0.75))
            step = min(0.005, abs(target - cur_profit))
            if target > cur_profit:
                tuned["profit_target"] = round(cur_profit + step, 3)
            elif target < cur_profit:
                tuned["profit_target"] = round(cur_profit - step, 3)

        # Stop loss: step toward 90% of typical loss magnitude, max 0.003 per cycle
        if metrics["avg_loss_pct"] < 0:
            target_stop = min(0.08, max(0.01, abs(metrics["avg_loss_pct"]) * 0.90))
            step = min(0.003, abs(target_stop - cur_stop))
            if target_stop < cur_stop:
                tuned["stop_loss"] = round(cur_stop - step, 3)
            elif target_stop > cur_stop:
                tuned["stop_loss"] = round(cur_stop + step, 3)

        metrics["tuning_applied"] = True
        metrics["tuning_reason"] = "bounded adjustments from recent closed trades"
        self._store_snapshot(bot_name, symbol, base_config, tuned, metrics)
        return tuned, metrics

    def _store_snapshot(
        self,
        bot_name: str,
        symbol: str,
        base_config: dict[str, Any],
        tuned_config: dict[str, Any],
        metrics: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO parameter_snapshots
                    (bot_name, symbol, created_at,
                     base_config_json, tuned_config_json, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    bot_name,
                    symbol,
                    datetime.utcnow().isoformat(timespec="seconds"),
                    json.dumps(base_config, sort_keys=True),
                    json.dumps(tuned_config, sort_keys=True),
                    json.dumps(metrics, sort_keys=True),
                ),
            )
