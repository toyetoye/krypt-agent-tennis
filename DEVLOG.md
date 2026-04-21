# KRYPT-AGENT — Development Log & Troubleshooting Reference
**Location:** `C:\Users\omola\Documents\krypt-agent\crypto-agent\`
**Last updated:** April 2026
**Agent 1:** `python main.py` | **Agent 2 (Watchdog):** `python watchdog.py`
**Dashboard:** Open `dashboard.html` in browser (served from `localhost:8877`)

---

## SYSTEM ARCHITECTURE

### Files
| File | Purpose |
|---|---|
| `main.py` | Agent 1 — market scanning, strategy testing, promotion pipeline |
| `watchdog.py` | Agent 2 — monitors losses, sends alerts, signals Agent 1 |
| `notifier.py` | Windows toast + Telegram notifications |
| `strategy_engine.py` | Generates strategy variants, consults learning history |
| `strategy_store.py` | SQLite persistence — all tables and queries |
| `market_intel.py` | OHLCV analysis, regime detection (trending/ranging/low_vol) |
| `promotion_pipeline.py` | Evaluates paper results → PROMOTE / RETRY / KILL |
| `exchange.py` | MEXC connector via ccxt (REST) |
| `risk_manager.py` | Drawdown limits, halt logic, equity tracking |
| `dashboard_api.py` | HTTP server on port 8877 |
| `dashboard.html` | Browser dashboard UI |
| `config.py` | All tunable parameters |
| `krypt_agent.db` | SQLite database |
| `ws_feed.py` | Phase A — real-time MEXC websocket feed (ticker + trades) |
| `order_manager.py` | Phase B — limit order lifecycle (paper + live modes) |
| `live_spread_capture.py` | Phase B — paired quote market-making engine |
| `live_test.py` | Phase B — live paper test runner |
| `spread_scanner.py` | Phase B — REST scan all MEXC pairs for spread + volume |
| `spread_scanner_v2.py` | Phase B — three-prong scanner (REST → WS → composite rank) |
| `ws_test.py` | WebSocket feed verification script |
| `ws_debug.py` | WS channel diagnostic script |

### Database Tables
| Table | Purpose |
|---|---|
| `strategies` | Strategy variants — status, params, metrics |
| `trades` | Every simulated trade — open + close in one row |
| `equity_snapshots` | Equity curve data points |
| `blacklist` | Failed param+regime combinations |
| `signals` | Watchdog → Agent 1 communication |
| `watchdog_log` | All watchdog interventions |
| `learning_history` | Lessons from kills, promotions, watchdog flags |
| `system_state` | KV store — equity, counters, etc. |

### Strategy Templates
| Template | Regime | Direction |
|---|---|---|
| `spread_capture` | low_volatility | Market-making (buy+sell) |
| `momentum_scalp` | trending_up / trending_down | Long or Short |
| `micro_grid` | ranging | Long (grid buys) + stop sells |
| `mean_reversion` | high_volatility | Long (buy dip, sell mean) |

---

## CONFIG (config.py) — KEY PARAMETERS

```python
paper_test_duration_seconds = 30   # Cycle time
min_win_rate = 0.55
min_profit_factor = 1.5
min_trades_for_evaluation = 5
```

---

## ISSUE LOG


### ISSUE 001 — P&L showing $0.0000 on all trades (118 trades, 0% win rate)
**Symptom:** Dashboard showed 118 trades but $0.0000 P&L and "--" win rate.
**Root cause:** `_paper_sim` used hardcoded `* 10` notional. For `spread_capture`:
`0.05 / 100 * 10 - 0.005 = 0.005 - 0.005 = $0.000` — fee exactly cancelled revenue every trade.
**Fix:** Replaced hardcoded `* 10` with `notional = qty * price` and proper fee rates.
- MEXC taker = 0.10%, maker = 0.02% (later corrected to 0% maker)
- All 4 strategies now use position-sized P&L

---

### ISSUE 002 — Cycle time too slow (60 minutes between cycles)
**Symptom:** Config had `paper_test_duration_minutes = 60`, waiting an hour between strategy evaluations.
**Fix:** Changed to `paper_test_duration_seconds = 150` (later reduced to 30s).
Also raised `min_trades_for_evaluation` from 3 → 5.

---

### ISSUE 003 — Only ETH/USDT appearing in trades
**Symptom:** Dashboard only showed ETH/USDT trades despite scanning 4 pairs.
**Root cause:** `best = conditions[0]` picked only the highest-confidence pair. One new variant
was generated per cycle for one pair only.
**Fix:** Replaced single-pair logic with per-pair loop. Each cycle now generates and evaluates
one candidate variant per pair simultaneously (BTC, ETH, SOL, XRP).
New structure: `self.paper_candidates = {}` dict mapping pair → (variant, condition).

---

### ISSUE 004 — Dashboard blank after rebuild
**Symptom:** Dashboard showed nothing after multi-chunk HTML rewrite.
**Root cause:** JavaScript was split across 5 separate `<script>` tags from chunk appends.
Variables like `equity`, `snapCount`, `open` declared inside `render()` in one script block
were invisible to `app.innerHTML+=` code in later script blocks at global scope.
**Fix:** Rewrote dashboard.html as single `<script>` block with all functions properly scoped.
`render()` now builds complete HTML string internally, sets `app.innerHTML` once.

---

### ISSUE 005 — Watchdog signals not reaching Agent 1
**Root cause:** `strategy_engine.blacklist_variant()` only wrote to in-memory list, never to DB.
Dashboard always showed "0 blacklisted". Watchdog signal check only looked at
`self.current_paper_variant` — missed if the flagged strategy was a different pair's candidate.
**Fix:**
- Every KILL verdict now calls `self.store.save_blacklist_entry()` to persist to DB
- Watchdog signal check now iterates all `self.paper_candidates` dict entries

---

### ISSUE 006 — "0 active" strategies on dashboard
**Root cause:** `active_strategies` in equity snapshot used `len(self.live_strategies)` which
is only populated in `--live` mode. Always 0 in paper-only mode.
**Fix:** Changed to `len(promoted_strategies)` (count from DB query for status="promote").

---

### ISSUE 007 — All sell trades showing as losses
**Symptom:** Every trade with side="sell" had negative P&L regardless of price direction.
**Root cause 1:** `"buy" if pnl > 0 else "sell"` — trade side was assigned based on outcome,
not direction. All losing trades got labelled "sell".
**Root cause 2:** `momentum_scalp` when `ch < 0` hardcoded `pnl = -(gross + cost) * 0.5`
— a guaranteed loss even though `ch < 0` means price fell = WINNING SHORT.
**Fix:** 
- Side now uses actual direction: `ch > 0` → "buy", `ch < 0` → "sell" for momentum
- Short trades (rsi_entry=65, trending_down) now correctly profit when price falls
- Removed random stop-out branching (candle-close sim confirms direction, no intra-candle reversal)


### ISSUE 008 — Equity bleeding (dropped from $1000 to $982 quickly)
**Symptom:** Equity curve dropping ~$4.50 per cycle.
**Root cause 1:** 10 old promoted strategies (qualified under fake sim) re-running each cycle
under realistic sim and losing money. Their status was "promote" but they had large
positive balances from the fake era that were now being eaten.
**Root cause 2:** `get_strategies_by_status("promoted")` queried wrong status string.
DB stores status as "promote" (not "promoted") — query always returned empty list,
so promoted strategies were never re-running AND never counted in active_strategies.
**Fix:**
- Reset 10 old promoted strategies back to "paper" status
- Fixed query to use `"promote"` not `"promoted"`
- Equity state reset to $1000.00 clean

---

### ISSUE 009 — $6 losses on single trades
**Symptom:** Individual trades losing up to $6 despite $0.50 cap.
**Root cause:** `qty = 0.02` for ALL pairs. At BTC $71k, notional = $1,420.
Two taker fees alone = $2.84. Stop-loss exits could hit $6+.
**Fix:** Switched to target notional model — `qty = TARGET_NOTIONAL / price`
- All pairs now trade ~$50-$200 notional regardless of price
- BTC: qty ≈ 0.0007, ETH: qty ≈ 0.023, SOL: qty ≈ 0.6, XRP: qty ≈ 38
- Hard $0.50 loss cap added: `if pnl < -MAX_LOSS: pnl = -MAX_LOSS`

---

### ISSUE 010 — No trades for 15+ minutes (strategies stuck at 0 trades, being killed)
**Symptom:** All new strategies generating exactly 0 trades and getting killed on retry.
**Root cause:** Profitability gates set too aggressively:
- `MOMENTUM_MIN_MOVE = 0.42%` — BTC 1m candles typically move 0.06-0.20%. Nothing passes.
- `SPREAD_MIN_SPREAD = 0.16%` — MEXC BTC/USDT spread is 0.001-0.002%. Impossible.
- `GRID_MIN_MOVE = 0.18%` — micro_grid uses 0% maker fee, so no gate needed.
**Fix:**
- Spread_capture: use `spread_min_pct` param (0.05%) as minimum, not impossible 0.16%
- Momentum: compute actual cost% at TARGET_NOTIONAL, gate = `move > cost × 1.1`
- Micro_grid: removed gate entirely (0% maker = winning trades are always free)
- Also reset old promoted strategies again to stop equity bleeding

---

### ISSUE 011 — Profitable shorts (sell high → buy low) showing as losses
**Symptom:** T-00354: SELL $82.87 → BUY $82.70 shows -$0.2180. Price fell = winning short.
**Root cause:** Random `stop_prob` branch fired regardless of candle direction. Even when
price confirmed the trade direction, ~20% of trades were randomly assigned a loss.
In a candle-close sim, direction is confirmed by close price — no intra-candle reversal risk.
**Fix:** Removed `stop_prob` entirely from momentum_scalp.
`pnl = gross - total_cost` always (positive when gate passes, by mathematical design).

---

### ISSUE 012 — Same entry/exit price on some trades
**Symptom:** Trades showing identical open/close price (e.g., BUY $82.89 → SELL $82.89).
**Root cause:** `abs(p - pp) < tiny threshold` — near-zero candle moves firing and recording
pointless trades at same price after 2dp rounding.
**Fix:** Added guard: `if abs(p - pp) < 0.0001 * pp: continue`
At SOL $83, skips any candle with < $0.0083 move.

---

### ISSUE 013 — Desktop Commander restricted to TAZERBO folder only
**Symptom:** `Path not allowed: C:/Users/omola/Documents/krypt-agent/...`
**Fix:** Added krypt-agent to Desktop Commander allowedDirectories via `set_config_value`:
```
allowedDirectories: ["C:\\Users\\omola\\Downloads\\TAZERBO\\TAZERBO",
                     "C:\\Users\\omola\\Documents\\krypt-agent"]
