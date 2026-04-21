# Tennis Strategy Analysis — 2026-04-20

**Run:** `tennis_run_v2hc_20260420_0740` — 6h multi-strategy paper run, completed 13:40 local.
**Config:** stake $10, 5s poll, hard_cap $1.00, kill_switch −$15, max-consecutive-losses 12.
**Market:** Madrid ATP+WTA qualifying (primary), Rome/Savannah quals, Gwangju Challenger.

## FINAL Results (full 6h)

| Variant | Filter | Trades | W/L | WR | **PnL** |
|---|---|---|---|---|---|
| **A** | control | 169 | 66/103 | 39% | **−$15.63** (killed at t+165m) |
| **B** | conf 0.70-0.85 + skip odds 2.50-3.00 | 109 | 41/68 | 38% | **−$15.44** (killed at t+165m) |
| **C** | max_fader_rank=300, min_rank_gap=50 | 236 | 106/130 | 45% | **+$7.07** |
| **D** | skip_lay_signals=True (BACK-only) | 381 | 173/208 | 45% | **+$39.72** 🏆 |

**Sample size:** 895 unique trade events across 412 SWING signals.

## Key findings

### 1. BACK-only (D) is the runaway winner
Over 6h and 381 trades, D produced +$39.72 with 47% WR. No other single filter comes close. The filter eliminated 222 LAY signals (~60% of raw signal volume).

### 2. Rank-gap filter (C) has real edge but regressed late
C peaked at +$25 at t+222m, then gave back to +$7.07 by end. The regression came from 4 matches specifically:
- T. C. Grant vs V. Erjavec: 16 trades, 0% WR, −$14.72 
- F. Jones vs G. Maristany: 13, 8%, −$9.39
- J. Forejtek vs L. Carboni: 10, 10%, −$8.20

These passed the rank filter but were **trending matches, not reverting** — fading them was fighting the tape. No rank/ELO signal distinguishes clean reverting matches from trending ones.

### 3. Hard cap $1.00 is useful after all — revising yesterday
190 hard_cap exits across the run for −$281.36. Yesterday's retrospective said hard caps were useless (gap-through blowouts). Today's data shows **50 of those 190 had hold times of 2-5 minutes** — slow-drift losers that the cap caught before they got worse. **Hard cap helps on drifts, doesn't help on gaps.** Net verdict: keep.

### 4. Aggregate PnL by 30-min bucket — volatility is brutal
Individual 30-min windows ranged from **−$34 to +$37** in total PnL across all strats. The run was dominated by 2 bad windows and 2 good ones. **Kill switch −$15 was too tight** — A and B died at t+165m precisely before the +$37 window at t+210m (the Madrid post-recovery rally). Raise to **−$25** for more headroom.

### 5. Odds 2.50-3.00 is still a loser zone — validated across days
Full 6h: 90 trades in this bucket, 48% WR, **−$10.80 total, avg −$0.12/trade**. Consistent with yesterday's retrospective finding AND this morning's preliminary analysis. Worth filtering.

### 6. Within C's chosen subset, the outcome was bimodal
Winning matches (Svajda, Waltert): 72% WR, +$55.39 on 88 trades. 
Losing matches (Bartunkova, Garin, Pellegrino, Charaeva): 21% WR, −$58.49 on 118 trades.

**Same input characteristics** (entry odds, move sizes, hold times). Different outcomes. The distinguishing factor was **whether the match was reverting or trending** — which pre-entry data we don't currently have.

**60% of C's PnL came from ONE match (Svajda vs Sakamoto: 56 trades, 80% WR, +$41.74).** Match concentration = variance risk.

### 7. api-tennis outage 09:06-10:25 was handled cleanly
HTTP 500s for ~79 minutes. Feed graceful degradation worked (Poll error logs, no new signals). Auto-recovered when service returned.

## Invalidated hypotheses
- **Yesterday's retrospective claim:** conf 0.70-0.80 is bad — **REJECTED.** B stacked this filter + odds skip, bled to kill switch. Live data shows conf is not a clean monotonic signal.
- **This morning's preliminary:** hard cap is useless — **REJECTED.** 50 drift-captures is meaningful value.

## Validated
- Rank filter (max_fader_rank=300, min_rank_gap=50) — real structural edge
- BACK-only (skip_lay_signals) — strongest single filter
- Hard cap $1.00 — helps on drifts
- Odds 2.50-3.00 skip — consistent negative across days
- Session kill switch — direction right, threshold too tight (raise to −$25)

## Next run: v3 variants (launched after this analysis)

All variants inherit BACK-only (yesterday's D = proven winner). Each adds ONE isolated filter on top to measure marginal contribution.

| Variant | Stack | Hypothesis tested |
|---|---|---|
| **A** | BACK-only | baseline to replicate today's +$39 |
| **B** | BACK + rank-gap (max 300, gap≥50) | does rank filter ADD to BACK-only? |
| **C** | BACK + skip odds [2.50, 3.00) | is the bad-odds-zone filter robust? |
| **D** | BACK + match cooldown (max 5 / 1h / match) | does killing re-entry bleed help? |

**Config changes from today:**
- Kill switch raised −$15 → −$25 (avoid premature death during chaos windows)
- Hard cap unchanged at $1.00
- Stake unchanged at $10
- New `max_trades_per_match_window` filter implemented and unit-tested

## Open questions for future experiments

1. **Trending vs reverting detection.** Pre-entry signal needed to distinguish Bartunkova-style bleeds from Svajda-style wins. Possibly: score context, serve-break count, consecutive-same-direction odds moves.
2. **Surface-specific ELO.** We have it but haven't used it. Clay (Madrid/Rome) dominates.
3. **Time-of-day filter.** The +$37 and −$34 windows were distinct hours; worth attribution.
4. **Stake scaling.** If future runs confirm edge, reduce stake for tighter per-trade risk.
