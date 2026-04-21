# KRYPT-AGENT: Autonomous Crypto Trading System

## Architecture Overview

A dual-system autonomous trading agent with a continuous strategy promotion pipeline.

```
┌─────────────────────────────────────────────────────────┐
│                    KRYPT-AGENT                          │
│                                                         │
│  ┌──────────────┐    Promotion    ┌──────────────────┐  │
│  │  SYSTEM A     │ ──────────────▶│  SYSTEM B         │  │
│  │  (Paper Lab)  │    Pipeline    │  (Live $1,000)    │  │
│  │               │                │                    │  │
│  │ Tests new     │  ◀────────────│  Demotes failing   │  │
│  │ strategies    │    Demotion    │  strategies back   │  │
│  │ every hour    │                │                    │  │
│  └──────┬───────┘                └────────┬───────────┘  │
│         │                                  │              │
│         ▼                                  ▼              │
│  ┌──────────────────────────────────────────────────┐    │
│  │              RISK MANAGER (Supreme Authority)     │    │
│  │  • Drawdown kill switch                           │    │
│  │  • Per-trade stop-loss                            │    │
│  │  • Profit ratchet                                 │    │
│  │  • Correlation guard                              │    │
│  │  • No leverage — ever                             │    │
│  └──────────────────────────────────────────────────┘    │
│                          │                                │
│                          ▼                                │
│  ┌──────────────────────────────────────────────────┐    │
│  │              MARKET INTELLIGENCE                  │    │
│  │  • Regime Detector (trending/ranging/volatile)    │    │
│  │  • Funding Rate Monitor                           │    │
│  │  • Order Book Depth Analyzer                      │    │
│  │  • Volatility Scanner                             │    │
│  └──────────────────────────────────────────────────┘    │
│                          │                                │
│                          ▼                                │
│  ┌──────────────────────────────────────────────────┐    │
│  │              EXCHANGE LAYER (ccxt)                 │    │
│  │  • Binance / Bybit / Pionex                       │    │
│  │  • Maker-only limit orders                        │    │
│  │  • WebSocket price feeds                          │    │
│  │  • Trade-only API keys (no withdrawal)            │    │
│  └──────────────────────────────────────────────────┘    │
│                          │                                │
│                          ▼                                │
│  ┌──────────────────────────────────────────────────┐    │
│  │              DASHBOARD (Web UI)                    │    │
│  │  • Live P&L tracking                              │    │
│  │  • Strategy performance leaderboard               │    │
│  │  • System A vs System B comparison                │    │
│  │  • Alerts & notifications                         │    │
│  └──────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## Hourly Strategy Promotion Cycle

```
EVERY HOUR:
│
├─ 1. Market Intelligence scans current conditions
│     → Determines regime: RANGING / TRENDING_UP / TRENDING_DOWN / HIGH_VOLATILITY
│
├─ 2. Strategy Generator creates variant suited to current regime
│     → Adjusts: pair, grid spacing, entry thresholds, stop-loss width
│     → Does NOT invent random strategies — tunes proven templates
│
├─ 3. System A (Paper) deploys variant
│     → Simulates trades against real-time prices
│     → Tracks: win_rate, profit_factor, max_drawdown, sharpe_ratio
│
├─ 4. At hour end, Strategy Evaluator scores results
│     │
│     ├─ PASS (win_rate > 55%, profit_factor > 1.5, max_drawdown < 1%):
│     │   → Strategy promoted to System B queue
│     │   → Starts with 5% capital allocation ($50)
│     │   → Allocation increases if live performance matches paper
│     │
│     ├─ MARGINAL (close to thresholds):
│     │   → Strategy gets one more hour of paper testing
│     │
│     └─ FAIL:
│         → Strategy logged with market conditions
│         → Parameters blacklisted for similar conditions
│         → Feeds learning: "grid 0.2% spacing fails when BTC vol > 3%"
│
├─ 5. System B reviews active strategies
│     → Any strategy underperforming its paper metrics by >30%: DEMOTED
│     → Any strategy hitting per-strategy drawdown limit: KILLED
│     → Capital reallocated to best performers
│
└─ 6. Dashboard updated with full cycle results
```

## Strategy Templates

### 1. Micro Grid (Ranging Markets)
- Places buy/sell orders in a tight range (0.2-0.5% spacing)
- Works when price oscillates sideways
- Expected: 10-30 micro-trades per hour
- Risk: price breaks out of range

### 2. Momentum Scalp (Trending Markets)
- Follows short-term momentum (5-15 min timeframe)
- Enters on pullbacks in trend direction
- Tight trailing stop-loss
- Expected: 3-8 trades per hour

### 3. Mean Reversion (High Volatility)
- Buys sharp dips, sells sharp spikes
- Uses Bollinger Bands + RSI extremes
- Very tight position sizing during volatility
- Expected: 1-3 trades per hour

### 4. Spread Capture (Low Volatility)
- Places limit orders on both sides of spread
- Market-making approach
- Only works on liquid pairs with decent spread
- Expected: 20-50 fills per hour

## Risk Management Rules (NON-NEGOTIABLE)

| Rule | Value | Action |
|------|-------|--------|
| Portfolio drawdown limit | 5% ($50 on $1000) | HALT all trading, alert |
| Per-trade risk | 0.5% ($5) | Hard stop-loss |
| Per-strategy allocation | Max 20% ($200) | Cap exposure |
| Correlation limit | Max 2 correlated positions | Block new entry |
| Leverage | ZERO | Never enabled |
| Daily loss limit | 2% ($20) | Halt for 4 hours |
| Profit ratchet | Every 10% gain | Floor moves up 5% |
| Max open positions | 5 | Queue additional |

## Profit Ratchet Example

```
Start:   $1,000  → Floor: $950 (5% drawdown limit)
Grow to: $1,100  → Floor: $1,045 (ratchets up)
Grow to: $1,210  → Floor: $1,150
Grow to: $1,331  → Floor: $1,265