```


---

## DESIGN DECISIONS & RATIONALE

### Position Sizing — Target Notional Model
Instead of fixed `qty`, all strategies use:
```python
qty = TARGET_NOTIONAL / max(pp, 0.0001)   # TARGET_NOTIONAL = $75
notional = qty * pp  # Always ≈ $75 regardless of pair price
```
Reason: Fixed qty at 0.02 gave BTC $1,420 exposure vs XRP $0.03. Meaningless P&L on small pairs.

### Profitability Gate — Momentum
```python
cost_pct = 0.0007 + TAKER_RATE * 2   # avg slip + 2x taker = ~0.27%
if move_pct < cost_pct * 1.1: continue   # 10% margin above costs
```
Reason: At $75 notional, 2x taker fee = $0.15. Need move > ~0.30% to cover all costs.
Gate prevents trades where fees eat the profit before execution.

### No Random Stop-Outs in Momentum Sim
Reason: Candle-close sim confirms direction at close. If BUY $82.64 → SELL $82.72 (price up),
the trade was profitable — no intra-candle reversal data available to model otherwise.
Random stop-outs caused correct-direction trades to show losses (ISSUE 011).

### MEXC Fees — Why Spread_Capture is the Primary Strategy
- MEXC maker fee = **0%** (limit orders)
- MEXC taker fee = 0.10%
- Spread_capture uses maker orders = zero fee on entry AND exit
- Every spread captured is pure profit (minus inventory risk)
- This is the biggest real-world edge: 0% maker on MEXC vs 0.10% on most exchanges

### Dynamic Spread Sizing
Spread_capture scales notional based on candle activity:
```python
range_ratio = candle_range / spread_pct
notional = min(MAX, max(MIN, TARGET * (range_ratio / 2.0)))
```
Wider candle = more activity = bigger position

### Signal-Quality Position Scaling (Momentum)
```python
profit_ratio = move_pct / cost_pct
notional = min(MAX_NOTIONAL, max(MIN_NOTIONAL, TARGET_NOTIONAL * (profit_ratio / 2.0)))
```
Strong move (profit_ratio 4.0) → $150 notional
Borderline move (profit_ratio 1.1) → $25 notional

### Adaptive Trailing Stop (Phase C Momentum)
Fixed trailing stops kill winning trades. A 0.15% trail on a 0.20% entry
immediately triggers on the first tick reversal (Test 4: 17/20 losses in <12s).
Solution: 3-phase ratcheting:
```
Phase 1 (profit < 0.15%): wide stop at 0.50% — let the trade breathe
Phase 2 (profit 0.15–0.35%): medium trail at 0.30%
Phase 3 (profit > 0.35%): tight trail at 0.20% — lock gains
```
Stop only moves in the protective direction (never widens).
Result: Test 5 improved WR from 13% → 33%, avg hold from ~5s → ~28s.

### Signal Quality Filters (Phase C Momentum)
Three filters reject noise before signals are generated:
1. **Spread filter** (max 80 bps): PSAI at 240 bps generated 8 noise signals in 5 min
2. **Trade density** (min 15/min in window): rejects "spikes" from 1-2 stale trades
3. **Min price** ($0.04): CALCIFY at $0.006 had 1.64% per tick — exceeds 0.50% stop

---

## CURRENT SYSTEM STATE (as of April 14, 2026)

### What's Working — Candle-Based Agent (main.py)
- 4 pairs tested simultaneously per cycle (BTC, ETH, SOL, XRP)
- 30-second cycle time with 4 strategy templates
- Watchdog Agent 2 with status-filtered signals (no more flood)
- Learning history with RSI parameter clamping (no more dead zones)
- SQLite WAL mode for concurrent access
- UTF-8 logging (no more Windows codec errors)
- Dashboard on localhost:8877

### What's Working — Live WebSocket Infrastructure (Phase A/B/C)
- `ws_feed.py`: Real-time MEXC websocket feed (ticker + trades, 15+ updates/sec)
- `order_manager.py`: Full order lifecycle with paper + live modes
- `momentum_detector.py`: Spike detection with quality filters (spread, density, price)
- `momentum_strategy.py`: Adaptive 3-phase trailing stop, taker entry, TP/SL/max hold
- `momentum_test.py`: Live paper test runner with CLI args for all tunable params
- `spread_scanner.py` + `spread_scanner_v2.py`: Three-prong pair scanner
- Thread-safe architecture: async WS in background, sync reads from main loop
- Dependencies: ccxt.pro, protobuf 5.29.5, aiohttp ThreadedResolver
- Max 15 concurrent WS pairs (MEXC throttles beyond this — Issue 021)

### Strategic Position
**Spread capture is INVALIDATED.** Adverse selection at retail speed.
**Multi-strategy momentum+bounce is NET POSITIVE.** Tiered 30-min test
(+$0.30, 55% WR) confirmed: bounce on hyper-volatile coins (ARIA) +
momentum on moderate coins (GENIUS/ENJ) = correct architecture. Untiered
run lost -$9.85 on same coins — proving volatility routing is the edge.
Bounce ARIA trades hit $1.10 and $0.46 in single trades (4-11 seconds).
Overnight 6-hour run launched 23:31 WAT April 14 to validate 24/7 viability.
**Next: analyse overnight results, WS auto-reconnect, Railway deploy.**

### Known Limitations / Still To Do
- [ ] **PRIORITY: Analyse overnight 6-hour run results**
- [ ] WS auto-reconnect on ping-pong timeout (Issue 027) — critical for 24/7
- [ ] Build S3 (Volume Surge) and S4 (Range Breakout) strategies
- [ ] Dynamic pair rotation (scanner → hot-swap every 15 min)
- [ ] Railway VPS deployment for 24/7 operation
- [ ] MEXC account + KYC for live trading
- [ ] Telegram bot token setup
- [ ] Dashboard enhancements (per-strategy P&L charts)
- [ ] Web crawler for external strategy discovery (Phase 5)

---

## HOW TO RUN

### Start both agents (two terminals):
```
# Terminal 1 — Agent 1
cd C:\Users\omola\Documents\krypt-agent\crypto-agent
python main.py

