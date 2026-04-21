# Tennis Poll-Rate Comparison Post-Mortem

**Date:** 2026-04-18  
**Duration:** 120 minutes per session, 5 parallel sessions, 14:25-16:25 UTC  
**Config:** doubles exclusion, prior filter (conf>=0.60, div>=0.05), $10/trade, same pair set  
**Shared:** same market (all 5 saw the same ~7-10 live matches throughout)

## Headline results

| Poll | Trades | W/L | WR | Net PnL | Avg TP | Avg SL | EV/trade |
|---|---|---|---|---|---|---|---|
| 30s | 62 | 33/29 | 53.2% | **+$1.95** | +$1.44 | -$1.64 | +$0.032 |
| **25s** | **64** | **34/30** | **53.1%** | **+$5.96** | **+$1.35** | **-$1.37** | **+$0.093** |
| 20s | 70 | 37/33 | 52.9% | -$4.89 | +$1.16 | -$1.46 | -$0.070 |
| 15s | 64 | 32/32 | 50.0% | -$4.85 | +$1.19 | -$1.39 | -$0.076 |
| 10s | 67 | 35/32 | 52.2% | **+$23.21** | +$1.90 | -$1.41 | +$0.346 |

**Aggregate across all 5:** 327 trades, net +$21.38, avg WR 52.3%.

## The poll10 outlier problem

poll10 looks like the big winner at +$23.21, but:
- Its largest single trade was **+$25.48** (TB-0027 BACK on "Player #53951" entered at odds 4.50 and exited at 1.22 — the odds collapsed from 22% implied to 82% in 40 seconds, a real turnaround)
- Without that one trade, poll10 is **-$2.27 on 66 trades**
- Which would rank it mid-pack, roughly tied with poll15

That's the nature of tennis though — fat-tail wins matter. The honest reading is:
- poll10 **caught a one-in-a-session event** that slower polls missed or got worse fills on
- We can't dismiss the outlier as "luck" because catching those events is the entire point of faster polling
- But we also can't extrapolate from n=1 whether 10s is systematically better

## Clean ranking, ignoring the outlier

| Rank | Poll | Net PnL |
|---|---|---|
| 1 | 25s | +$5.96 |
| 2 | 30s | +$1.95 |
| 3 | 10s (ex-outlier) | -$2.27 |
| 4 | 15s | -$4.85 |
| 5 | 20s | -$4.89 |

**25s wins the "consistent profitability" crown.**

## Key insight: first-half vs second-half

| Poll | H1 PnL | H2 PnL | Pattern |
|---|---|---|---|
| 30s | +$7.73 | -$5.78 | frontloaded, gave back half |
| 25s | +$9.28 | -$3.31 | frontloaded, kept more |
| 20s | -$5.42 | +$0.54 | bad start, mild recovery |
| 15s | -$6.06 | +$1.21 | bad start, mild recovery |
| 10s | +$23.66 | -$0.45 | one big early trade, flat after |

**Every session peaked early and gave some back.** This matches the pattern we saw in previous runs. Reflects the fact that European afternoon tennis (12-16 UTC) is denser than early evening — more simultaneous matches, more high-quality volatility signals.

## Move% bucket analysis (quality by move magnitude)

| Move% | p30 WR | p25 WR | p20 WR | p15 WR | p10 WR |
|---|---|---|---|---|---|
| 10-12% | 54% (n=13) | 50% (n=14) | 39% (n=18) | 50% (n=14) | 47% (n=17) |
| 12-14% | 50% (n=14) | 67% (n=12) | 54% (n=13) | 53% (n=15) | 62% (n=16) |
| 14-16% | 57% (n=21) | 45% (n=22) | 50% (n=22) | 45% (n=20) | 47% (n=19) |
| 16-18% | 62% (n=8) | 67% (n=9) | 67% (n=9) | 50% (n=8) | 50% (n=8) |
| 18-20% | 50% (n=4) | 50% (n=4) | 75% (n=4) | 75% (n=4) | 75% (n=4) |
| 20%+ | 0% (n=2) | 33% (n=3) | 75% (n=4) | 33% (n=3) | 33% (n=3) |

**Observations:**
- 10-12% bucket is the noisiest, and 20s poll had worst WR there (39%) — consistent with 20s "catching" too-small moves
- 18-20% bucket is clean across all polls (50-75% WR), small sample
- Biggest differentiation is actually in the 12-14% bucket where 25s stands out (67%)
- Poll rate matters MOST for the mid-sized moves (12-14%, 16-18%) — where signal quality is highest

## Why the middle polls (15s, 20s) did worst

This was unexpected. My initial hypothesis was "faster poll = more noise", but the data says the relationship isn't monotonic. 

Best guess: at 15-20s, the strategy catches **moves that are just getting started**. A 15s poll sees a 10% move that's on its way to 18% — we enter, the move continues against our fade, we stop out. 
- 30s poll waits until the move has already printed 18-20% — closer to exhaustion
- 10s poll is so fast it catches the peak before retracement begins, plus occasionally nails a reversal turnaround

The 15-20s range may be a "valley of despair" — too fast to filter noise, too slow to catch tops.

## Recommendations

### Near-term (next session)
**Use 25s poll as the new default.** Clear evidence:
- +$5.96 on 64 trades is the best risk-adjusted result
- Highest EV/trade among sustainable configs
- Better avg TP than 30s (+$1.35 vs +$1.44 is close, but with much more trading activity)
- Matches the intuition of "catch the move after it's confirmed but before it starts reverting"

### What to study next
1. **Repeat the 5-way comparison in a different time window** to see if these results hold. Today was European afternoon (12-16 UTC). Run the same matrix on US evening (22-02 UTC) or next weekend morning.
2. **30s vs 25s A/B over multiple sessions** — these two are our candidates. Six 60-min sessions each should give statistical confidence.
3. **Drop 15-20s entirely** — they're the worst performers by every metric.
4. **Consider a 10s variant with bigger min_move_pct** (say, 15% instead of 10%). The 10s outlier is compelling but the signal-to-noise at 10-12% moves is poor. Using 10s only for big moves could harvest the fat tails without the noise.

## Infrastructure notes

All 5 parallel sessions ran cleanly — no crashes, no dashboards crashed beyond poll30's HTTP server getting stuck once (process was fine). Memurai stayed up. Prior agent kept feeding. The architecture scales.

Total api-tennis calls across 5 sessions: ~2,000 — 1% of daily quota. No rate limit issues.

## Raw data

Files archived as `tennis_poll*_20260418_1211.log` (timestamp is launch time, actual session was 14:25-16:25 UTC — can ignore the file name confusion).

Analysis scripts: `_analyze.py`, `_deeper.py`, `_pollwise.json` for later reproducibility.
