import os
import unittest
from unittest import mock

import notify


class FakeResp:
    def __init__(self, status=204, body=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body or {}

    def json(self):
        return self._body


class NotifyTests(unittest.TestCase):
    def test_bold_converted_for_discord(self):
        self.assertEqual(notify.to_discord_markdown("*BUY* SOL _x_ `y`"), "**BUY** SOL _x_ `y`")
        self.assertEqual(notify.to_discord_markdown("**already**"), "**already**")
        self.assertEqual(notify.to_discord_markdown("2 * 3 * 4"), "2 * 3 * 4")

    def test_long_message_split_under_limit(self):
        msg = "\n".join("line %d %s" % (i, "x" * 50) for i in range(100))
        parts = notify._chunks(msg, notify.DISCORD_LIMIT)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= notify.DISCORD_LIMIT for p in parts))
        self.assertEqual("\n".join(parts), msg)

    @mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://example/hook",
                                  "TELEGRAM_TOKEN": "", "TELEGRAM_CHAT_ID": ""})
    def test_discord_only(self):
        with mock.patch.object(notify.requests, "post", return_value=FakeResp()) as post:
            self.assertTrue(notify.notify("*hi*"))
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://example/hook")
        self.assertEqual(post.call_args.kwargs["json"], {"content": "**hi**"})

    @mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://example/hook"})
    def test_rate_limit_retried_once(self):
        resps = [FakeResp(429, {"retry_after": 0.01}), FakeResp()]
        with mock.patch.object(notify.requests, "post", side_effect=resps) as post:
            self.assertTrue(notify.send_discord("hi"))
        self.assertEqual(post.call_count, 2)

    @mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "", "TELEGRAM_TOKEN": "",
                                  "TELEGRAM_CHAT_ID": ""})
    def test_nothing_configured_sends_nothing(self):
        with mock.patch.object(notify.requests, "post") as post:
            self.assertFalse(notify.notify("hi"))
        post.assert_not_called()

    @mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://example/hook"})
    def test_network_error_does_not_raise(self):
        with mock.patch.object(notify.requests, "post", side_effect=OSError("down")):
            self.assertFalse(notify.notify("hi"))


if __name__ == "__main__":
    unittest.main()