# Terminal 2 — Agent 2 (Watchdog)
cd C:\Users\omola\Documents\krypt-agent\crypto-agent
python watchdog.py
```

### Reset equity to $1000 (after code changes):
```python
import sqlite3, json, time, os
os.chdir(r'C:\Users\omola\Documents\krypt-agent\crypto-agent')
conn = sqlite3.connect('krypt_agent.db')
c = conn.cursor()
for k,v in [('equity',1000.0),('peak_equity',1000.0),('floor_equity',950.0)]:
    c.execute('INSERT OR REPLACE INTO system_state (key,value,updated_at) VALUES (?,?,?)',
              (k,json.dumps(v),time.time()))
conn.commit(); conn.close(); print('Reset done')
```

### Reset promoted strategies (if bleeding equity):
```python
import sqlite3, os
os.chdir(r'C:\Users\omola\Documents\krypt-agent\crypto-agent')
conn = sqlite3.connect('krypt_agent.db')
conn.execute("UPDATE strategies SET status='paper' WHERE status='promote'")
conn.commit(); conn.close(); print('Done')
```

### Check DB status:
```python
import sqlite3, os
os.chdir(r'C:\Users\omola\Documents\krypt-agent\crypto-agent')
conn = sqlite3.connect('krypt_agent.db')
c = conn.cursor()
c.execute('SELECT status, COUNT(*) FROM strategies GROUP BY status')
print([r for r in c.fetchall()])
c.execute('SELECT COUNT(*), ROUND(SUM(pnl),4) FROM trades WHERE closed_at IS NOT NULL')
print(c.fetchone())
conn.close()
```


---

## PAPER SIM MODEL — CURRENT LOGIC (main.py _paper_sim)

### Constants
```python
MIN_NOTIONAL    = 25.0    # $ minimum per trade
TARGET_NOTIONAL = 75.0    # $ base position
MAX_NOTIONAL    = 200.0   # $ maximum per trade
MAX_LOSS        = 0.50    # $ hard loss cap per trade
TAKER_RATE      = 0.001   # 0.10% MEXC taker
MAKER_RATE      = 0.000   # 0.00% MEXC maker (KEY ADVANTAGE)
```

### Per-Candle Logic

**spread_capture:**
1. Check live spread ≥ spread_min_pct param (0.05%)
2. Check candle range ≥ 2× spread
3. 60% fill probability (queue position)
4. Notional scaled by candle activity
5. pnl = spread × notional (maker = 0%)
6. 20% chance of inventory risk: pnl -= unwind_cost

**momentum_scalp:**
1. Compute cost% = avg_slip + 2×taker ≈ 0.27%
2. Gate: move_pct ≥ cost% × 1.1 — else SKIP
3. Scale notional by profit_ratio (stronger move = bigger position)
4. Direction: rsi_entry≤35 = trending_up = LONG only | rsi_entry≥65 = SHORT only
5. Skip candles moving against direction
6. pnl = gross - total_cost (always positive when gate passes)
7. Hard cap: pnl = max(pnl, -MAX_LOSS)

**micro_grid:**
1. Check candle move within grid_spacing range (sp×0.5 to sp×2)
2. 75% fill probability (limit order queue)
3. Upward move: pnl = gross (0% maker fee = pure profit)
4. Downward move: pnl = -(gross + taker + stop_slip) [stop-loss exit]

**mean_reversion:**
1. Check candle range > 0.5%
2. 85% exit fill probability
3. Check capture_pct (30% of range) > cost_pct × 1.1
4. pnl = capture - entry_slip - taker (positive when gate passes)

---

## WATCHDOG AGENT — TRIGGER CONDITIONS

| Trigger | Condition | Action |
|---|---|---|
| consecutive_loss | 2 losing trades in a row on same strategy | force_reevaluate signal |
| threshold_breach | Net P&L < -$0.10 on last 5 trades | force_reevaluate signal |

Both triggers: Windows toast notification + Telegram (if configured).
Cooldown: 5 minutes between signals for same strategy.
Poll interval: 30 seconds.

### Telegram Setup (when ready):
Set env vars before running:
```
$env:TELEGRAM_BOT_TOKEN = "your_bot_token"
$env:TELEGRAM_CHAT_ID = "your_chat_id"
```

---

## GOING LIVE CHECKLIST

1. [ ] Paper test for minimum 72 hours — review promoted strategies
2. [ ] Verify at least 3 strategies with consistent positive P&L and WR > 60%
3. [ ] Open MEXC account (works from Nigeria, zero maker fee)
4. [ ] Complete KYC on MEXC
5. [ ] Generate API key + secret on MEXC
6. [ ] Set environment variables:
   ```
   $env:EXCHANGE_API_KEY = "your_key"
   $env:EXCHANGE_API_SECRET = "your_secret"
   ```
7. [ ] Test with `python main.py --live` (10 second cancel window)
8. [ ] Deploy to Railway VPS for 24/7 operation (no laptop dependency)
   - Use existing Railway account (same as FORCAP)
   - Deploy as worker service, not web service
   - SQLite persists between restarts

---

## FUTURE ROADMAP

### Phase 3 — Parallel Strategy Engine (Next Priority)
- Fetch OHLCV once per pair (already done)
- Use `concurrent.futures.ThreadPoolExecutor` to sim 1000+ strategies simultaneously
- Generate batch of N variants per cycle (not just 1 per pair)
- At $84k BTC, each sim takes <1ms → 1000 strategies in <2 seconds on ROG

### Phase 4 — Learning System Enhancement
- Currently: param nudging based on failure patterns
- Next: cluster successful param sets by regime, generate new variants near clusters
- Eventually: Bayesian optimisation of params within each regime

### Phase 5 — Web Crawler + Strategy Discovery
- Crawl: TradingView community scripts, GitHub algo-trading repos, r/algotrading
- Claude API translates strategy descriptions → Python sim code
- Sandbox validation (no imports except math/random)
- Auto-inject as new strategy templates
- Security: all generated code validated before execution

### Phase 6 — FORCAP Integration

---

## PHASE A — LIVE WEBSOCKET FEED (Completed)

### New Files
| File | Purpose |
|---|---|
| `ws_feed.py` | LiveFeed class — real-time MEXC websocket data via ccxt.pro |
| `ws_test.py` | Feed verification script |
| `ws_debug.py` | Diagnostic script for testing individual WS channels |

### Architecture
- Runs in background thread with own asyncio event loop
- Thread-safe: synchronous code reads via `get_*` methods (protected by `threading.Lock`)
- Uses `ccxt.pro.mexc` with `watchTicker` (bid/ask) + `watchTrades` (live trades)
- `watchOrderBook` not used — times out on MEXC in ccxt.pro (known issue)
- `ThreadedResolver` required — `aiodns` fails on Windows DNS
- Protobuf required for MEXC websocket message decoding

### Dependencies Added
```
pip install "protobuf==5.29.5"
```

### Key Findings from Live Data
- BTC/USDT spread: ~$0.01 (0.0 bps) — essentially zero
- ETH/USDT spread: ~$0.01 (0.04 bps)
- SOL/USDT spread: ~$0.01 (1.2 bps) — widest of the 4
- XRP/USDT spread: ~$0.0001 (0.74 bps)
- Ticker update rate: ~25.6/s across 4 pairs
- Trade stream: ~2.7 trades/s visible
- Real spreads are EXTREMELY tight on major pairs — confirms spread_capture
  on BTC/ETH will be near-impossible at retail speed

### API Reference
```python
from ws_feed import LiveFeed

