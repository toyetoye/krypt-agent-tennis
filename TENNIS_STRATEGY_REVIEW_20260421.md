# Tennis Trading Strategy Review — external sources

**Date:** Apr 21, 2026
**Purpose:** Survey of publicly-known tennis in-play trading strategies and academic
models, to stress-test our v7 filter stack and identify candidate v8 ideas.

**Scope note.** Most public writing on tennis trading is blog-level folk wisdom, not
code. Genuinely profitable systems rarely get open-sourced. Where academic papers
exist, they mostly model match-win probability, not trading PnL. So the value here
is *directional* — what edges do practitioners and researchers think exist — not
turnkey algorithms.

---

## 1. Academic / modelling literature

### Klaassen & Magnus (2001, 2003) — the canonical point-level model
Two papers ground the entire in-play tennis modelling literature:
- **Klaassen & Magnus (2001, JASA):** "Are Points in Tennis iid?" — concludes that
  points in tennis are **not** iid. Winning the previous point has a positive effect
  on winning the current point, AND at "important" points the server is at a
  disadvantage relative to non-important points. Deviations from iid are small but
  real. Important for us: **confirms a micro-momentum effect exists.**
- **Klaassen & Magnus (2003, EJOR):** "Forecasting the winner of a tennis match."
  Simple Markov model where p_a and p_b (server win probabilities) are assumed
  constant through the match. Given the current score, back-recursion gives P(match
  win). The computer program `TENNISPROB` computes this instantly. Inversion: given
  a starting match probability (from bookie odds or a logit on world-ranking diff),
  you can estimate p_a and p_b. Then you recompute P(win) after every point.

### Easton & Uylangco (2010, IJF) — the bombshell for us
Compared Klaassen-Magnus model predictions vs live Betfair odds, point-by-point.
**Key findings:**
- Markets are highly efficient: bookmaker odds track the point-level model with
  **extremely high correlation**.
- The market **anticipates** upcoming service breaks and service holds up to 4
  points early. (That is: the market is forward-looking on serve dynamics.)
- The market does NOT **instantaneously** react to the tendency of players to lose
  more points than expected after conceding a break. This is a real inefficiency,
  persisting briefly after breaks.
- No such bias for service-game wins (markets handle them correctly).

**Implication for us.** The single biggest documented inefficiency is
**post-break price stickiness**: after player X concedes a break, empirically
they continue to underperform for several points, but the market corrects too
slowly. This is a direct candidate for a v8 filter — if we can detect a break
just occurred AND the price hasn't fully adjusted, FADE (LAY) the player who
just got broken. Our current detector fires on any ±10% swing; it doesn't
specifically know a service break occurred. Adding that signal could be a
meaningful edge.

### Kovalchik & Reid (2019, IJF) — dynamic calibration update
"A calibration method with dynamic updates for within-match forecasting."
Takes the Klaassen-Magnus iid starting estimate and updates parameters as the
match progresses, using observed serve/return performance *within* the match.
Outperforms static iid for late-match forecasting. **Implication:** our current
model assumes a player's underlying win probability is fixed by pre-match rank
+ ELO. A within-match updater (e.g., is this player over/under-serving their
prior?) could sharpen entries in sets 2-3.

### Huang, Knottenbelt, Bradley (Imperial, final-year project)
"Inferring tennis match progress from in-play betting odds." Direct inversion
of market odds back to estimated point-win probability. Not directly useful for
us (we already have scores) but confirms the odds carry rich point-level info.

### "Capturing Momentum" (arXiv 2404.13300, 2024)
LightGBM classifier for predicting "momentum swings" in a match using
point-level features. Feature importance study: **break points, point
differentials, and consecutive-point runs** rank highest. This maps directly
onto our state classifier — `LOST_SET1_BAGEL`, `AHEAD_WON_SET1_FADING` are
momentum-state proxies. The v5-madrid backtest that built our `NEG_STATES`
frozenset is essentially the same idea implemented via hand-curated states
instead of boosted trees. Candidate v9+: replace the hard-coded state filter
with a learned classifier over the same inputs.

### DeepTennis (Stanford CS230, 2019)
RNN on point-level data; predicts match winner. Recommends coupling with live
betting markets for value detection. Not directly useful but the feature list
(fatigue proxies, momentum) validates ours.

---

## 2. Practitioner wisdom — Betfair blogs and forums

These sources are blog-level and should not be treated as validated edges.
But they tell us what real tennis traders *watch for*, which cross-checks our
filter design.

### Caan Berry (caanberry.com)
Respected pro trader. On tennis specifically:
- Advocates for **value entry + patience** over constant scalping.
- Cautions against trading matches where one player dominates set 1 heavily —
  says the market already fully prices the dominance and upside is capped.
