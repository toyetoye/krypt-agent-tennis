# Soccer Trading Strategies — context database

**Date captured:** Apr 21, 2026
**Status:** Parked for later. No soccer work done yet; this is research notes
for when we branch krypt-agent into soccer.
**Source purpose:** When we pick up soccer, we don't want to re-crawl from
scratch. This doc captures what the public record says so we can start
evaluating edges quickly.

---

## Structural differences vs tennis (why this isn't a copy-paste job)

| Dimension | Tennis (our current) | Soccer |
|---|---|---|
| Score events | Many (every point) | Few (goals only; ~2.7/match avg EPL) |
| Odds movement | Continuous, volatile | Step functions at goals; slow drift between |
| Market depth | Good on ATP/WTA main draws | Massive on top leagues, thin on minor |
| Match duration | 1.5-5 hours | ~95 mins including added time |
| Primary markets | Match odds only | Match odds (1X2), Over/Under, Correct Score, BTTS, HT/FT |
| Key event | Service break | Goal (or near-goal: big chance, red card) |
| Momentum concept | Point/set runs | Pressure phases, xG accumulation |

**Implication:** tennis is a high-frequency swing-trading problem (5s polling
catches enough). Soccer is a *state-change* trading problem — you're
essentially holding positions that pay off on specific in-match events. Our
tennis detector architecture (±X% odds move triggers) would not transfer
directly. We'd want an event detector (goal scored, red card, big chance)
plus a position-holding framework.

---

## 1. The canonical soccer trading strategies (practitioner consensus)

### 1a. Lay the Draw (LTD) — the signature strategy
**Mechanism.** Pre-kickoff or at 0-0 early in the match, place a LAY on the
Draw in the Match Odds market. When a goal is scored, the Draw price rises
(because a draw is now less likely with fewer minutes left for the other
team to equalise). Back the Draw at the higher price to green up.

**When it works:** Strong home favourite with historical goal-scoring record
against away team, league with high goals-per-game, especially when the
favourite scores first.

**When it fails:** 0-0 at full time. Also fails if the *underdog* scores
first (draw price doesn't rise as sharply, because favourite is now priced
in to equalise).

**Market efficiency note:** This is the single most well-known soccer trade.
Practitioners in 2024-2025 posts say "lay the draw is not dead but it's
harder" — the market has adapted. Variants have emerged:
- **First-half LTD:** Lay the HT draw only; tighter window, smaller moves,
  but clear exit point at HT whistle.
- **Second-half LTD:** Lay draw around 60-82 mins when still level.
  Underdogs tire, late goals cluster.
- **Metaltone exit:** If underdog scores first, you're in trouble.
  Metaltone adds a LAY on the leader to hedge; rumoured to originate
  on the Betfair forum.

**Key filters used by pros (Goalstatistics, FootyAmigo):**
- League average goals > 2.5
- Draw % < 25% for the league
- Home team scores >= 1.5 goals/game, concedes <= 1.25
- Away team scores <= 1.25, concedes >= 1.25
- Head-to-head: avoid matchups that historically draw
- Teams with >15 shots-on-target average
- Pre-match draw odds between 3.2 and 5.0 (sweet spot)

### 1b. Back the Over (goals markets)
**Mechanism.** Back Over 0.5 / 1.5 / 2.5 goals in-play; green up when a
goal is scored and the over odds drop. Variants exist for both the
first-half goals market and full-match.

**Notable pattern (from practitioner posts):**
- Back Over 1.5 at 30' when 0-0 — odds drift upward as minutes pass with
  no goal, then crash if a goal lands before HT.
- Back Over 2.5 at 60' when 1-1 — draws at 60' have ~70% chance of
  another goal per one practitioner's claim (not verified).

### 1c. Lay the Under / Lay 0-0
Correlated with LTD but on the correct-score market.
**Advantage:** Cleaner than LTD — if a goal is scored, you win the full
lay, no need to trade out. **Disadvantage:** Thinner liquidity on correct
score markets, especially late in the match.