feed = LiveFeed(pairs=["BTC/USDT", "SOL/USDT"])
feed.start()

# Thread-safe reads from synchronous code:
ob = feed.get_order_book("BTC/USDT")      # OrderBookSnapshot
spread = feed.get_live_spread("BTC/USDT")  # float (percent)
mid = feed.get_mid_price("BTC/USDT")       # float
bid, ask = feed.get_best_bid_ask("BTC/USDT")
trades = feed.get_recent_trades("BTC/USDT", n=20)
flow = feed.get_trade_flow("BTC/USDT", seconds=60)
stats = feed.get_stats()

feed.stop()
```

---

## PHASE B — ORDER MANAGEMENT + LIVE SPREAD CAPTURE (Completed — Strategy Invalidated)

### New Files
| File | Purpose |
|---|---|
| `order_manager.py` | ManagedOrder lifecycle: PENDING→PLACED→FILLED/CANCELLED |
| `live_spread_capture.py` | Real-time market-making engine with paired buy+sell quotes |
| `live_test.py` | Live paper test runner — connects all components |
| `spread_scanner.py` | REST scan of all MEXC USDT pairs for spread+volume |
| `spread_scanner_v2.py` | Three-prong scanner: REST scan → WS live observation → composite ranking |
| `ws_debug.py` | Diagnostic script for testing individual WS channels |

### Architecture
- `OrderManager`: Thread-safe order tracking with paper (simulated) and live modes
- `LiveSpreadCapture`: Places paired buy+sell limit orders to capture spread
  - Inventory skew: biases quotes to reduce net exposure
  - Stale quote cleanup: auto-cancels unfilled orders after `max_hold_sec`
  - One-sided fill handling: closes at mid-price on timeout
- `SpreadConfig`: Tunable parameters (min_spread_bps, order_notional, max_hold_sec, etc.)

### Live Test Results — THREE TESTS RAN

#### Test 1: SOL/USDT + XRP/USDT (tight spreads, 1-2 bps)
| Metric | Value |
|---|---|
| P&L | -$0.14 |
| Round trips | 11 (each +$0.003-$0.006) |
| One-sided | 13 (each -$0.01 to -$0.05) |
| Fill rate | 39.3% |

**Finding:** Fills are frequent but spread ($0.005) is too small to absorb
one-sided drift losses ($0.015 avg). Net negative.

#### Test 2: DOT/USDT + DRIFT/USDT + EIGEN/USDT + ENS/USDT (mid-cap, 12-18 bps)
| Metric | Value |
|---|---|
| P&L | -$0.04 |
| Round trips | 0 |
| One-sided | 9 |
| Fill rate | 0% round trips |

**Finding:** Spreads are wider but too illiquid — price rarely crosses both
bid and ask within order lifetime. Zero completed round trips.

#### Test 3: ELF + BROCCOLIF3B + COPPERINU + CAPTCHA + DUPE (wide spreads, 30-170 bps)
| Metric | Value |
|---|---|
| P&L | **-$4.26** |
| Round trips | **0** |
| One-sided | 11 (all losses, up to -$0.57 each) |
| Quotes placed | 450 |
| Fill rate | 0% round trips, 1.2% any fill |

**Finding:** Wide spreads are wide BECAUSE nobody trades. Every fill was
adverse selection — our buy fills when someone dumps (price falling),
sell never fills because price moved away. Losses per one-sided fill
are LARGER on wide-spread pairs ($0.27-$0.57 vs $0.015 on SOL).

### Three-Prong Scanner Results
Scanned 1,802 USDT pairs, observed 25 candidates live for 45 seconds each.

**Prong 1 — Widest Spreads:** ENM (850 bps), BLINKY (507 bps) — zero trades observed.
**Prong 2 — Most Active:** ELF (25.3 trades/min, 170 bps) — clear standout.
**Prong 3 — Sweet Spot (composite):** ELF scored 133.3 (5× higher than #2).

**But even ELF produced zero round trips in live testing.**

### CRITICAL CONCLUSION: Spread Capture is NOT Viable at Retail Speed

The fundamental problem is **adverse selection**:
1. Our buy limit order only fills when a real trader pushes price DOWN through it
2. After filling, price has moved AGAINST us — our sell order is now above market
3. The sell never fills (or fills at a loss)
4. This happens regardless of spread width: tight spreads = small losses per fill,
   wide spreads = large losses per fill, but both are net negative

Professional market makers solve this with sub-millisecond requoting (cancel before
the adverse move arrives). A Python script polling at 2-second intervals cannot do this.

**The infrastructure IS valuable** — ws_feed, order_manager, and scanner are exactly
what's needed for REACTIVE strategies (momentum, event-driven) rather than PASSIVE
strategies (market-making/spread capture).

### Dependencies Added
```
pip install "protobuf==5.29.5"
```

---

### Phase 6 — FORCAP Integration (Future)
- krypt-agent P&L feeds into FORCAP EOM logbook
- CII-style performance tracking per strategy
- Multi-vessel approach: run separate agent instances per fleet vessel

---

## PHASE C — MOMENTUM SPIKE DETECTOR (In Progress)

### New Files
| File | Purpose |
|---|---|
| `momentum_detector.py` | Spike detection: rolling 15/30/60s windows, volume + bias + quality filters |
| `momentum_strategy.py` | Position management: taker entry, adaptive trailing stop, TP/SL, max hold |
| `momentum_test.py` | Live paper test runner with CLI args for all tunable params + dashboard on :8878 |
| `bounce_detector.py` | Dump detection: watches for 1-15% drops, confirms with sell bias + volume |
| `bounce_strategy.py` | Bounce position: enters LONG near dump low, exits on partial retracement |
| `multi_test.py` | Multi-strategy runner: momentum + bounce simultaneously, shared WS feed |
| `pair_scanner.py` | Quick MEXC scan: top 50 USDT pairs by volatility x volume |
| `deep_scan.py` | Deep MEXC scan: cheap volatile coins (<$1) with tick-size analysis |
| `tick_check.py` | Verifies MEXC tick sizes vs stop-loss distance for pair viability |
| `pair_list.py` | Curated pair lists for different test configs |
| `run_multi.py` | Launch script for multi-strategy test (15 cheap volatile coins) |

### Architecture
- `MomentumDetector`: Watches live trade stream from `ws_feed.py`
  - Ingests trades via timestamp-based dedup (fixed from ID-based — Issue 020)
  - Analyses price moves in configurable time windows
  - Confirms with volume threshold + directional bias (buy% vs sell%)
  - Scores confidence: 35% move_size + 25% volume + 25% bias + 15% density
- `MomentumStrategy`: Acts on signals from detector
  - Taker entry with 3 bps slippage simulation
  - Trailing stop (default 0.25%), take profit (0.8%), hard stop (0.5%)
  - Max hold time (120s) to prevent stuck positions
  - Tracks win rate, total P&L, fees paid
- `MomentumConfig`: All tunable parameters in one dataclass

### Key Parameters
```python
min_move_pct = 0.25    # minimum % move to trigger (tuned up from 0.4)
windows = [15, 30, 60] # rolling window sizes (seconds)
min_volume_usd = 500   # minimum $ volume in window
min_trades = 5          # minimum trade count
min_buy_bias = 65       # % directional bias required
min_confidence = 0.60   # composite score threshold (tuned up from 0.50)
trailing_stop_pct = 0.20  # phase 3 tight trail (adaptive ratchets from 0.50)
take_profit_pct = 0.50
stop_loss_pct = 0.5
max_hold_sec = 120
# Signal quality filters
max_spread_bps = 80    # reject pairs with wider spread
min_trades_per_min = 15 # reject thin pairs
min_pair_price = 0.01   # reject sub-cent tokens (tick granularity)
warmup_sec = 60         # ignore signals after feed connect
```

### Live Test Results
**Test 1 (0.3% threshold, BTC/ETH/SOL/XRP, 3 min):**
- Zero signals — trade dedup bug caused 0% moves everywhere
- Fixed: replaced trade_id dedup with timestamp-based filtering

**Test 2 (0.15% threshold, BTC/ETH/SOL/XRP, 3 min, 7:12 AM WAT):**
- Data pipeline working correctly: 851 trades ingested, real moves computed
- BTC: 108 trades/min, $302k vol, +0.015% move (60s window)
- ETH: 100 trades/min, $142k vol, +0.007% move
- Zero signals — market too quiet at 7 AM WAT (biggest move was 0.047%)
- This is CORRECT behavior — no false signals during flat market

**Test 3 (0.10% threshold, 4 pairs, 3 min, 9:37 AM WAT):**
- 9 signals, 3 closed trades, ALL losers, P&L: -$0.61
- First 3 signals fired on first tick (startup false signals — Issue 020 warmup)
- 0.10% threshold is noise on majors — entries are essentially random
- Fee drag dominates: $0.45 in fees on $0.30 gross movement

**Test 4 (0.20% threshold, 30 pairs, 5 min, 10:36 AM WAT — fixed trail 0.15%):**
- 15 of 30 pairs DEAD (zero WS data) — Issue 021 discovered
- 27 signals, 23 trades, 3W/20L = 13% WR, P&L: -$4.75
- PSAI generated 8 noise signals (240 bps spread, 9 trades/min)
- Trail stop too tight: 17/20 losses exited in <12s on first tick reversal
- Prompted: warmup fix, quality filters (spread + density), WS pair cap

**Test 5 (0.20% threshold, 15 pairs, 5 min, 11:30 AM WAT — adaptive trail):**
- All 15 pairs connected successfully (30 streams)
- PSAI filtered out (spread filter), quality filters working
- 25 signals, 12 trades, 4W/8L = 33% WR, P&L: -$1.32
- CALCIFY caused 5 of 8 losses (tick granularity — Issue 022)
- **Excluding CALCIFY: gross P&L is POSITIVE (+$0.47)**
- Winners: GENIUS +$0.23 (TP 8s), BLESS +$0.16 (TP 15s), ENJ +$0.19 (TP 17s)
- Adaptive trail working: avg hold improved from ~5s to ~28s

**Test 8 — PEAK HOURS (0.25% threshold, 15 pairs, 30 min, 14:32-15:02 WAT):**
- $75 notional, 0.50% TP, 60s cooldown, $0.04 min price
- 54 signals, 40 trades, 13W/27L = 32% WR, P&L: -$4.77
- Total fees: $6.15 (49% of gross losses)
- GENIUS was ONLY net-positive pair (~+$0.14 over 12 trades, 4 TP hits)
- Majors (BTC/ETH/SOL/XRP): 7 trades, 0 wins, -$1.23 — Issue 024
- ENJ: 4 trades, 0 wins, -$1.44 — tick granularity Issue 025
- max_hold_time: 13 trades (33%), nearly all losses — Issue 026
- take_profit exits: 10 trades, avg +$0.32 net, avg 28s hold
- stop_loss exits: 6 trades, avg -$0.58 net (working as intended)
- **Key finding: strategy is profitable on correct pairs (GENIUS), needs filtering**

**Multi-Strategy v2 (Mom+Bounce, 15 cheap volatile, 5 min, 21:05-21:10 WAT):**
- $50 notional, adaptive stops, bounce target fix, flat_exit
- **FIRST NET POSITIVE: P&L = +$0.59 | 15 trades | 8W/7L | 53% WR**
- Momentum: +$0.42 (10 trades, 5W/5L)
- Bounce: +$0.17 (5 trades, 3W/2L)
- Total fees: $1.55 (covered by wins)
- Best cycle: ARIA dump-and-bounce = $1.24 in 21 seconds
- Bounce SKIP fix prevented 2 guaranteed-loss entries
- Projected hourly: $7.05 | daily: $169 (optimistic, needs 30-min validation)

**30-min Untiered Multi (Mom+Bounce, 15 cheap volatile, 30 min, 21:15-21:45 WAT):**
- Both strategies on ALL pairs — no routing
- 71 trades, 18W/53L = 25% WR, **P&L: -$9.85**
- Momentum: -$9.55 (59 trades, 12W/47L) — ARIA caused 18 trades/-$3.13
- Bounce: -$0.30 (12 trades, 6W/6L) — near break-even, stable
- **Key finding: momentum on hyper-volatile coins is poison, bounce is the answer**
- Issues 024-026 confirmed at scale

**30-min Tiered Multi (Mom on moderate + Bounce on hyper-vol, 30 min, 22:35-23:05 WAT):**
- **FIRST NET-POSITIVE 30-MIN TEST: P&L = +$0.30 | 22 trades | 12W/10L | 55% WR**
- Momentum (GENIUS/ENJ/BLESS/MYX): -$0.16, 14 trades, 7W/7L — break-even
- Bounce (ARIA/APR/COAI/CHECK/IRYS/ENJ): +$0.46, 8 trades, 5W/3L, 63% WR
- $10.15 improvement vs untiered run
- Best trades: BNC-02 ARIA +$1.10 (11s), BNC-01 ARIA +$0.46 (4s)
- Projected hourly: $0.60, daily: $14.32
- Overnight 6-hour run launched at 23:31 WAT (PID 49012)

### Issue Fixed
**ISSUE 020 — Trade dedup bug: all moves showing 0.0000%**
Trade ingestion used `trade_id == last_id` to dedup, which only skipped
one trade and re-ingested all others as duplicates. Price history flooded
with same-price entries → moves always computed as 0%. Fixed by tracking
`last_trade_ts` per pair and only ingesting trades with newer timestamps.

### Next Steps
- [ ] **PRIORITY: Run 30-min multi-strategy test (run_multi.py) to validate +$0.59/5min edge**
- [ ] WS auto-reconnect on ping-pong timeout (Issue 027)
- [ ] Build S3 (Volume Surge) and S4 (Range Breakout) strategies
- [ ] Dynamic pair rotation: scanner → hot-swap into feed every 15 min
- [ ] Railway VPS deployment for 24/7 operation
- [ ] MEXC account + KYC for live trading

---

### ISSUE 014 — Agent 1 crash: MOMENTUM_MIN_MOVE / MOMENTUM_COST_PCT undefined
**Symptom:** Agent 1 crashes with `NameError: name 'MOMENTUM_MIN_MOVE' is not defined` whenever
a `momentum_scalp` variant is generated.
**Root cause:** Constants `MOMENTUM_MIN_MOVE` and `MOMENTUM_COST_PCT` were referenced in `_paper_sim`
but never defined. They were described in the design docs but missed during code assembly.
**Fix:** Added both constants after MAKER_RATE in `_paper_sim`:
```python
MOMENTUM_COST_PCT = 0.0007 + TAKER_RATE * 2   # ~0.27%
MOMENTUM_MIN_MOVE = MOMENTUM_COST_PCT * 1.1    # 10% margin above breakeven
```

---

### ISSUE 015 — micro_grid sell trades showing negative P&L despite sell-high-buy-low
**Symptom:** Dashboard shows SELL $82.87 → BUY $82.70 = -$0.2204. Selling higher and buying
lower should be a profit if it were a real short. All buy→sell trades are positive,
all sell→buy trades are negative — identical pattern to ISSUE 007/011.
**Root cause:** `trade_side = "buy" if ch > 0 else "sell"` — when price drops (`ch < 0`),
the trade was labelled "sell" (short). But micro_grid is a LONG-ONLY strategy: it places
grid BUY orders. When price drops, a grid buy fills and then stops out at a loss.
The trade is fundamentally a LONG that lost, not a SHORT that won.
The "sell" label caused the dashboard to display it as a short trade (SELL→BUY)
with negative P&L, making profitable-looking shorts appear as losses.
**Fix:** Changed `trade_side` to always be `"buy"` for micro_grid:
```python
trade_side = "buy"   # micro_grid is LONG-only — all trades open as buys
```
Dashboard now correctly shows: BUY $82.87 → SELL $82.70 = -$0.17 (a losing long, as intended).

---

### ISSUE 016 — Watchdog signal flood (30+ stale signals per cycle)
**Symptom:** Agent 1 processes 30+ watchdog signals at the start of every cycle. Same strategy
IDs repeat endlessly (e.g., 7c05239f, 9ba35c46, d8d18524). None match current paper candidates.
**Root cause 1:** `check_all_strategies()` queried ALL strategies with trades in the last hour,
including killed/dead ones. Dead strategies still have losing trades in the DB, so they trigger
the watchdog every poll forever.
**Root cause 2:** `already_signalled()` only checked for UNPROCESSED signals within cooldown
(`processed_at IS NULL AND created_at > cutoff`). Once Agent 1 processes a signal (sets
`processed_at`), the cooldown no longer sees it, so the watchdog creates a fresh signal
for the same strategy on the very next poll.
**Fix:**
- Added `AND s.status IN ('paper', 'promote')` to the strategy scan query — dead strategies
  are no longer monitored
- Changed `already_signalled()` to check ALL recent signals regardless of `processed_at`
  status — once a signal is created, the 5-minute cooldown applies whether or not Agent 1
  has processed it yet

---

### ISSUE 017 — Unicode logging errors on Windows (cp1252 codec)
**Symptom:** Every log line containing ⚡, ─, or 📊 triggers a `UnicodeEncodeError: 'charmap'
codec can't encode character` traceback. Non-fatal (message still logged) but extremely noisy.
**Root cause:** Windows console defaults to cp1252 encoding. Python's `FileHandler` and
`StreamHandler` inherit this, which can't encode emoji or box-drawing characters.
**Fix:**
- Added `encoding="utf-8"` to `FileHandler` in both `main.py` and `watchdog.py`
- Added `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` and same for
  `sys.stderr` at the top of both files

