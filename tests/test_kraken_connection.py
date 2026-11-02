"""
Offline tests for kraken_connection.py and position_guardian.py.
No network or API keys needed: Kraken responses are mocked.

Run from the repo root:
    python -m unittest discover -s tests -v
"""

import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kraken_connection as kc  # noqa: E402
import position_guardian as pg  # noqa: E402

ASSET_PAIRS = {
    "XXBTZUSD": {
        "altname": "XBTUSD", "wsname": "XBT/USD", "base": "XXBT", "quote": "ZUSD",
        "pair_decimals": 1, "lot_decimals": 8, "ordermin": "0.00005", "costmin": "0.5",
        "tick_size": "0.1",
    },
    "XDGUSD": {
        "altname": "XDGUSD", "wsname": "XDG/USD", "base": "XXDG", "quote": "ZUSD",
        "pair_decimals": 7, "lot_decimals": 8, "ordermin": "13", "costmin": "0.5",
    },
    "DOTUSD": {
        "altname": "DOTUSD", "wsname": "DOT/USD", "base": "DOT", "quote": "ZUSD",
        "pair_decimals": 4, "lot_decimals": 8, "ordermin": "2.5", "costmin": "0.5",
        "tick_size": "0.0005",
    },
}


class PairMetadataMixin:
    def setUp(self):
        kc._asset_pairs_cache["ts"] = time.time()
        kc._asset_pairs_cache["data"] = dict(ASSET_PAIRS)

    def tearDown(self):
        kc._asset_pairs_cache["ts"] = 0.0
        kc._asset_pairs_cache["data"] = {}


class FormattingTests(PairMetadataMixin, unittest.TestCase):
    def test_price_uses_pair_decimals_looked_up_by_altname(self):
        # Before: lookup by "XDGUSD" missed the cache and fell back to 2 decimals.
        self.assertEqual(kc.format_price("XDGUSD", 0.0914712345), "0.0914712")

    def test_price_snaps_to_tick_size(self):
        self.assertEqual(kc.format_price("XBTUSD", 66926.37), "66926.4")
        self.assertEqual(kc.format_price("DOTUSD", 1.38183), "1.3820")

    def test_price_string_passthrough(self):
        self.assertEqual(kc.format_price("XBTUSD", "95.97"), "95.97")

    def test_price_unknown_pair_falls_back_to_8_decimals(self):
        self.assertEqual(kc.format_price("NOPEUSD", 1.123456789123), "1.12345679")

    def test_volume_rounds_down_never_up(self):
        self.assertEqual(kc.format_volume("XBTUSD", 0.000123456789), "0.00012345")
        self.assertEqual(kc.format_volume("DOTUSD", 2.5), "2.5")

    def test_below_minimum(self):
        self.assertIsNone(kc.below_minimum("DOTUSD", 2.5, 1.4))
        self.assertIn("below DOTUSD minimum", kc.below_minimum("DOTUSD", 2.4, 1.4))
        self.assertIn("cost", kc.below_minimum("XBTUSD", 0.00005, 1000.0))
        self.assertIsNone(kc.below_minimum("NOPEUSD", 0.0000001, 1.0))


class PlaceOrderTests(PairMetadataMixin, unittest.TestCase):
    def test_dust_order_is_refused_without_calling_kraken(self):
        with mock.patch.object(kc, "_private") as private:
            ok, result = kc.place_order("DOTUSD", "sell", "market", volume=1.0)
        self.assertFalse(ok)
        self.assertTrue(result["below_minimum"])
        private.assert_not_called()

    def test_order_payload_is_formatted_and_has_no_cost_field(self):
        with mock.patch.object(kc, "_private", return_value={"txid": ["OABC"]}) as private:
            ok, result = kc.place_order(
                "XDGUSD", "sell", "stop-loss", volume=20.123456789, price=0.0914712345, cost=1.0,
            )
        self.assertTrue(ok)
        self.assertEqual(result, {"txid": ["OABC"]})
        path, data = private.call_args.args
        self.assertEqual(path, "/0/private/AddOrder")
        self.assertEqual(data["volume"], "20.12345678")
        self.assertEqual(data["price"], "0.0914712")
        self.assertNotIn("cost", data)

    def test_cost_with_price_derives_volume(self):
        with mock.patch.object(kc, "_private", return_value={"txid": ["O1"]}) as private:
            kc.place_order("DOTUSD", "buy", "market", cost=14.0, price=1.4)
        self.assertEqual(private.call_args.args[1]["volume"], "10")

    def test_api_error_returns_false(self):
        err = kc.KrakenAPIError("/0/private/AddOrder", ["EOrder:Insufficient funds"])
        with mock.patch.object(kc, "_private", side_effect=err):
            ok, result = kc.place_order("DOTUSD", "sell", "market", volume=3.0)
        self.assertFalse(ok)
        self.assertEqual(result["error"], ["EOrder:Insufficient funds"])


