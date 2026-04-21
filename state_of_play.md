# krypt-agent tennis — session handoff (Apr 21, 2026, ~21:02 local)

**Purpose:** Resume work in a fresh chat without losing context. All critical
state, config, and findings compacted below.

---

## 1. RUNNING PROCESSES (DO NOT DISTURB without explicit user instruction)

| Process | Port | PID | Started | Ends ~ | Variants |
|---|---|---|---|---|---|
| **v6** | 8887 | 92048 | 14:33 Apr 21 | 23:33 Apr 21 | V1, V2, V3 |
| **v7** | 8888 | 97444 | 21:00 Apr 21 | 06:00 Apr 22 | V1, V2, V3, V4, V5, V6, V7 |

Last known state (21:02 local):
- **v6** at 388m / 540: V1=+$12.21/544, V2=+$13.67/241, V3=+$55.10/312
- **v7** at 1m / 540: all variants at 0/0 (just started)

Kill-switch for both: $25 session loss only (no consecutive-loss kill).

---

## 2. v7 VARIANTS (CURRENT EXPERIMENT - what we're testing)

Defined in `tennis_multi_v7.py`. NEG_STATES = frozenset({BEHIND_SET1_HEAVY, BEHIND_LOST_SET1_BAGEL, AHEAD_SET1_HEAVY, AHEAD_WON_SET1_FADING, EVEN_LOST_SET1}).

| # | Label | Config kwargs | Purpose |
|---|---|---|---|
| 1 | V1 | `skip_lay_signals=True` | Baseline A (no filters) |
| 2 | V2 | V1 + `blocked_event_types={challenger}` | Isolate tier filter |
| 3 | V3 | V2 + `skip_odds_bands=((1.60,1.80),)` | Tier + odds (matches v6 V3) |
| 4 | V4 | V1 + `blocked_entry_states=NEG_STATES` | Isolate state filter |
| 5 | V5 | V4 + `block_doubles=False` | V4 + allow doubles |
| 6 | **V6** | V3 + `blocked_entry_states=NEG_STATES` | **Full stack (all 3 filters)** |
| 7 | **V7** | V3 duplicate | A/A control for variance measurement |

The key questions this run answers:
- **V4 vs V1:** does the state filter alone add edge?
- **V6 vs V3:** does state filter add edge ON TOP of tier + odds filters?
- **V3 vs V7:** how much run-to-run variance is there in identical configs?
- **V5 vs V4:** do doubles help or hurt when state filter is applied?

---

## 3. CODEBASE STATE

New files since v6:
- `tennis_entry_state.py` (8.9 KB): live state classifier, 18 state labels, reuses helpers from tennis_dominance.py
- `tennis_multi_v7.py` (11.7 KB): 7-variant runner, port 8888
- `_test_entry_state.py` (29 tests), `_test_state_e2e.py` (7 tests), `_test_blocked_tiers.py` (4 tests)

Patched files:
- `tennis_detector.py` (16.4 KB): added `blocked_entry_states: frozenset = None` and `block_doubles: bool = True` config fields
- `tennis_strategy.py` (47.4 KB): state filter in `_can_enter()` right after tier blocklist; doubles check now respects `cfg.block_doubles`

**Test suite: 71 tests, all passing.**

Dashboard file (`tennis_multi_dashboard.html`) was patched earlier to render arbitrary variant labels (previously hardcoded to A/B/C/D). Both v6 and v7 use it.

---

## 4. KEY FINDINGS FROM THIS SESSION

### State-filter simulation on v5-madrid's 1,169 BACK trades

Negative-edge states (edge/trade):
- BEHIND_SET1_HEAVY: n=4, -$2.29 (tiny sample)
- BEHIND_LOST_SET1_BAGEL: n=7, -$0.40
- AHEAD_SET1_HEAVY: n=9, -$0.33
- AHEAD_WON_SET1_FADING: n=17, -$0.19
- EVEN_LOST_SET1: n=68, -$0.13 (biggest dollar leak)

Positive-edge states:
- FADING_WON_SET1_BAGEL: n=8, +$1.03/trade (best)
- FINAL_SET: n=78, +$0.21/trade
- DOMINATING_WON_SET1_BAGEL: n=13, +$0.31

### Simulated filter stack on v5-madrid data

- Baseline A (all trades): +$23.61 on 1,169
- V3 (skip Chall + skip [1.60,1.80]): +$56.80 on 809
- V4 equivalent (skip 5 neg states): computed from non-V3 paths
- V6 equivalent (V3 + 5 neg states, "V4-strict"): **+$79.97 on 735** ← best simulated