---

### ISSUE 018 — Learning engine pushes rsi_entry into dead zone (no trades generated)
**Symptom:** All 4 pairs showing 0 trades cycle after cycle. Strategies retrying endlessly
then getting killed for "insufficient trades after max retries". Dashboard shows no activity.
**Root cause:** `_apply_learning()` nudged `rsi_entry` from 35 → 42 (× 1.2). After blacklisting,
`_adjust_away_from_blacklist()` pushed it to 52.5 (× 1.25). The sim code requires `rsi_entry ≤ 35`
for longs or `rsi_entry ≥ 65` for shorts — anything between is `else: continue`, so every candle
was skipped. Both functions applied blind multipliers with no parameter bounds.
**Fix:** Added parameter clamping after nudging in BOTH `_apply_learning()` and
`_adjust_away_from_blacklist()`:
```python
if "rsi_entry" in adjusted:
    rsi = adjusted["rsi_entry"]
    if rsi <= 50:
        adjusted["rsi_entry"] = min(rsi, 35)   # clamp to long zone
    else:
        adjusted["rsi_entry"] = max(rsi, 65)   # clamp to short zone
if "rsi_exit" in adjusted:
    adjusted["rsi_exit"] = max(50, min(80, adjusted["rsi_exit"]))
```
Trades resumed immediately after fix — ETH fired +$0.0464 on first cycle.