### 1d. Back the Leader After a Goal (squeeze trade)
**Mechanism.** Team A scores first. Their match-odds price crashes
(e.g. 2.5 → 1.5). Back them immediately — if they score a 2nd, price
crashes further. Green up.
**Downside:** If opponent equalises, price snaps back. This is a
time-decay play — the longer you hold with no 2nd goal, the less
you make.

### 1e. Fade the Early Goal (contrarian)
**Mechanism.** Heavy favourite concedes an early goal (e.g. 0-1 at 15').
Favourite's odds jump from 1.4 to maybe 2.5. Back the favourite, hoping
they equalise and price crashes back.
**Risk:** High — underdog may hold or extend. Pros use this selectively,
with strict stop-loss and match selection (only top-tier home favourites
with strong late-goal records).

### 1f. Correct Score Trading
Back multiple low-probability correct scores pre-match; one typically
shortens a lot in-play. Variant: lay high-probability correct scores and
green up as the match progresses away from those outcomes. Thin liquidity
makes this specialist territory.

### 1g. Time Decay / "Scalping Under" when leading
Favourite leads 2-0. Back Under 3.5 or Under 2.5 (whatever's available).
Every minute without another goal, Under odds shorten. Green up as price
moves. **Clean trade** when it works but you're short-vol — one late goal
blows it up.

---

## 2. Academic / modelling literature

### 2a. Poisson models (Maher 1982, then everyone)
Treat goals as two independent Poisson processes with rates λ_home,
λ_away derived from team attack/defence strengths. Gives a joint
distribution over (home goals, away goals) → probabilities for 1X2,
Over/Under, correct-score.

**Strengths:** Simple, interpretable, fast.
**Weaknesses:** Assumes goal independence (wrong — momentum exists),
overestimates high-scoring games, doesn't handle home advantage well
without adjustment.

**Well-known extensions:** Dixon-Coles (1997) adds a correlation correction
for low-scoring draws; bivariate Poisson (Karlis-Ntzoufris); time-decay
weighting of recent matches.

### 2b. Bayesian / hierarchical models (Rue-Salvesen 2000, Baio-Blangiardo 2010)
Team strengths as latent variables with priors, updated via MCMC.
Better at handling small samples (early season, new teams). Computationally
heavy but robust. Good baseline for a soccer v1.

### 2c. Machine learning approaches
- **LSTM / Transformers on odds time-series** (cabeywic/football-lstm-betting
  on GitHub) — predicts next-interval Betfair prices. Interesting framing
  for our setup but published work doesn't show consistent PnL.
- **Random forests / GBDTs on engineered features** (xG, possession, form,
  injury flags). Standard modern approach.
- **Draw-market ML classifiers** — referenced in the StefanBelo
  BetfairAiTrading community posts. Specifically noting that soccer draw
  is a commonly-targeted market because it's the most liquid 1X2 leg with
  the most inefficiency (human bettors underweight draws).

### 2d. In-play modelling
- **Dixon-Robinson (1998):** model goal-scoring rates as functions of match
  state (score, time remaining, minutes since last goal). This is THE
  canonical in-play soccer model.
- **Koopman-Lit (2015):** state-space bivariate Poisson, in-play updating.
- **xG-based live models:** incorporate expected-goals accumulation;
  several commercial providers (Opta, Stats Perform) sell this.

---

## 3. Known data sources and infrastructure

### Free-ish data
- **football-data.co.uk** — historical match results + closing odds for
  major European leagues back to the 90s. Free. Standard starting point
  for every DIY soccer modeller.
- **football-data.org** (API) — free tier with fixtures/results.
- **API-Football / API-Sports** — commercial but affordable; live scores,
  lineups, events. Similar class to our api-tennis provider.
- **Betfair Historical Data** — stream recordings (BZ2 files) for
  backtesting. Free to Betfair customers on request.
- **StatsBomb Open Data** — event-level data for some leagues/competitions.
  Gold for xG-style work.

### Infrastructure
Same as tennis: **Flumine + BetfairLightweight** (Python) for Betfair
execution and streaming. No need to rebuild.

### GitHub repos worth bookmarking
- `dashee87/betScrapeR` / related — Poisson model tutorial in Python,
  well-documented.
- `markoutso/betfair-strategy-tester` — Python framework for simulating
  soccer strategies against Betfair Match Odds CSV data. Good starting
  point for a backtest harness.
- `amankhoza/betfair-machine-learning` — scripts for collecting and
  cleaning Betfair football odds + ML toolkit.
- `cabeywic/football-lstm-betting` — LSTM/Transformer predictors for
  in-play odds.
- `betfair-datascientists` (Betfair-owned tutorials) — EPL ML walkthrough
  in Python, Poisson in R.

---

## 4. Strategic priorities if/when we start soccer

Ranked roughly by edge-per-effort:

1. **LTD with modern filters.** Build a filtered LTD system using
   league-level draw %, team-level goal rates, and in-play shot/corner
   thresholds (>= 3 shots+corners per 10 min window). This is the
   most-tested practitioner strategy and has the clearest entry/exit logic.
2. **Under-goals time-decay on leading favourites.** 2-0 or 3-0 at HT,
   back Under 3.5 or 4.5 for time-decay profit. Simple, high-probability,
   capped downside per trade.
3. **Dixon-Coles / Poisson baseline** for pre-match value bets.
   Well-documented, fast to implement, gives us a benchmark.
4. **In-play goal-rate model (Dixon-Robinson class).** Harder but opens
   up the full in-play market. Build after (1)-(3) are solid.
5. **Draw-market ML classifier.** Per community noise, the draw market
   is where ML has the most room. Park until we have a data pipeline.

## 5. Risks / things to watch for

- **Integrity.** Lower-tier soccer (Asian 2nd divisions, some African
  leagues) has known match-fixing issues. Stick to top-5 European +
  big South American leagues + MLS.
- **VAR / clock stoppages.** In-play soccer trading is now affected by
  VAR delays and added-time variance — a goal scored at 90+5 is common
  now. Older LTD literature (pre-2018) under-weights this.
- **Referee variance.** Red-card rate varies massively by ref. Any
  serious soccer model needs ref as a feature.
- **Post-COVID crowd effect.** Empty-stadium matches (2020-21) broke
  home-advantage assumptions. Training data from that window is
  contaminated.
- **Limit-risk.** Soccer in-play on Betfair is highly liquid but also
  highly efficient. The naive LTD edge from 2012-2015 is largely arbed
  out by algos; modern practitioners use layered filters just to break
  even with variance.

---

## 6. Sources (for re-verification when we come back)

Practitioner:
- myfootballtradingsystem.com (low-risk strategies catalogue)
- thetrader.bet/sports-trading/betfair-trading-strategies/football
- betfairtradingcommunity.com (LTD guide, 2025)
- footyamigo.com (LTD pro guide, 2025)
- caanberry.com/4-profitable-betfair-football-trading-strategies
- ukfootballtrading.com/laying-the-draw
- goalprofits.com/lay-the-draw
- goalstatistics.com/article/football-trading
- BetAngel forum thread 31099 (LTD selection & timing discussion)

Academic (for the model stack):
- Maher (1982) — original Poisson soccer model.
- Dixon & Coles (1997) — low-score correlation correction.
- Dixon & Robinson (1998) — in-play goal-rate model.
- Karlis & Ntzoufras (2003) — bivariate Poisson.
- Rue & Salvesen (2000) — Bayesian dynamic rating.
- Baio & Blangiardo (2010) — hierarchical Bayesian.
- Koopman & Lit (2015) — state-space extension.

Tutorials:
- dashee87.github.io (Poisson in Python walkthrough)
- betfair-datascientists.github.io/modelling/soccerModellingTutorialPython
- betfair-datascientists.github.io/modelling/EPLmlPython

Code:
- github.com/liampauling/flumine (execution framework, same as tennis)
- github.com/markoutso/betfair-strategy-tester
- github.com/amankhoza/betfair-machine-learning
- github.com/cabeywic/football-lstm-betting
- github.com/betfair-down-under/AwesomeBetfair (curated index)

---

*End of context. When soccer work begins, start with this doc + a fresh
literature pass to catch anything published since Apr 2026.*
