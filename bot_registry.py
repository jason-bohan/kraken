"""
bot_registry.py — the bots that run in production, in one place.

Used by bot_monitor_cron.py (restarts), telegram_bot.py (/bots) and the
terminal (BOTS screen). Each entry is a screen session name.
"""

BOTS = {
    # screen name: label, command args, log file, optional API key profile (see .env.example)
    "bot_BTC":      {"label": "BTC swing", "args": ["stock_swing_bot.py", "BTC"], "log": "logs/BTC_swing.log", "profile": "SWING_BTC"},
    "bot_ETH":      {"label": "ETH swing", "args": ["stock_swing_bot.py", "ETH"], "log": "logs/ETH_swing.log", "profile": "SWING_ETH"},
    "bot_SOL":      {"label": "SOL swing", "args": ["stock_swing_bot.py", "SOL"], "log": "logs/SOL_swing.log", "profile": "SWING_SOL"},
    "bot_ADA":      {"label": "ADA swing", "args": ["stock_swing_bot.py", "ADA"], "log": "logs/ADA_swing.log", "profile": "SWING_ADA"},
    "bot_HFT":      {"label": "Dynamic HFT", "args": ["dynamic_hft_bot.py"], "log": "logs/HFT.log", "profile": "HFT"},
    "telegram_bot": {"label": "Telegram", "args": ["telegram_bot.py"], "log": "logs/telegram_bot.log", "profile": "TELEGRAM"},
}