---

### ISSUE 019 — Agent 1 crash: "database is locked" (SQLite concurrency)
**Symptom:** Agent 1 crashed with `sqlite3.OperationalError: database is locked` during
`update_strategy_status()`. Dashboard showed "waiting for agent".
**Root cause:** Multiple processes (4 stale watchdog instances + Python REPL sessions from
manual DB resets) all competing for write locks on the same SQLite DB. SQLite's default
journal mode blocks concurrent writers.
**Fix:**
- Added `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` to both
  `strategy_store.py._conn()` and `watchdog.py.get_conn()`
- Added `timeout=10` to `sqlite3.connect()` calls
- WAL mode allows concurrent reads+writes; busy_timeout retries instead of immediately failing
- Killed all stale python processes before restart

### ISSUE 021 — MEXC WebSocket dead pairs beyond ~15 concurrent subscriptions
**Symptom:** When subscribing to 30 pairs (60 streams), the last 15 pairs show zero
data — no trades, no volume, no price movement — while the first 15 work normally.
**Root cause:** MEXC WebSocket likely throttles or silently drops subscriptions beyond
a connection limit. The `asyncio.wait` in `_feed_loop` doesn't detect this because
the tasks don't fail — they just receive no data.
**Fix:** Capped pair list at 15 for now. Future: implement dynamic pair rotation
(scanner identifies volatile pairs → hot-swap into the WS feed).

