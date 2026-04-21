# Strategy Post-Mortem Comparison (Apr 14-18, 2026)

## Summary by strategy

| Strategy | Sessions | Trades | Avg WR | Net PnL | Fees | Gross PnL |
|---|---|---|---|---|---|---|
| MOM (crypto momentum) | 10 | 1,710 | 24.9% | -$242.84 | $157.68 | **-$85.16** (broken) |
| BNC (crypto bounce) | 6 | 258 | 49.2% | -$28.93 | $30.23 | **+$1.30** (fee-killed) |
| Tennis (swing fade) | 4 | 313 | 55.6% | **-$1.98** | $11.46 | +$9.48 (on track) |

## Crypto session-by-session (tournament runs)

| Session | Dur | Trades | WR | PnL | Fees | MOM n | MOM WR | MOM PnL | BNC n | BNC WR | BNC PnL |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Apr14 13:51 (20m) | 20.7m | 32 | 9.4% | -$14.61 | $4.10 | 25 | 0.0% | -$11.42 | 7 | 42.9% | -$3.19 |
| Apr14 14:15 (20m) | 20.8m | 44 | 15.9% | -$6.09 | $3.83 | 35 | 5.7% | -$8.22 | 9 | 55.6% | +$2.13 |
| Apr15 14:56 (4h) | 240.8m | 826 | 29.2% | -$105.85 | $81.64 | 715 | 26.3% | -$98.42 | 111 | 47.7% | -$7.43 |
| Apr15 22:59 (30m) | 30.8m | 125 | 40.8% | -$1.59 | $15.49 | 120 | 41.7% | -$1.18 | 5 | 20.0% | -$0.41 |
| Apr16 03:22 (1h) | 60.7m | 96 | 17.7% | -$18.34 | $8.87 | 86 | 15.1% | -$15.34 | 10 | 40.0% | -$3.00 |
| Apr16 15:14 (1.5h) | 78.7m | 134 | 18.7% | -$27.14 | $11.17 | 107 | 14.0% | -$26.05 | 27 | 37.0% | -$1.09 |
| Apr16 17:54 (25m) | 25.3m | 6 | 66.7% | +$2.93 | $0.34 | 6 | 66.7% | +$2.93 | 0 | - | $0.00 |
| Apr16 18:19 (2h) | 120.8m | 358 | 36.3% | -$41.43 | $28.99 | 282 | 29.4% | -$28.75 | 76 | 61.8% | -$12.68 |
| Apr16 20:42 (5h) | 300.8m | 130 | 16.9% | -$24.64 | $8.60 | 117 | 15.4% | -$21.39 | 13 | 30.8% | -$3.26 |
| Apr17 21:55 (4h) | 240.8m | 61 | 24.6% | -$8.79 | $6.98 | 61 | 24.6% | -$8.79 | 0 | - | $0.00 |

## Tennis session-by-session

| Session | Trades | WR | PnL | EV/trade | Notes |
|---|---|---|---|---|---|
| Run 1 Apr17 (pre-fix) | 113 | 51.3% | -$10.33 | -$0.09 | Default config |
| Run 2 Apr17 21:55 (locked) | 101 | 59.4% | +$2.46 | +$0.024 | maxconf=0.85 maxmv=20% |
| Run 3 Apr17 23:56 (late) | 56 | 58.9% | +$4.61 | +$0.082 | same cfg, thin books |
| **Run 4 Apr18 08:04 (+prior)** | **43** | **51.2%** | **+$1.25** | +$0.029 | **Prior filter active** |
| **Tennis totals post-fix** | **200** | **57.0%** | **+$8.32** | **+$0.042** | net positive across 3 runs |

## Key findings

### 1. MOM is fundamentally broken
Even stripping out all fees, MOM is -$85.16 across 1,710 trades and 24.9% WR. This is not variance - the sample is statistically decisive. Widening parameter ranges for exploration (recent patch) made it worse, not better, because the broader search space includes more bad territory, not less. **Recommendation: archive MOM, stop running it.**

### 2. BNC has edge but is crushed by fees
- Gross PnL is +$1.30 across 258 trades at 49.2% WR - barely positive edge but real
- Fees are $30.23 - literally 24x the gross profit, turning the strategy net-negative
- Fee drag per trade: +$0.117 (MEXC taker fee on $50 notional)
- **Recommendation: rebuild BNC with maker-only entries** (half the fees, 0.01% vs 0.05%) and/or raise the minimum edge threshold so we skip trades where gross EV < 2x fees.

### 3. Tennis is the only net-positive strategy so far
- 57% WR across 3 post-fix runs, +$8.32 net
- Run 4 (with prior filter) adds +$1.25 but had tough variance mid-session
- Prior filter IS firing correctly - logs show 20+ blocks during Run 4 with divergence-based logic
- **Recommendation: continue tennis iteration. Current priority list:**
  1. Fix SL slippage (30s poll window causes 15-25% gaps - consider 15s poll)
  2. Handle doubles separately (prior doesn't cover - either exclude doubles entirely or reduce stake)
  3. Tune prior filter divergence threshold (currently 5pp - might go to 7pp for more selectivity)

### 4. Time-of-day effect (crypto)
Best WR clustered around US market hours (18-22 UTC = 2-5pm NY):
- 22 UTC: WR 40.8% (vs overall 24.9%)
- 18 UTC: WR 36.3%
- Early morning UTC (03:00): WR 17.7% (worst)
- Mid-afternoon UTC (13-15:00): WR 9.4-28.5%

This suggests volume/liquidity during US hours produces cleaner signals. Worth exploring whether running crypto only during 14-22 UTC helps.

### 5. Stage-1 was the one-off winner
`tournament_results_stage1.json` produced survivor G03-S00 with cum=+$3.80. Params: `MOM[mv=0.56% cf=0.77 tr=0.25% tp=0.81% sl=0.57%]`. Modest thresholds. Every subsequent session with widened parameters has gone backwards. **Lesson: the tournament framework amplifies errors when the base strategy is weak. If MOM is broken, no amount of parameter search helps.**

## What to do next

| Action | Priority | Rationale |
|---|---|---|
| Kill MOM | High | 1710 trades proved it's broken; no path to profit |
| Rebuild BNC with maker orders | Medium | Gross edge is real, just fee-killed |
| Continue tennis iterations | High | Only profitable strategy, clear improvement arc |
| Fix tennis poll interval 30s -> 15s | Medium | Should halve SL slippage |
| Deploy prior agent to Railway | Low | Works locally, production move is mechanical |

_Generated Apr 18, 2026 from tournament.log (7MB), tennis_test logs (run 1-4), and tournament_results*.json files._
