"""
notify.py — send bot alerts to Discord and/or Telegram.

Configure in .env (either or both):
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
    TELEGRAM_TOKEN=...            TELEGRAM_CHAT_ID=...

Messages are written in Telegram-style Markdown (*bold*, _italic_, `code`);
for Discord, *bold* is converted to **bold**. Sending never raises: a failed
alert must not crash a trading bot.
"""

import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()

DISCORD_LIMIT = 2000  # Discord rejects message content longer than this
TIMEOUT = 8

_BOLD = re.compile(r"(?<![*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])")


def to_discord_markdown(msg: str) -> str:
    """Convert Telegram legacy Markdown bold (*x*) to Discord bold (**x**)."""
    return _BOLD.sub(r"**\1**", msg)


def _chunks(text: str, limit: int) -> list[str]:
    """Split text into pieces of at most `limit` chars, preferring line breaks."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


def send_discord(msg: str) -> bool:
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return False
    ok = True
    for part in _chunks(to_discord_markdown(msg), DISCORD_LIMIT):
        try:
            r = requests.post(url, json={"content": part}, timeout=TIMEOUT)
            if r.status_code == 429:  # rate limited: wait as told, retry once
                wait = float(r.json().get("retry_after", 1))
                time.sleep(min(wait, 5))
                r = requests.post(url, json={"content": part}, timeout=TIMEOUT)
            ok = ok and r.ok
        except Exception:
            ok = False
    return ok


def send_telegram(msg: str) -> bool:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "Markdown"},
            timeout=TIMEOUT,
        )
        return r.ok
    except Exception:
        return False


def notify(msg: str) -> bool:
    """Send msg to every configured channel. Returns True if any send succeeded."""
    sent_discord = send_discord(msg)
    sent_telegram = send_telegram(msg)
    return sent_discord or sent_telegram


if __name__ == "__main__":
    import sys
    text = " ".join(sys.argv[1:]) or "✅ *Kraken bots* — test notification"
    if not (os.getenv("DISCORD_WEBHOOK_URL") or os.getenv("TELEGRAM_TOKEN")):
        sys.exit("Set DISCORD_WEBHOOK_URL (or TELEGRAM_TOKEN + TELEGRAM_CHAT_ID) in .env first.")
    sys.exit(0 if notify(text) else "Send failed — check the webhook URL / token.")
