"""
Tests for the terminal: screen data functions (with Kraken mocked) and the
Textual app driven headlessly.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import terminal_data as td  # noqa: E402
from learning_engine import LearningEngine  # noqa: E402

TICKERS = {
    "XXBTZUSD": {"c": ["70000.0", "1"], "o": "68000.0", "v": ["1", "100"], "p": ["1", "69000"],
                 "l": ["1", "67000"], "h": ["1", "71000"]},
    "SOLUSD": {"c": ["150.0", "1"], "o": "160.0", "v": ["1", "50000"], "p": ["1", "155"],
               "l": ["1", "148"], "h": ["1", "162"]},
}
PAIRS = {"XXBTZUSD": {"altname": "XBTUSD", "base": "XXBT", "quote": "ZUSD"},
         "SOLUSD": {"altname": "SOLUSD", "base": "SOL", "quote": "ZUSD"}}


def kraken_mocks():
    return [
        mock.patch.object(td.kc, "get_all_tickers", return_value=TICKERS),
        mock.patch.object(td.kc, "get_asset_pairs", return_value=PAIRS),
        mock.patch.object(td.kc, "pair_altname", side_effect=lambda p: PAIRS.get(p, {}).get("altname", p)),
    ]


class DataTests(unittest.TestCase):
    def setUp(self):
        self.patches = kraken_mocks()
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_market_shows_day_change(self):
        with mock.patch.object(td, "all_bot_positions", return_value={}):
            v = td.market()
        rows = {r[0]: r for r in v.rows}
        self.assertEqual(rows["BTC"][1], "70,000")
        self.assertEqual(rows["BTC"][2], ("+2.94%", "up"))
        self.assertEqual(rows["SOL"][2][1], "down")

    def test_positions_pnl_and_unprotected_flag(self):
        state = {"stock_swing_bot_SOL": {"SOLUSD": {"entry_price": 140.0, "volume": 2.0, "fees": 1.0,
                                                    "stop_loss": 130.0, "take_profit": 154.0, "sl_txid": None}}}
        with mock.patch.object(td, "all_bot_positions", return_value=state):
            v = td.positions()
        row = v.rows[0]
        self.assertEqual(row[0], "swing SOL")
        self.assertEqual(row[6], ("+$19.00", "up"))     # (150-140)*2 - 1 fee
        self.assertEqual(row[9], ("NO STOP", "down"))
        self.assertIn("unrealized +$19.00", v.note)

    def test_performance_from_learning_db(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "learning.db"
            le = LearningEngine(str(db))
            tid = le.record_entry("dynamic_hft_bot", "SOLUSD", "buy", 100, 1, 0.5, {}, {})
            le.record_exit(tid, 103, 2.5, 0.025, "take_profit")
            v = td.performance(db_path=db)
        self.assertEqual(v.rows[0][:3], ["dynamic_hft_bot", 1, "100%"])
        self.assertEqual(v.rows[0][3], ("+$2.50", "up"))

    def test_backtests_reads_latest_report(self):
        with tempfile.TemporaryDirectory() as d:
            rep = {"strategy": "hft", "days": 30, "costs": {"fee": 0.004, "slippage": 0.0005}, "pairs": {
                "SOLUSD": {"metrics": {"trades": 40, "win_rate": 0.45, "total_return": -0.05, "buy_hold": 0.1},
                           "costs_doubled": {"total_return": -0.2}, "random_percentile": 0.3,
                           "walk_forward": {"out_of_sample": {"total_return": -0.02}},
                           "verdict": "Loses money after costs.", "equity": [[1, 1.0], [2, 0.95]]}}}
            Path(d, "hft_1.json").write_text(json.dumps(rep))
            v = td.backtests(report_dir=Path(d))
        self.assertEqual(v.rows[0][0], "SOL")
        self.assertEqual(v.rows[0][8], ("Loses money after costs.", "warn"))
        self.assertEqual(v.series, [1.0, 0.95])

    def test_orders_error_is_shown_not_raised(self):
        with mock.patch.object(td.kc, "get_open_orders", side_effect=RuntimeError("Missing KRAKEN_API_KEY")):
            v = td.orders()
        self.assertIn("Missing KRAKEN_API_KEY", v.note)

    def test_every_code_has_help(self):
        codes = [r[0] for r in td.help_view().rows]
        for code in td.SCREENS:
            if code != "HELP":
                self.assertIn(code, codes)


class AppTests(unittest.TestCase):
    """Drive the Textual app headlessly with fast fake screens."""

    def _fake_screens(self):
        def fake(code):
            return lambda arg="": td.View(f"{code}  test {arg}".strip(), ["A", "B"], [["x", ("+1%", "up")]],
                                          note=f"note {code}", series=[1, 2, 3] if code == "CHRT" else [])

        def boom(arg=""):
            raise RuntimeError("feed down")
        screens = {code: (fake(code), 0, desc) for code, (_, _, desc) in td.SCREENS.items()}
        screens["BAL"] = (boom, 0, "x")
        return screens

    def test_layout_commands_and_errors(self):
        import goobtrademaster as gt

        async def scenario():
            with mock.patch.dict(td.SCREENS, self._fake_screens(), clear=True):
                app = gt.GoobTerminal(layout=[("MKT", ""), ("POS", ""), ("PNL", ""), ("SENT", "")],
                                      save_layout=False)
                async with app.run_test(size=(160, 48)) as pilot:
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertEqual(app.panels[0].code, "MKT")
                    self.assertTrue(app.panels[0].has_class("-active"))

                    # "3 CHRT SOL" loads a chart into panel 3 and selects it
                    app.query_one("#cmd").value = "3 CHRT SOL"
                    await pilot.press("enter")
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    p3 = app.panels[2]
                    self.assertEqual((p3.code, p3.arg), ("CHRT", "SOL"))
                    self.assertTrue(p3.has_class("-active"))
                    self.assertTrue(p3.query_one(".spark").display)

                    # F2 then a bare code targets panel 2
                    await pilot.press("f2")
                    app.query_one("#cmd").value = "bal"
                    await pilot.press("enter")
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    note = str(app.panels[1].query_one(".note").render())
                    self.assertIn("feed down", note)  # error shown in the panel, app still alive

                    app.query_one("#cmd").value = "NOPE"
                    await pilot.press("enter")
                    self.assertIn("Unknown code", app.status_msg)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