- "Stay away from matches where one player completely dominates another in the
  first set."
- **Maps directly onto our `AHEAD_SET1_HEAVY` negative-edge state.** Our backtest
  finding (n=9, -$0.33/trade) validates his instinct quantitatively.

### TradeShark Tennis (tradesharktennis.com) / Tennis Profits (tennisprofits.com)
Full-time tennis traders since 2008/2010. Both emphasize:
- **Match selection > strategy.** Most trades they skip.
- Track serve-hold %, break-point conversion, tie-break record.
- Key event: **"player just broken"** causes predictable price moves that can
  be traded. (Consistent with Easton-Uylangco finding above.)
- They trade **match odds**, not set/game markets.

### UK Football Trading (ukfootballtrading.com) tennis guide
Lists a catalogue of entry-point patterns by score state:
- "Lay the server who's serving for the set" — RvR trade, big if broken.
- "Lay the heavy favourite after set 1 win" — classic fade.
- "Gap-fill in tight games" — place back + lay 4-5 ticks apart, expect bounce.
- **Warns** about 1st-set dominance: price overreacts, then mean-reverts.

### SportsTradingLife / The Trader
More basic strategy overviews. Key recurring themes across all practitioner sources:
1. **Serve is everything** — price moves predictably around break points.
2. **Set 1 is noisy** — smart traders avoid heavy positions pre-set-2.
3. **Mean-reversion works on first-set blowouts** — when favourite wins set 1
   6-1 or 6-2, the market over-reacts; there's a fade opportunity.
4. **Tie-breaks are volatile** — pros split on whether to trade them.
5. **Surface matters** — clay is slower, fewer breaks, longer matches.
6. **Doubles markets are thinner, less predictable** — most pros avoid them.

### Forum consensus (BetAngel forum)
Tennis liquidity and micro-edge has decayed significantly since ~2015. Quoted
from an experienced trader: markets have "changed out of all recognition" and
simple scalping no longer works. This is important context: published strategies
from 2010-2015 may no longer be profitable because the exchange is now
dominated by algos that have already priced in the easy edges.

---

## 3. Code repositories (GitHub)

Honest assessment: there are essentially no production-quality open-source
tennis in-play trading bots. What exists:

### Infrastructure / frameworks
- **betfair-down-under/AwesomeBetfair** — curated list of Betfair tools.
  Worth bookmarking.
- **BetfairLightweight** (liampauling) — Python wrapper for Betfair API,
  streaming support. Production-grade.
- **Flumine** (liampauling) — framework on top of Lightweight. Handles
  order management, market subscription, backtesting. Every serious
  Betfair bot on GitHub uses this.
- **jmcarp/betfair.py** — older MIT-licensed wrapper.
- **BowTiedBettor/BetfairBot** — example bot in Flumine. Not tennis-specific.

### Tennis-specific (mostly modelling, not trading)
- Multiple ML model repos predicting match winners from pre-match features
  (ELO, ranking, h2h). Not in-play, so only useful as prior enrichment
  (which we already do via Sackmann ELO + ATP rank fetcher).
- Several pose-detection / video-analysis repos (TTNet, RallyClip,
  Pose2Trajectory). Irrelevant to trading.

### Betfair Data Scientists tutorials (betfair-datascientists.github.io)
Betfair-owned tutorial site with multi-part Flumine walkthroughs. Covers:
- How to connect to stream vs polling API.
- How to build a trading strategy in Flumine.
- How to backtest against historical stream data.
- Simple Aus Open tennis model (pre-match only).

**Takeaway:** Infrastructure is a solved problem (Flumine + Lightweight).
Strategy IP stays closed. Our position — using api-tennis.com polling
instead of Betfair stream — is structurally different because Betfair
stream gives microsecond-level order-book updates; api-tennis gives
5-second score+odds snapshots. That means most published scalping
strategies (tick-scalping, queue-position plays) are not even portable
to our setup. We're structurally limited to **swing trading** on
score-driven odds moves, which is exactly what we're doing.

---

## 4. What this tells us about our v7 design

**Things we got right (cross-validated by external sources):**

1. **First-set-dominance fade is a real pattern.** `AHEAD_SET1_HEAVY`
   and `AHEAD_WON_SET1_FADING` as negative-edge states align with both
   Caan Berry's advice and common practitioner wisdom. Our backtest
   found the magnitude (-$0.19 to -$0.33/trade); external sources
   confirm direction.
2. **Challenger tier blocking.** Practitioners universally warn that
   lower-tier matches are noisier and (occasionally) subject to
   integrity concerns. Our v3 blocklist is cautious and defensible.
