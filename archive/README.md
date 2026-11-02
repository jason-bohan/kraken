# Archive

Scripts that are no longer run or maintained. They were moved here on
2026-11-02 when the project was narrowed to two bots
(`stock_swing_bot.py` and `dynamic_hft_bot.py`).

They are kept for reference only. They import modules from the repository
root, so to run one you would have to move it back. Known problems:

- **Options / futures / ETF / leverage bots** (`smart_options_bot`,
  `smart_etf_options_bot`, `cheap_futures_bot`, `btcz_*`, `aave_leverage_bot`):
  Kraken's spot API offers none of these. Most never place an order;
  `aave_leverage_bot` calls `place_order` with arguments it does not accept,
  and `btcz_take_profit_bot` never imports the functions it uses.
- **`btc_swing_bot` / `eth_swing_bot` / `sol_swing_bot`**: the "rebuy" mode
  repeats the long-position exit logic, so after selling it tries to sell
  coins it no longer holds.
- **`dot_swing_bot` / `doge_swing_bot`**: retried below-minimum ("dust")
  sells every minute.
- **`universal_bull_bot`**: `formatted_stop_price` is undefined; the short
  path is disabled.
- **`sol_grid_bot`, `momentum_scanner_bot`, `correlation_bot`**: not reviewed
  in depth; positions are kept in memory only.
- **Scanners** (`*_gem_scanner`, `meme_coin_scanner`): print ideas, never trade.
- **One-shot helpers** (`protective_orders`, `add_take_profits`, `check_*`,
  `test_*`): superseded by `protect_now.py` and `kraken_connection.py`.
