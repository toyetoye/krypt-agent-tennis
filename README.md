# krypt-agent-tennis

Live tennis paper-trading bot. Polls api-tennis.com every 5s, detects
momentum signals and dominance patterns, runs multiple filter-variant
strategies side-by-side, and serves a live dashboard.

**Paper trading only.** No real money at risk.

## What's running

Currently the `tennis_multi_v9.py` runner with 7 filter variants:

| Variant | What it tests |
|---|---|
| V3  | Control: skip_lay + skip Challenger + skip odds [1.60, 1.80) |
| V6  | V3 + skip 5 negative entry states (anchor) |
| V7  | V3 duplicate (A/A variance control) |
| V8  | V6 + smart-E cooldown (3 losses → 30min freeze) |
| V10 | V6 but ATP/WTA only (block ITF too) |
| V12 | V6 + hard_cap = $0.50 |
| V13 | V6 + hard_cap = $0.35 (target: max realised loss ≈ $0.50) |

V12/V13 are the loss-cap experiments. See `state_of_play.md` for the
current session context and what we're investigating.

## Deployment

Runs on Railway via Docker. Every push to `main` auto-deploys.

Required environment variables (set in Railway's Variables tab):

- `APITENNIS_KEY` — api-tennis.com v3 key
- `PYTHONIOENCODING=utf-8` (default in Dockerfile, but explicit doesn't hurt)
- `PYTHONUTF8=1` (same)

Optional:

- `REDIS_URL` — prior filter is a no-op without this, which is fine

Exposed port: `$PORT` (set by Railway). Dashboard at `/`, API at `/api`.

## Running locally

```bash
# Clone
git clone git@github.com:toyetoye/krypt-agent-tennis.git
cd krypt-agent-tennis

# Python 3.13 venv
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt

# Copy .env.example to .env and fill in APITENNIS_KEY
cp .env.example .env
# (edit .env)

# Load env vars (Windows PowerShell)
Get-Content .env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { $env:($matches[1].Trim())=$matches[2].Trim() } }

# Run
python tennis_multi_v9.py --minutes 540 --poll 5 --stake 10 --port 8888 --max-session-loss 25 --hard-cap 1.0
```

Dashboard: http://localhost:8888/

## Files

**Production (shipped to Railway):**

- `tennis_multi_v9.py` — the runner
- `tennis_detector.py`, `tennis_strategy.py` — signal + trade logic
- `tennis_feed.py`, `tennis_feed_apitennis.py` — data source
- `tennis_dominance.py`, `tennis_entry_state.py` — additional filters
- `tennis_players.py`, `player_match.py` — rank/ELO enrichment (soft-fails gracefully if `sackmann_data/` missing, which it is on Railway)
- `tennis_multi_dashboard.html` — the dashboard UI
- `_elo_snapshot.json` — reference ELO data (300 KB)

**Not shipped:**

- `venv/` — rebuilt from `requirements.txt`
- `sackmann_data/` — 20 MB of CSVs; rank/ELO filters are not active in any v9 variant anyway
- `*.log` — Railway log retention handles this
- `_*.py`, `_*.ps1` — local analysis scratch
- `.env` — secrets

## Docs

- `state_of_play.md` — living doc of what's currently running and what we're investigating
- `TENNIS_STRATEGY_REVIEW_20260421.md` — academic research review (service-break detection, ranking inefficiencies)
- `SOCCER_STRATEGY_CONTEXT.md` — parked for a later project

## Acknowledgements

Built iteratively with Claude across multiple sessions. Most of the
filter variants and their justifications are documented inline in the
runner's module docstring.
