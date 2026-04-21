# Tennis Strategy - Validated Config (2026-04-17)

## Status
**Net-positive out-of-sample.** Locked in; do not change without another 100+ trade validation run.

## Validation results

| Metric | Run 1 (pre-fix) | Run 2 (post-fix) |
|---|---|---|
| Duration | 100 min | 120 min |
| Trades | 113 | 101 |
| Win rate | 51.3% | **59.4%** |
| Net PnL | -$10.33 | **+$2.46** |
| EV/trade | -$0.09 | **+$0.024** |
| Avg winner | +$1.35 | +$1.24 |
| Avg loser | -$1.68 | -$1.81 |
| Take-profit exits | 57 | 59 |
| Stop-loss exits | 51 | 39 (-24%) |

Run 2 extrapolated: **+$1.23/hour** at $10 stakes.

## Config (tennis_detector.py TennisConfig defaults)

```
min_odds_move_pct      10.0      # unchanged from original
max_odds_move_pct      20.0      # NEW - rejects momentum-continuation moves
min_confidence         0.55      # unchanged
max_confidence         0.85      # NEW - rejects over-scored signals
stop_odds_move_pct     11.0      # was 15.0
target_odds_move_pct    8.0      # unchanged
cooldown_sec          300.0      # was 120.0, now per (match, direction)
relose_cooldown_sec   600.0      # NEW - strategy-layer blocker after SL
```

## Key analytical finding: confidence is inverted

On the 113-trade Run 1 sample:

| Conf bucket | n | WR | EV |
|---|---|---|---|
| 0.55-0.65 | 5 | 80% | +$0.51 |
| 0.65-0.75 | 21 | 57% | +$0.01 |
| 0.75-0.85 | 35 | 54% | +$0.23 |
| 0.85-0.95 | 39 | 49% | -$0.35 |
| 0.95+ | 13 | **31%** | **-$0.56** |

The `_score_signal` formula rewards large moves and odds near 2.00. In tennis,
large mid-match odds moves usually reflect decisive events (set won, break
of serve converted) and CONTINUE, not reverse. The strategy bets on
reversal, so high-scored signals fail the reversal thesis. Keep
min_confidence=0.55 as the floor (filters obvious noise) but cap at 0.85.

## Same inversion in move size

| |move| bucket | n | WR | EV |
|---|---|---|---|
| 10-15% | 27 | **70%** | **+$0.38** |
| 15-20% | 24 | 58% | +$0.11 |
| 20-25% | 31 | 42% | -$0.36 |
| 25-30% | 14 | 50% | -$0.20 |
| 30-50% | 17 | **29%** | **-$0.54** |

Small moves revert (thesis works). Large moves trend (thesis fails).
Hence max_odds_move_pct=20.

## Side asymmetry (observed but not encoded)

| Swing type | n | WR | EV |
|---|---|---|---|
| lay_home  | 19 | 63% | +$0.31 |
| back_away | 38 | 55% | +$0.06 |
| lay_away  | 27 | 48% | -$0.24 |
| back_home | 29 | **41%** | **-$0.41** |

Not filtered out because excluding the two losing-tilted types reduced
sample size too much (42 to 22) without meaningfully improving backtest
total (+$13.94 to +$11.56). Worth revisiting after more data.

## Known remaining issues

1. **SL slippage on polled feed.** Avg stop-loss was -$1.81 (designed -$1.10
   at 11% SL on $10 stake). Odds can gap >20% between 30s polls when a
   match has a decisive point. Possible fix: drop --poll from 30 to 15s
   (API budget at Business tier is 200k/day, easily sustainable).
2. **Commission drag.** 5% Betfair-style commission on gross winnings takes
   ~$4 per 100 trades off the top. Real Betfair rates drop to 2-3% at volume.
3. **Small sample.** n=101 is one session; could be variance. Want 3-5
   sessions of 100+ trades each before considering this "done".

## Reproducing the run

```
$env:TENNIS_PROVIDER = "api_tennis"
$env:APITENNIS_KEY = "<rotate-me>"
.\venv\Scripts\python.exe tennis_test.py --minutes 120 --poll 30 --stake 10
# Dashboard at http://localhost:8880
```

## Next steps (in priority order)

1. Rotate the api-tennis.com API key (exposed in chat history)
2. Run 2-3 more 2hr validation sessions this week, check WR stays 55-65%
3. If consistent, drop poll to 15s to fight SL slippage
4. Revisit side-asymmetry filter after more data accumulates

## Session context

- Analysis script artifacts: bets2 dict in PID 33568 REPL (113 trades)
- Run 1 log archived as tennis_test_run1.log
- Run 2 log is current tennis_test.log (346 KB)
- Original tennis_detector.py preserved as tennis_detector.py.bak
- All other patched files have .bak siblings too