---

### ISSUE 022 — Sub-cent tokens break trailing stop (tick granularity)
**Symptom:** CALCIFY ($0.0061) produced 5 of 8 losses in Test 5. One tick = $0.0001 =
1.64% of price — exceeding the 0.50% hard stop. Every tick reversal immediately
triggers the stop. BLESS ($0.016) also affected: one tick = 0.61%.
**Root cause:** Trailing stop distance in absolute terms ($0.00003 at 0.50% of $0.0061)
is smaller than the minimum price increment. Price can't move less than one tick,
so every adverse tick triggers exit.
**Fix:** Added `min_pair_price` filter (default $0.01) to `MomentumConfig`. Signals
from pairs below this price are rejected in `_check_window()`.

---

### ISSUE 023 — Trailing stop too tight: 1-4s exits on first tick reversal
**Symptom:** Test 4 (0.15% fixed trail) — 17 of 20 losses exited in <12 seconds.
Winning trades prove the signal detects real moves, but the stop kills trades
before they can run. Average hold time ~5 seconds.
**Fix:** Implemented adaptive 3-phase trailing stop in `momentum_strategy.py`:
```
Phase 1 (profit < 0.15%): stop_loss_pct (0.50%) — let it breathe
Phase 2 (profit 0.15-0.35%): 0.30% — tightening
Phase 3 (profit > 0.35%): trailing_stop_pct (0.20%) — lock gains
```
Stop only ratchets UP (longs) / DOWN (shorts) — never widens. Result: Test 5
improved from 13% WR to 33% WR, avg hold from ~5s to ~28s.

---

*This file is maintained across chat sessions. Add new issues at the bottom of ISSUE LOG.*
*Update "CURRENT SYSTEM STATE" after each significant session.*

---

### ISSUE 024 — Majors (BTC/ETH/SOL/XRP) are noise generators for momentum strategy
**Symptom:** Peak-hours test: 7 trades on majors, 0 wins, -$1.23 total. All triggered
on 60s window "spikes" of 0.25-0.30% that are just normal volatility. Enter, go
sideways for 120s, exit at max_hold_time minus $0.15 fee.
**Root cause:** Major pairs move 0.02-0.07% per minute normally. A 0.25% move in 60s
doesn't indicate momentum — it's just a wider-than-average candle. The move has
already happened before entry, and mean-reversion is more likely than continuation.
**Fix:** Removed BTC/ETH/SOL/XRP from the momentum pair list. Strategy now runs on
volatile mid-caps only where 0.25% spikes are abnormal and indicate real momentum.

---

### ISSUE 025 — ENJ tick granularity at $0.047 (same class as CALCIFY Issue 022)
**Symptom:** 4 trades on ENJ, 0 wins, -$1.44. One tick = $0.0001 = 0.21%, which is
inside the 0.50% stop but close enough that 2-3 adverse ticks trigger exits.
**Fix:** Raised `min_pair_price` from $0.04 to $0.10 in MomentumConfig defaults and
CLI args.

---

### ISSUE 026 — max_hold_time exits bleed via fee drag
**Symptom:** 13 of 40 trades (33%) exit at max_hold_time (120s). Average gross P&L
near zero (-$0.05) but average net P&L is -$0.16 because of the $0.15 round-trip fee.
These are "no signal" trades — the spike was detected, but price went sideways.
**Root cause:** No mechanism to exit early when a trade isn't moving. The only options
are: trailing stop (needs adverse move), take_profit (needs favorable move), stop_loss
(needs big adverse move), or max_hold_time (waits 120s for nothing).
**Fix:** Added `flat_exit` condition: if unrealised P&L is between -0.05% and +0.05%
after 45 seconds, exit at market. Saves ~$0.10-$0.15 per trade vs waiting to 120s.

---

### ISSUE 027 — WS ping-pong keepalive timeout disconnects all streams
**Symptom:** After ~25 min, all 20 WS streams die simultaneously with
"Connection to wss://wbs-api.mexc.com/ws timed out due to ping-pong keepalive
missing on time". Reconnect loops every ~11s but never recovers.
**Root cause:** MEXC drops idle WS connections. ccxt.pro's auto-reconnect
retries but hits same timeout repeatedly. No backoff or session refresh.
**Fix:** Not yet implemented. Workaround: 30-min test duration stays within
the keepalive window. Production fix: add explicit reconnect with fresh session.

---

### ISSUE 028 — Dashboard KeyError when strategies have different pair lists
**Symptom:** Tiered routing (separate mom_pairs and bnc_pairs) causes
`KeyError: 'ARIA/USDT'` in dashboard `_stats()` because it iterates
`all_pairs` but calls `mom_det.get_market_state(pair)` — which only has
momentum pairs in its `_price_history`. Crashed main loop and dashboard.
**Fix:** Added try/except fallback: try `mom_det` first, fall back to
`bnc_det`, skip pair if neither has it. Applied to dashboard `_stats()`,
main loop market snapshot, and final report per-pair section.


---

## PHASE D — TOURNAMENT RUNNER (Evolutionary Strategy Selection)

### New Files
| File | Purpose |
|---|---|
| `tournament.py` | Evolutionary tournament: spawn N configs, run rounds, kill losers, mutate winners, cascade validate |
| `run_tournament.py` | Launch script — starts tournament as background process with logging |