The bot can NEVER give back more than ~5% from peak.
```

## Tech Stack

- **Language**: Python 3.11+
- **Exchange**: ccxt (Binance initially)
- **Data**: WebSocket feeds for real-time, REST for historical
- **Database**: SQLite (local, no infra needed) → PostgreSQL later
- **Scheduling**: asyncio event loop with hourly cycle
- **Dashboard**: React SPA served locally or on Railway
- **Alerts**: Telegram bot for notifications
- **Deployment**: Your local machine initially → VPS/Railway for 24/7

## File Structure

```
krypt-agent/
├── config.py              # API keys, risk params, exchange settings
├── main.py                # Entry point, orchestrates the hourly cycle
├── exchange.py            # ccxt wrapper, order execution, WebSocket
├── market_intel.py        # Regime detection, volatility, funding rates
├── strategy_engine.py     # Strategy templates + variant generator
├── paper_trader.py        # System A — simulated execution
├── live_trader.py         # System B — real execution
├── risk_manager.py        # Supreme authority, drawdown limits
├── promotion_pipeline.py  # Evaluates paper results, promotes/demotes
├── strategy_store.py      # SQLite — logs all strategies + results
├── dashboard/             # React dashboard
│   ├── src/
│   └── package.json
├── requirements.txt
└── README.md
```

## Phase Plan

### Phase 1: Foundation (Week 1-2)
- Exchange connectivity + WebSocket feeds
- Market intelligence (regime detection)
- Risk manager
- Paper trader (System A)
- SQLite strategy store

### Phase 2: Live Engine (Week 3)
- Live trader (System B)
- Promotion pipeline
- Telegram alerts
- Deploy on local machine

### Phase 3: Dashboard (Week 4)
- React dashboard
- Strategy leaderboard
- P&L charts
- Mobile-friendly

### Phase 4: AI Enhancement (Week 5+)
- LLM-powered strategy variant generation
- Sentiment analysis integration
- On-chain data feeds
- Multi-exchange arbitrage
