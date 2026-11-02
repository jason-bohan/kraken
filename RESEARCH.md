# Strategy research log

Every strategy idea tested, in order, including failures. The more ideas are
tried, the more likely one "passes" by luck, so the count matters as much as
the results. Rules and pass criteria are written down **before** a test runs.

All tests: `backtester.py` / `momentum_backtest.py`, Coinbase public candles,
Kraken taker costs (0.40% fee + 0.05% slippage per side) unless noted.

## Pass criteria (all must hold)

1. **Walk-forward:** positive return on unseen data, with settings chosen only
   from earlier data.
2. **Timing / selection beats random:** beats ≥ 90% of random runs with the
   same exposure (same number of trades / holdings, random timing or picks).
3. **Survives double costs:** still positive with fees and slippage doubled.
4. **Enough trades** to judge (≥ 10 per coin, or ≥ 30 portfolio trades).

Passing means "worth a dry run", not "proven".

## Results

| # | Date | Idea | Data | Result |
|---|------|------|------|--------|
| 1 | 2026-11-02 | **HFT dip-buy** (`dynamic_hft_bot`): RSI(1m) ≤ 40 or 2–15% dip, +10% / −8% exits | 30 days, 1m, 6 coins | **Fail.** In the market 100% of the time; tracked buy-and-hold down (avg −20% vs −19%). |
| 2 | 2026-11-02 | HFT + trend filter (4h/12h SMA), time limit, 1–10% targets: 192 combos | same | **Fail.** Walk-forward −25% per coin over 190 trades; maker costs also fail. |
| 3 | 2026-11-02 | **Trend-following** daily: fast EMA > slow EMA and close > slow EMA | 3 years, daily, 6 coins | **Fail on returns.** Avg −6.9% per coin vs buy-and-hold +42.8%; walk-forward −9%; beats random timing 60% (≈ luck). Drawdown 44% vs 76% for buy-and-hold. |
| 4 | 2026-11-02 | Trend-following on 4h candles | 2 years, 4h, 6 coins | **Fail.** −0.62% per trade over 368 trades; walk-forward −36% per coin. |
| 5 | 2026-11-02 | **Swing** (`stock_swing_bot`): multi-timeframe RSI/MACD score, 1.5×ATR stop, +10% target | 180 days, 5m, 4 coins | **Fail.** 8.8% win rate, −1.08% per trade over 226 trades; walk-forward −23% per coin; worse than random timing. The ATR stop (~1–2%) is hit by noise long before the +10% target. |
| 6 | 2026-11-02 | **Cross-coin momentum** (spec below) | 3 years, daily, 16 coins | _pending_ |

Note: the first trend runs reported "beats 100% of random". That was a bug in
the random baseline (random entries reused the trend exit and churned to
−99%), fixed in commit f4c0d88. Rows 3–4 use the corrected check.

## #6 Cross-coin momentum — specification (written before running)

**Idea.** Coins that rose most over the past few weeks tend to keep
outperforming for a while (cross-sectional momentum), and a whole-market
downtrend is a good time to be in cash. Documented in academic work on crypto,
which is why it is tried before anything more exotic.

**Rules.**
- Universe: 16 liquid coins with 3+ years of Coinbase history — BTC, ETH, SOL,
  ADA, DOGE, LINK, AVAX, XRP, DOT, LTC, BCH, UNI, AAVE, XLM, ATOM, NEAR.
- Every `rebalance_days` (7), at the daily close: score each coin by its
  return over the last `lookback` days.
- Market filter: if BTC's close is below its `filter_sma`-day average, hold
  only cash (0 = filter off).
- Otherwise hold the `top_k` best-scoring coins **with a positive score**, each
  at 1/`top_k` of equity; unused slots stay in cash.
- Trade at the next day's open. Costs are charged on every buy and sell.

**Settings.** Defaults: lookback 28, top_k 3, filter_sma 100, weekly.
Walk-forward grid (36 combos): lookback {14, 28, 56} × top_k {2, 3, 5} ×
filter_sma {0, 50, 100, 200}.

**Random baseline.** At each rebalance, hold the same number of coins as the
strategy did, keeping as many of last week's coins as the strategy kept, and
fill the rest at random from the eligible coins. Same timing, exposure and
turnover (so the same fees): tests whether the momentum *selection* adds
anything. (A first version reshuffled every week and lost mostly to fees,
which would have made any strategy look good; caught on synthetic data
before any real run.)

**Known bias.** The universe is today's survivors; coins that died in the
period are missing, which flatters any long-only test.