### Architecture
- **Shared LiveFeed**: One WS connection, all contestants read from it
- **N Contestants**: Each has own MomentumDetector + MomentumStrategy + BounceDetector + BounceStrategy
- **Independent tracking**: Each contestant tracks its own P&L, win rate, trade count
- **Evolutionary loop**: After each round, rank by P&L → kill bottom 50% → mutate top 50% → refill
- **Cascade validation**: Survivors run extended 2-hour test → only net-positive strategies promoted
- **Dashboard**: http://localhost:8879 shows live leaderboard, round progress, elimination log

### Parameter Space (what gets randomised/mutated)
**Momentum (8 params):**
- min_move_pct (0.30-0.80%), min_confidence (0.55-0.85), trailing_stop_pct (0.15-0.40%)
- take_profit_pct (0.40-1.00%), stop_loss_pct (0.30-0.70%), max_hold_sec (60-180s)
- signal_cooldown_sec (45-150s), entry_notional ($30-$80)

**Bounce (7 params):**
- min_dump_pct (1.0-3.0%), min_confidence (0.45-0.75), retracement_target (0.25-0.55)
- stop_below_low_pct (0.3-1.0%), max_hold_sec (45-120s)
- signal_cooldown_sec (60-150s), entry_notional ($30-$80)

### Tournament Flow
```
Round 1: 8 random contestants → 30 min → rank → kill bottom 4
Round 2: 4 survivors + 4 mutants → 30 min → rank → kill bottom 4
Round 3: 4 survivors + 4 mutants → 30 min → rank → kill bottom 4
Round 4: 4 survivors + 4 mutants → 30 min → rank → kill bottom 4
Cascade: 2-4 survivors → 120 min extended validation
Promoted: Only net-positive, ≥5 trades, ≥40% WR survive
```

### Mutation Logic
- Survivors keep exact same configs (recreated with fresh strategy state)
- Mutants: pick random survivor parent, nudge all params ±25% within bounds
- Fixed params (windows, volume thresholds, spread filters) are NOT mutated
- Each contestant gets a fingerprint (MD5 of its param set) for tracking lineage

### How to Run
```powershell
# Quick test (20 min total)
cd C:\Users\omola\Documents\krypt-agent\crypto-agent
.\venv\Scripts\python.exe tournament.py --contestants 4 --rounds 2 --round-min 10 --cascade-min 0

# Full tournament (4h total)
.\venv\Scripts\python.exe run_tournament.py

# Background with logging
.\venv\Scripts\python.exe run_tournament.py
Get-Content tournament_YYYYMMDD_HHMM_stdout.log -Tail 30
```

### Results Output
- `tournament_results.json`: Full params of all survivors and promoted strategies
- `tournament.log`: Detailed log of every creation, kill, mutation, promotion
- Dashboard at http://localhost:8879 (live during run)

### Overnight Run Analysis (April 15 06:41 — 290 min)
**Result: -$25.79 | 248 trades | 35% WR | 86W/162L**
- Momentum: -$16.72 (161 trades) — trailing stop exits too fast, fee drag
- Bounce: -$9.35 (85 trades) — entering before dump finished
- Fees: ~$24.80 total (almost entire loss is fee drag)
- Take-profit exits: avg +$0.32 net, ~28s hold — THESE WORK
- Trailing stop exits: avg -$0.15 net, ~7s hold — killed by fees
- Root cause: strategy isn't bad at direction, it's drowning in 0.10% taker fees

**Key insight:** The tournament runner addresses this by testing different
TP/SL/trail/cooldown combinations simultaneously. A config with higher
min_move_pct (0.60%) and longer cooldown (120s) would trade ~50 times
instead of 248, saving ~$20 in fees with similar gross wins.

---

*Updated April 15, 2026 — Phase D tournament runner built*
*Next priority: Run first tournament, analyse results, iterate*


---

## PHASE E — TENNIS ODDS TRADING (Momentum Swing Detection)

### New Files
| File | Purpose |
|---|---|
| `tennis_feed.py` | Polls The Odds API for live tennis match odds, tracks history per match |
| `tennis_detector.py` | Detects momentum swings (odds drifting/shortening) with configurable thresholds |
| `tennis_strategy.py` | Paper trading: back/lay bets, green-up exits, P&L with commission |
| `tennis_test.py` | Test runner with dashboard on http://localhost:8880 |

### Architecture
- **Data source**: The Odds API (free tier: 500 req/month, $79/mo for 10K)
- **Polling**: Background thread, configurable interval (60s default)
- **Thread-safe**: Main loop reads via get_* methods, feed polls in background
- **Paper trading**: Simulates Betfair Exchange back/lay with 5% commission on winnings
- **Dashboard**: http://localhost:8880 (JSON API showing matches, odds moves, bets)

### How Tennis Trading Works (vs Crypto)
| Concept | Crypto | Tennis |
|---|---|---|
| Data source | MEXC WebSocket (real-time) | The Odds API (polled every 30-60s) |
| "Price" | Token price in USDT | Decimal odds (e.g., 2.50) |
| "Long" | Buy token | Back player (bet FOR them) |
| "Short" | Sell token | Lay player (bet AGAINST them) |
| Fee model | 0.10% per side (taker) | 5% of net winnings (commission) |
| "Momentum" | Price spike detection | Odds drift/shorten detection |
| Entry signal | Price moves > 0.25% in 60s | Odds move > 10% in 5 min |
| Exit | Trailing stop / TP / SL | Counter-bet to lock profit (green-up) |

### Key Parameters (TennisConfig)
```python
min_odds_move_pct = 10.0    # trigger on 10%+ odds change
windows = [120, 300, 600]   # 2min, 5min, 10min detection windows
min_confidence = 0.55       # composite score gate
stake_amount = 10.0         # $ per paper bet
target_odds_move_pct = 8.0  # take profit at 8% favourable move
stop_odds_move_pct = 15.0   # stop loss at 15% adverse move
max_hold_sec = 600.0        # max 10 min hold
min_odds = 1.20             # filter: no heavy favourites
max_odds = 5.00             # filter: no big underdogs
cooldown_sec = 120.0        # 2 min between signals per match
```

### Strategy Logic
1. **Detection**: Odds drifting > 10% in 5 min = player losing momentum
2. **Entry**: BACK the drifting player (market overreaction, they're undervalued)
3. **Or**: LAY the shortening player (momentum priced in, reversion likely)
4. **Exit**: When odds move 8% in our favour → green-up (counter-bet to lock profit)
5. **Stop**: If odds move 15% against us → cut loss
6. **Commission**: 5% of net winnings (Betfair model)

### How to Run
```powershell
# Get free API key at https://the-odds-api.com
$env:ODDS_API_KEY = "your_key_here"
cd C:\Users\omola\Documents\krypt-agent\crypto-agent
.\venv\Scripts\python.exe tennis_test.py --minutes 60 --poll 60 --stake 10

# Dashboard: http://localhost:8880
```

### Next Steps for Tennis
- [ ] Get The Odds API key (free signup)
- [ ] Run first paper test during a live ATP/WTA tournament
- [ ] Add Betfair Exchange API for real trading (needs Betfair account)
- [ ] Build tennis-specific tournament runner (same evolutionary logic)
- [ ] Add point-by-point data source for faster signal detection
- [ ] Integrate tennis P&L into unified dashboard with crypto

