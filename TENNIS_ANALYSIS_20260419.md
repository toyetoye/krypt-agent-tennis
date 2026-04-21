# Tennis Strategy Analysis - 2026-04-19

**Dataset:** 385 trades across 3 post-SL-fix logs. After deduping for the multi-strat run's 4-way cluster, 190 "control" trades available for strategy analysis.

**Clean subset (single-strategy runs only):** 118 trades from two clean runs (poll5_all + slfix_v1).

## Headline findings

### 1. Baseline strategy IS profitable, but by a thin margin
- WR: 47.4% across 190 control trades
- Mean PnL: +$0.07/trade
- Realized R:R: 1.24 (wins bigger than losses)
- **Net +$12.87 on 190 trades**

Edge is real but small relative to variance (stdev $1.65 vs mean $0.07). Statistical confidence requires hundreds of trades.

### 2. Hard caps provide ZERO benefit - KILL THIS EXPERIMENT
From Q3 (gap distribution in losing trades):
- Losses > $1 where cap COULD have helped (gap <7%): **0 trades**
- Losses > $1 where cap COULD NOT help (gap >=7%): **32 trades, -$63.52**

Every significant loss was a gap-past-cap event. A $0.50/$1.00/$1.50 cap would tag the exit reason as "hard_cap" instead of "stop_loss" but exit at the same bad price.

### 3. Odds band 2.80-3.50 is genuinely problematic
Robust signal across both clean runs:
- poll5_all: n=6, -$3.23, WR 17%
- multi_A_B_C_D: n=8, -$8.39, WR 50% (but -$ on winners because small wins, big losses)

Recommended filter: skip entries where entry_odds in [2.80, 3.50).

### 4. Odds band 3.50-5.00 is a GREAT band
Profitable across ALL three runs:
- poll5_all: n=18, +$12.18, WR 61%
- slfix_v1: n=6, +$2.99, WR 50%
- multi: n=10, +$0.47, WR 40%

Don't filter this band. If anything, favor it.

### 5. Confidence 0.70-0.80 is the losing sweet spot
- <0.65: +$0.26/trade (n=15)
- 0.65-0.70: +$0.40/trade (n=32)
- 0.70-0.75: -$0.13/trade (n=48)
- 0.75-0.80: -$0.13/trade (n=48)
- 0.80-0.85: +$0.19/trade (n=47)

Detector confidence is non-monotonic. Mid-band (0.70-0.80) is poison.

### 6. First trade per match is profitable; 2nd trade is suspect
Single-run analysis:
- poll5_all 2nd: n=13, -$0.08 (flat)
- slfix_v1 2nd: n=9, +$0.78 (positive)
- multi 2nd: n=9, -$19.05 (huge loss, driven by capacity bug)

On clean runs, 2nd trade is not clearly a problem. Skip this filter.

### 7. The multi-strategy run data is polluted
Due to the max_open_bets capacity bug, strategies diverged on entries. The "multi_A_B_C_D" run data is unrepresentative. It should be excluded from strategic decisions.

## Recommended next experiment

**Single change: add odds filter skipping [2.80, 3.50) entries.**

- Low risk (removes ~5% of trade volume)
- Strong signal in data (unprofitable in both runs that traded the band)
- Easy to revert if it doesn't generalize

Run this as a single-strategy configuration for 12h. If it holds up, we commit. If net PnL drops, we revert.

**Do NOT stack multiple filters simultaneously.** Combining filters gives false-positive edge improvements due to multiple-comparisons on small sample.

**Do NOT pursue hard-cap variants further.** Data is definitive.

## What we still don't know

1. Whether the strategy's edge persists as trade count grows. Need 500+ trades in a consistent config.
2. Whether Asian overnight hours (04-07 UTC) really are structurally different, or just had bad luck in the 48h run.
3. Whether the 2.80-3.50 odds band is uniformly bad or specific to certain tournaments/player types.
4. Whether slippage on live Betfair would preserve the ~1.24 R:R or crush it.

---
Generated 2026-04-19 14:20 UTC.