class OpenOrdersTests(unittest.TestCase):
    def test_failure_raises_instead_of_returning_empty(self):
        err = kc.KrakenAPIError("/0/private/OpenOrders", ["EAPI:Invalid nonce"])
        with mock.patch.object(kc, "_private", side_effect=err):
            with self.assertRaises(kc.KrakenAPIError):
                kc.get_open_orders()

    def test_success_returns_open_dict(self):
        with mock.patch.object(kc, "_private", return_value={"open": {"O1": {}}}):
            self.assertEqual(kc.get_open_orders(), {"O1": {}})

    def test_order_status(self):
        with mock.patch.object(kc, "_private", return_value={"O1": {"status": "closed"}}):
            self.assertEqual(kc.get_order_status("O1"), "closed")
        with mock.patch.object(kc, "_private", side_effect=kc.KrakenAPIError("p", "down")):
            self.assertIsNone(kc.get_order_status("O1"))


class NonceAndKeyTests(unittest.TestCase):
    def test_nonce_strictly_increases_across_threads(self):
        nonces, lock = [], threading.Lock()

        def worker():
            for _ in range(200):
                n = int(kc._next_nonce())
                with lock:
                    nonces.append(n)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(nonces), len(set(nonces)))

    def test_key_profile_selects_profile_keys(self):
        env = {"KRAKEN_KEY_PROFILE": "hft", "KRAKEN_API_KEY": "default",
               "KRAKEN_API_SECRET": "d", "KRAKEN_API_KEY_HFT": "hftkey", "KRAKEN_API_SECRET_HFT": "s"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(kc._api_credentials(), ("hftkey", "s"))

    def test_key_profile_missing_keys_is_an_error(self):
        with mock.patch.dict(os.environ, {"KRAKEN_KEY_PROFILE": "NOPE"}, clear=False):
            with self.assertRaises(RuntimeError):
                kc._api_credentials()


class GuardianTests(PairMetadataMixin, unittest.TestCase):
    def test_sl_counts_as_fired_only_when_closed(self):
        with mock.patch.object(pg, "get_order_status", return_value="closed"):
            self.assertTrue(pg.check_exit_orders({"sl": "O1"}))

    def test_failed_lookup_is_not_a_fill(self):
        with mock.patch.object(pg, "get_order_status", return_value=None):
            self.assertFalse(pg.check_exit_orders({"sl": "O1"}))

    def test_cancelled_sl_is_not_a_fill_and_is_cleared(self):
        exit_orders = {"sl": "O1"}
        with mock.patch.object(pg, "get_order_status", return_value="canceled"):
            self.assertFalse(pg.check_exit_orders(exit_orders))
        self.assertIsNone(exit_orders["sl"])

    def test_exit_order_skipped_for_dust(self):
        with mock.patch.object(pg, "place_order") as place:
            result = pg.place_exit_orders("DOTUSD", 1.0, 1.4, 0.1, 0.08)
        place.assert_not_called()
        self.assertTrue(result["below_minimum"])
        self.assertIsNone(result["sl"])

    def test_exit_order_price_formatted_for_pair(self):
        with mock.patch.object(pg, "place_order", return_value=(True, {"txid": ["OSL"]})) as place:
            result = pg.place_exit_orders("XDGUSD", 20.0, 0.0914712, 0.1, 0.04)
        self.assertEqual(result["sl"], "OSL")
        self.assertEqual(place.call_args.kwargs["price"], "0.0878124")


if __name__ == "__main__":
    unittest.main()
