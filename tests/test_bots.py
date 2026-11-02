"""
Offline tests for the two kept bots, the position store and the cron helpers.
Kraken calls are mocked; learning DB and state files go to a temp directory.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import position_store as ps  # noqa: E402
from learning_engine import LearningEngine  # noqa: E402
import stock_swing_bot as swing  # noqa: E402
import dynamic_hft_bot as hft  # noqa: E402
import bot_monitor_cron as cron  # noqa: E402


class TempDirMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.learner = LearningEngine(os.path.join(self.tmp.name, "learning.db"))

    def tearDown(self):
        self.tmp.cleanup()


class PositionStoreTests(TempDirMixin, unittest.TestCase):
    def test_roundtrip_update_remove(self):
        store = ps.PositionStore("bot_a", self.tmp.name)
        store.set("SOLUSD", {"entry_price": 150.0, "volume": 0.1})
        store.update("SOLUSD", sl_txid="O1")
        self.assertEqual(ps.PositionStore("bot_a", self.tmp.name).get("SOLUSD")["sl_txid"], "O1")
        self.assertEqual(ps.all_bot_positions(self.tmp.name), {"bot_a": {"SOLUSD": {
            "entry_price": 150.0, "volume": 0.1, "sl_txid": "O1"}}})
        store.remove("SOLUSD")
        self.assertEqual(store.all(), {})

    def test_missing_or_corrupt_file_reads_as_empty(self):
        store = ps.PositionStore("bot_b", self.tmp.name)
        self.assertEqual(store.all(), {})
        store.path.write_text("{not json")
        self.assertEqual(store.all(), {})


class MacdTests(unittest.TestCase):
    def test_signal_is_an_ema_of_macd_not_a_fixed_fraction(self):
        prices = [100 + i * 0.5 for i in range(40)] + [120 - i for i in range(15)]
        m = swing.calculate_macd(prices)
        self.assertNotAlmostEqual(m["signal"], m["macd"] * 0.9)
        self.assertLess(m["histogram"], 0)  # momentum turned down after the rally

    def test_too_little_data(self):
        self.assertEqual(swing.calculate_macd([1.0] * 20)["histogram"], 0)


class SwingBotTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.patches = [
            mock.patch.object(swing, "LEARNER", self.learner),
            mock.patch.object(swing, "STORE", ps.PositionStore("stock_swing_bot_SOL", self.tmp.name)),
            mock.patch.object(swing, "SYMBOL", "SOL"),
            mock.patch.object(swing, "PAIR", "SOLUSD"),
            mock.patch.object(swing, "CONFIG", {**swing.get_stock_config("SOL"), "min_confidence": 0.6}),
            mock.patch.object(swing, "tg", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()
        swing.position = None
        swing.trade_history.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        swing.position = None
        super().tearDown()

    def _buy(self):
        signal = {"price": 100.0, "confidence": 0.8, "reason": "test",
                  "analysis": {"1h": {"atr": 2.0}}}
        with mock.patch.object(swing, "get_balance", return_value={"ZUSD": "1000"}), \
             mock.patch.object(swing, "place_order", side_effect=[
                 (True, {"txid": ["OBUY"]}), (True, {"txid": ["OSL"]})]) as place, \
             mock.patch.object(swing, "get_fill", return_value={
                 "status": "closed", "vol_exec": 0.5, "avg_price": 101.0, "cost": 50.5, "fee": 0.2}), \
             mock.patch.object(swing, "below_minimum", return_value=None):
            self.assertTrue(swing.execute_buy(signal, dry_run=False))
        return place

    def test_buy_uses_real_fill_places_sl_and_persists(self):
        place = self._buy()
        pos = swing.position
        self.assertEqual((pos["entry_price"], pos["volume"], pos["fees"]), (101.0, 0.5, 0.2))
        self.assertEqual(pos["sl_txid"], "OSL")
        self.assertAlmostEqual(pos["stop_loss"], 101.0 - 3.0)  # 1.5 x ATR below the real fill
        self.assertEqual(place.call_args_list[1].kwargs["order_type"] if "order_type" in place.call_args_list[1].kwargs
                         else place.call_args_list[1].args[2], "stop-loss")
        self.assertEqual(swing.STORE.get("SOLUSD")["sl_txid"], "OSL")

    def test_restart_restores_position(self):
        self._buy()
        swing.position = None  # simulate process restart
        with mock.patch.object(swing, "get_pair_base_balance", return_value=0.5), \
             mock.patch.object(swing, "get_order_status", return_value="open"):
            swing.restore_position(dry_run=False)
        self.assertIsNotNone(swing.position)
        self.assertEqual(swing.position["sl_txid"], "OSL")

    def test_restart_after_sl_filled_records_exit_net_of_fees(self):
        self._buy()
        swing.position = None
        with mock.patch.object(swing, "get_pair_base_balance", return_value=0.0), \
             mock.patch.object(swing, "get_order_status", return_value="closed"), \
             mock.patch.object(swing, "get_fill", return_value={
                 "status": "closed", "vol_exec": 0.5, "avg_price": 98.0, "cost": 49.0, "fee": 0.2}):
            swing.restore_position(dry_run=False)
        self.assertIsNone(swing.position)
        self.assertIsNone(swing.STORE.get("SOLUSD"))
        row = self.learner.summarize("stock_swing_bot", "SOL")
        self.assertEqual(row["sample_size"], 1)
        # (98 - 101) * 0.5 - 0.4 fees = -1.9 on $50.50
        self.assertAlmostEqual(row["avg_pnl_pct"], -1.9 / 50.5, places=6)

    def test_failed_status_lookup_is_not_an_exit(self):
        self._buy()
        with mock.patch.object(swing, "get_order_status", return_value=None):
            self.assertFalse(swing.check_sl_filled())
        self.assertIsNotNone(swing.position)

    def test_dust_position_is_dropped_not_retried(self):
        self._buy()
        with mock.patch.object(swing, "get_order_status", return_value="open"), \
             mock.patch.object(swing, "cancel_order", return_value=True), \
             mock.patch.object(swing, "get_pair_base_balance", return_value=0.5), \
             mock.patch.object(swing, "place_order", return_value=(False, {"error": ["Local"], "below_minimum": True})):
            self.assertTrue(swing.execute_sell(90.0, "stop_loss", dry_run=False))
        self.assertIsNone(swing.position)


class HftTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = ps.PositionStore("dynamic_hft_bot", self.tmp.name)
        self.patches = [
            mock.patch.object(hft, "LEARNER", self.learner),
            mock.patch.object(hft, "STORE", self.store),
            mock.patch.object(hft, "tg", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()
        hft.positions.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        hft.positions.clear()
        super().tearDown()

    def test_thread_keeps_managing_position_after_being_dropped(self):
        """Before: dropping a pair from rotation killed its thread and orphaned the position."""
        stop = threading.Event()
        stop.set()  # already dropped from rotation
        position = {"entry_price": 1.0, "volume": 10.0, "fees": 0.0, "exit_orders": {"sl": "OSL"}}
        hft.positions["ABCUSD"] = position
        prices = iter([1.0, 1.2])  # second check hits +20% → take profit

        with mock.patch.object(hft, "get_pair_data", side_effect=lambda p: (next(prices), 50, 1.2, 1.0)), \
             mock.patch.object(hft, "check_exit_orders", return_value=False), \
             mock.patch.object(hft, "close_position", return_value=True) as close, \
             mock.patch.object(hft.time, "sleep"):
            hft.trade_pair("ABCUSD", False, stop, position)

        close.assert_called_once()
        self.assertEqual(close.call_args.args[3], "take_profit")

    def test_dust_close_forgets_position(self):
        position = {"entry_price": 1.0, "volume": 10.0, "fees": 0.0, "exit_orders": {"sl": None}}
        self.store.set("ABCUSD", position)
        hft.positions["ABCUSD"] = position
        with mock.patch.object(hft, "cancel_remaining_exit"), \
             mock.patch.object(hft, "get_pair_base_balance", return_value=10.0), \
             mock.patch.object(hft, "place_order", return_value=(False, {"below_minimum": True})):
            self.assertTrue(hft.close_position("ABCUSD", position, 0.9, "stop_loss", dry_run=False))
        self.assertNotIn("ABCUSD", hft.positions)
        self.assertEqual(self.store.all(), {})

    def test_deployed_total_is_derived_from_open_positions(self):
        hft.positions.update({"A": {"entry_price": 2.0, "volume": 3.0}, "B": {"entry_price": 1.0, "volume": 4.0}})
        self.assertEqual(hft.deployed_total(), 10.0)

    def test_restore_drops_position_no_longer_held(self):
        self.store.set("ABCUSD", {"entry_price": 1.0, "volume": 10.0, "fees": 0.0,
                                  "exit_orders": {"sl": "OSL", "sl_price": 0.92}})
        with mock.patch.object(hft, "get_balance", return_value={"ZUSD": "5"}), \
             mock.patch.object(hft, "get_pair_base_balance", return_value=0.0), \
             mock.patch.object(hft, "get_order_status", return_value="open"), \
             mock.patch.object(hft, "cancel_order", return_value=True) as cancel, \
             mock.patch.object(hft, "get_fill", return_value=None), \
             mock.patch.object(hft, "get_ticker", return_value={"c": ["1.1"]}):
            restored = hft.restore_positions()
        self.assertEqual(restored, {})
        cancel.assert_called_once_with("OSL")
        self.assertEqual(self.store.all(), {})


class CronTests(TempDirMixin, unittest.TestCase):
    def test_bot_command_uses_this_interpreter_and_profile_if_configured(self):
        with mock.patch.dict(os.environ, {"KRAKEN_API_KEY_HFT": "k"}):
            cmd = cron.bot_command(["dynamic_hft_bot.py"], "logs/HFT.log", "HFT")
        self.assertTrue(cmd.startswith("KRAKEN_KEY_PROFILE=HFT "))
        self.assertIn(sys.executable, cmd)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(cron.bot_command(["x.py"], "l.log", "NOPE").startswith("KRAKEN_KEY_PROFILE"))

    def test_owned_pairs_come_from_state_files(self):
        ps.PositionStore("dynamic_hft_bot", self.tmp.name).set("XXBTZUSD", {"volume": 1})
        with mock.patch.object(cron, "all_bot_positions",
                               lambda: ps.all_bot_positions(self.tmp.name)), \
             mock.patch.object(cron, "pair_altname", lambda p: {"XXBTZUSD": "XBTUSD"}.get(p, p)):
            self.assertEqual(cron.bot_owned_pairs(), {"XBTUSD"})


if __name__ == "__main__":
    unittest.main()