### The PRE_MATCH finding (not a TZ bug)

572 of 1169 v5-madrid trades had `event_time > entry_ts` — our detector fires
BEFORE scheduled match start. PRE_MATCH edge: +$0.020/trade (breakeven).
BUT split by tier: Challenger PRE_MATCH = -$31.15, non-Challenger = +$42.34.
V2's tier filter already catches the Challenger PRE_MATCH leak.

### Tier policy (confirmed)

- **Challenger**: blocked in V2, V3, V6, V7 (tier-filter variants).
  Allowed in V1, V4, V5.
- **ITF** (men and women): allowed in ALL 7 variants.
- **ATP, WTA**: allowed in ALL 7 variants.

### Doubles

V5 tests whether re-enabling doubles (blocked since Apr 18) helps or hurts.
V1-V4 and V6-V7 all still block doubles.

### Match-order effect (known, not yet filtered)

Orders 3-5 on same match lose ~$29 on 275 trades ex-outliers. Real effect,
not yet implemented. Candidate for v8.

### Previous v6 findings (carried forward)

- v5-madrid (Apr 21 morning, 9h): A=+$55.66/905, B=+$22.48/290, total +$82
- v6 (Apr 21 afternoon, 9h, still running): V3 = +$55 on 312 trades, solid
- Running 2-day total across all sessions: ~+$121 over 1356+ A-style trades

---

## 5. STANDARD COMMANDS

### Check live state
```powershell
Invoke-RestMethod http://localhost:8887/api | ForEach-Object { $_.strategies } | Format-Table
Invoke-RestMethod http://localhost:8888/api | ForEach-Object { $_.strategies } | Format-Table
```

### Launch template
```powershell
$env:APITENNIS_KEY="13762e9717adb7558813a0391642900f108682c092b1dee348ada134bfae8ce6"
$env:REDIS_URL="redis://127.0.0.1:6379/0"
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
$ts = Get-Date -Format "yyyyMMdd_HHmm"
Start-Process -FilePath ".\venv\Scripts\python.exe" `
  -ArgumentList "tennis_multi_v7.py","--minutes","540","--poll","5","--stake","10",`
  "--port","<PORT>","--max-session-loss","25","--hard-cap","1.0" `
  -RedirectStandardOutput "tennis_run_v7_${ts}_stdout.log" `
  -RedirectStandardError "tennis_run_v7_${ts}_stderr.log" `
  -PassThru -NoNewWindow
```

---

## 6. DATA FILES IN WORKING DIRECTORY

`C:\Users\omola\Documents\krypt-agent\crypto-agent\`

Core analysis CSVs:
- `_v5madrid_all_back.csv` (1,222 BACK trades, v5-madrid)
- `_trades_with_outcomes.csv` (1,169 trades joined with match outcomes)
- `_trades_categorized.csv` (+ category A/B/C/D)
- `_trades_with_entry_state_v2.csv` (+ reconstructed entry states, fixed tz "bug")

Log files (running):
- `tennis_run_v6_20260421_1433_*.log`
- `tennis_run_v7_20260421_2100_*.log`

Analysis scripts (reusable): `_entry_state_v2.py`, `_state_sim_v2.py`, `_state_x_tier.py`, `_trade_vs_match_outcome.py`, `_zone_drilldown.py`, `_deep_v5madrid.py`, `_jumps_analysis.py`

Test files: `_test_entry_state.py`, `_test_state_e2e.py`, `_test_blocked_tiers.py`

---

## 7. KNOWN TOOL QUIRKS (Windows dev env)

- Desktop Commander `interact_with_process` with multi-line heredocs hits Python REPL continuation trap → write `.py` file then exec
- Windows-MCP `PowerShell` tool sometimes times out silently but command succeeded → verify via process list or new powershell session
- PowerShell cp1252 → set `$env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"`
- Literal `$<digits>` in PS strings becomes variable ref → use single-quotes or `\$`
- Two concurrent processes on api-tennis = ~24 req/min combined, well under rate limits

---

## 8. RESUME PROMPT FOR NEW CHAT

> I'm resuming the krypt-agent tennis paper-trading session. Please read
> `C:\Users\omola\Documents\krypt-agent\crypto-agent\state_of_play.md` for
> full context before doing anything. v6 (port 8887) and v7 (port 8888) are
> running unchanged with 7 variants on v7. Don't touch the processes unless
> I explicitly ask.
>
> Also read the transcript journal if you have access to previous sessions.
>
> I'll tell you what to do next. For now, just confirm both dashboards are
> responsive and summarize the last state you see.
