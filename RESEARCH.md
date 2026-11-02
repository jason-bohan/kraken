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
| 6 | 2026-11-02 | **Cross-coin momentum** (spec below) | 2.7 years, daily, 16 coins | **Fail (near miss).** Default settings: +86.8% vs BTC buy-and-hold +55.0% (Sharpe 0.68 vs 0.58), beats 92% of random picks, +40.5% with costs doubled, 304 trades. But walk-forward −41.9% vs BTC −17.1% on the same unseen periods: the best settings on past data (always top_k 2, weak/no market filter) did badly next. Max drawdown 58.5% vs 53.1% for BTC. |
| 7 | from 2026-11-02 | **Momentum + BTC filter, forward paper trading** (spec below) | live Kraken prices, weekly, no money | _running_ |

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

### #6 result notes

- Passes 3 of 4 pre-registered checks with the default settings, fails the
  walk-forward. Per the rules, that is a fail: not traded live.
- What the walk-forward says: choosing settings by past performance picked
  the most concentrated version (2 coins, little or no BTC filter) every
  time, and it fell apart in the following period. Results depend heavily on
  the settings, which is a warning sign of fragility.
- The default settings were fixed before the test (not tuned), so their +87%
  is not hindsight-picked, but it is a single run over one period with a
  survivor-only coin list, and the random check passed narrowly (92%).
- Not yet done: how many of the 36 setting combos beat BTC over the whole
  period (robustness), and how the default settings did on the walk-forward's
  unseen periods.

### #6b Robustness check — criteria (written before running)

Same data and costs as #6, no new strategy idea. Every one of the 36 setting
combos is run over one common window (after the longest warm-up), and the
window is also split into two halves.

Momentum counts as **robust enough to paper-trade** only if all hold:
1. At least 2/3 of the 36 combos beat BTC buy-and-hold on Sharpe over the
   whole window.
2. At least half of the combos beat BTC buy-and-hold on return in **both**
   halves separately.
3. The default settings (28 / 3 / 100) beat BTC buy-and-hold over the
   walk-forward's unseen periods.

Otherwise: stop testing momentum and fall back to plain DCA + trend filter.

### #6b result (2026-11-02): **not robust** — 2 of 3 checks

Common window 893 days; BTC buy-and-hold +12.0% (Sharpe 0.33): +72.6% in the
first half, −34.8% in the second.

1. Beat BTC on Sharpe: **26/36 — pass** (needed 24).
2. Beat BTC in both halves: **15/36 — fail** (needed 18).
3. Default settings on the walk-forward's unseen periods: **+58.8% vs BTC
   −17.1% — pass.** (The walk-forward itself lost because tuning kept picking
   2-coin, little-or-no-filter settings; the untuned defaults did well.)

Per the pre-registered rule, momentum stops here and is not traded.

Pattern seen in the table (noted *after* the run, so it is a new hypothesis,
not evidence): with no BTC filter, 0/9 combos beat BTC in both halves; with a
50- or 100-day filter, 14/18 did; with a 200-day filter, 1/9. Most combos
still lost money in the falling second half, just less than BTC. Testing this
on the same data would be fitting to it; it can only be judged on new data.


## #7 Momentum + BTC filter — forward paper trading (written before the first week)

**Why forward only.** The #6b table suggested momentum works with a 50–100 day
BTC filter. That pattern was found by looking at those results, so it can
only be judged on data that did not exist yet.

**Rules (fixed, no tuning):** the #6 defaults — 16-coin universe, rank by
28-day return, hold the top 3 with a positive return at 1/3 each, all cash
while BTC's daily close is below its 100-day average. Rebalance weekly, using
completed daily candles from Kraken; fills at the current Kraken price with
0.40% fee + 0.05% slippage per side. Paper capital $1,000 (only the
percentage matters). Benchmark: BTC bought on the same first day and held.

**Runs:** `paper_momentum.py`, weekly (GitHub Actions or by hand). The ledger
`paper/momentum_ledger.json` is committed so the record can't be lost or
quietly edited.

**Decision points:**
- **Week 13 (early stop):** stop if it trails BTC buy-and-hold by more than
  10 percentage points, or is down more than 20%.
- **Week 26 (decision):** consider small real money only if it is ahead of
  BTC buy-and-hold **and** above its starting value. Six months is still a
  small sample: a pass means "small, capped real test", not "proven".