3. **Doubles blocking.** Consensus pro opinion: avoid doubles. Our V5
   (doubles re-allowed) is already diverging downward in the current
   run, which will become our in-sample confirmation.
4. **Swing trading on ±10% odds moves.** Matches the "event-driven
   trading around breaks" approach that recurs in practitioner writing.

**Gaps — candidate v8+ ideas:**

1. **Service-break detection as a first-class signal.** Easton-Uylangco's
   key finding: the market under-reacts to the tendency of just-broken
   players to keep losing points. Our detector fires on price movement,
   which catches *most* breaks indirectly. But an explicit "break just
   occurred" flag — derived from score_home/score_away transitions — would
   let us trade the post-break inefficiency directly. Priority: HIGH.
   This is the single best-documented alpha in the tennis literature.
2. **Serving-for-the-set filter.** Practitioners trade the server when
   they're serving for the set; if broken, big price move. Could become
   a dominance pattern alongside our current `classify_dominance`.
3. **Within-match calibration (Kovalchik-Reid).** Update p_serve/p_return
   mid-match based on observed points; compare to pre-match prior to
   detect over/under-performers. Matches our existing rank/ELO enrichment
   layer — same spirit, but dynamic.
4. **Learned state classifier (LightGBM on entry states).** Replace the
   frozenset of 5 hard-coded NEG_STATES with a gradient-boosted model
   trained on our full trade history. Would require enough trade data
   (we're at ~1,400 A-style trades currently — borderline). Park until
   we have ~5k.
5. **Match-order filter.** Known loss pattern (orders 3-5 on same match
   lose ~$29/275 ex-outliers). Already flagged in state_of_play as a
   v8 candidate; this review doesn't change that priority.
6. **Tie-break mode.** Disputed territory but some pros claim edge on
   tie-break entries. Worth isolating — we currently lump tie-breaks
   into whichever set state they belong to.
7. **Surface awareness.** We currently have no surface feature. Clay
   matches have fewer breaks and lower volatility; our ±10% trigger
   may under-fire there. Nice-to-have, not urgent.

**Things we should NOT chase:**

- **Tick-scalping.** Published pro opinion is unanimous that this edge
  is dead on Betfair post-2015. Our 5-second polling precludes it anyway.
- **Pre-match value betting.** Different problem entirely; published ML
  models exist (rank+ELO logit achieves ~72% accuracy) but the edge vs
  efficient Betfair closing odds is thin.
- **Pose/video analysis as a signal.** Latency + data access make this
  infeasible for our setup.

---

## 5. Sources (for re-verification)

Academic:
- Klaassen & Magnus (2001) "Are Points in Tennis iid?" — JASA 96(454):500-509.
- Klaassen & Magnus (2003) "Forecasting the winner of a tennis match" —
  EJOR 148(2):257-267.
- Easton & Uylangco (2010) "Forecasting outcomes in tennis matches using
  within-match betting markets" — IJF 26(3):564-575.
- Kovalchik & Reid (2019) "A calibration method with dynamic updates for
  within-match forecasting" — IJF 35(2):756-766.
- Ingram "A point-based Bayesian hierarchical model..." — martiningram.github.io.
- arXiv 2404.13300 (2024) "Capturing Momentum: Tennis Match Analysis..."

Practitioner / blog:
- caanberry.com/tennis-trading-strategies
- tradesharktennis.com (blog)
- tennisprofits.com
- ukfootballtrading.com/tennis-trading
- sportstradinglife.com/2019/02/how-to-trade-tennis
- BetAngel forum thread 1256 (learning-to-trade-tennis)

Infrastructure:
- github.com/liampauling/flumine
- github.com/liampauling/betfair
- betfair-datascientists.github.io/tutorials (multi-part Flumine tutorials)
- github.com/betfair-down-under/AwesomeBetfair

---

## 6. Recommended next action

Build the **service-break detection signal** (item 1 in Gaps above). It is:
- Directly supported by Easton-Uylangco's published finding.
- Cheap to implement (we already have score_home / score_away in meta).
- Independent of our current momentum detector, so it can be added as a
  parallel entry path without disturbing v7's filter-stack experiment.
- Testable: we can backfill against our v5-madrid CSVs to see if
  just-broken players underperform their momentum-signal expectation.

Propose a v8 design: add `break_occurred=True/False` flag to match meta;
new entry path "LAY player who just got broken" with cooldown + odds band;
keep running alongside v6/v7 variants rather than replacing them.

---

*End of review. Nothing here is validated on our live paper session yet —
external material is input to decision-making, not proof.*
