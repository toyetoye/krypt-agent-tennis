"""
KRYPT-AGENT - Tennis Odds Momentum Detector (Phase E)
Detects momentum swings in live tennis match odds.

Concept:
    - Odds SHORTENING (e.g., 2.00 -> 1.50) = player gaining momentum
    - Odds DRIFTING (e.g., 1.50 -> 2.00) = player losing momentum
    - A -15% odds move in 5 min = significant swing (like a break of serve)
    - We BACK the player whose odds are drifting (they're now undervalued)
    - We LAY the player whose odds are shortening (they're now overvalued)

    In tennis trading terms:
    - "Back" = bet FOR player to win (goes long on them)
    - "Lay" = bet AGAINST player (goes short on them)
    - Profit = back high, lay low (like buy low sell high in crypto)

Tuning notes (post-Apr 17 paper-test observations):
    - Tightened stop-loss 15% -> 11% because avg loser was 47% bigger than
      avg winner at default settings (113 trades over 61 min, net -$22.52).
    - Added max_odds_move_pct filter to reject spurious >=50% readings
      (suspended markets, half-over artifacts manifesting as -73/-80%).
    - Cooldown now keyed by (match_id, swing_direction) so an opposite-
      side signal on the same match is still allowed, but same-side
      repeats are blocked; cooldown bumped 120s -> 300s.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

from tennis_feed import TennisFeed, TennisMatch

logger = logging.getLogger("krypt.tennis_det")


class SwingType(Enum):
    BACK_HOME = "back_home"     # home odds drifting - back them
    BACK_AWAY = "back_away"     # away odds drifting - back them
    LAY_HOME = "lay_home"       # home odds shortening too fast - lay
    LAY_AWAY = "lay_away"       # away odds shortening too fast - lay


@dataclass
class TennisSignal:
    """A detected momentum swing - potential trade entry."""
    match_id: str
    match_label: str
    swing_type: SwingType
    player: str               # which player this is about
    current_odds: float       # current decimal odds
    odds_move_pct: float      # % change that triggered signal
    window_sec: float         # time window of the move
    confidence: float         # 0-1 score
    timestamp: float = field(default_factory=time.time)

    @property
    def age(self) -> float:
        return time.time() - self.timestamp


@dataclass
class TennisConfig:
    """Tunable parameters for tennis momentum detection."""
    # Detection thresholds
    min_odds_move_pct: float = 10.0    # min % odds change to trigger
    # Data from 100min paper run (2026-04-17): moves >=20% reverted rarely
    # (WR 42% at 20-25%, 29% at 30%+). Tightened 50.0 -> 20.0.
    max_odds_move_pct: float = 20.0    # ignore moves above this (no edge)
    windows: List[int] = field(
        default_factory=lambda: [120, 300, 600])  # 2m, 5m, 10m
    min_confidence: float = 0.55
    # Same data showed confidence score is INVERSELY correlated with
    # outcomes: conf 0.95+ had 31% WR, conf 0.75-0.85 had 54%+ WR.
    # Reject signals that score too high -- they're momentum-continuation,
    # not the momentum-reversal the strategy bets on.
    max_confidence: float = 0.85

    # Entry
    stake_amount: float = 10.0         # $ per bet (paper)
    # Stake-sizing scheme: 'flat' (default, stake=stake_amount) or 'conviction'
    # (stake scales with signal confidence).
    stake_scheme: str = "flat"
    stake_min: float = 5.0                 # floor for non-flat schemes
    stake_max: float = 20.0                # ceiling for non-flat schemes
    stake_conf_baseline: float = 0.70      # conf at which stake = stake_amount
    max_open_bets: int = 3

    # Exit
    target_odds_move_pct: float = 8.0  # take profit when odds move 8%
    stop_odds_move_pct: float = 5.0    # stop loss when odds move 5% against (was 11, flipped to enforce TP>SL asymmetry)
    max_hold_sec: float = 600.0        # max 10 min hold
    hard_cap_dollars: float = None     # None = off. If set, exit when unrealized PnL <= -this_value

    # Entry filters (optional, all default off)
    skip_odds_bands: tuple = None          # tuple of (lo, hi) tuples; skip entry if entry_odds in any band
    conf_skip_mid_lo: float = None         # with conf_skip_mid_hi, skip signals where lo <= conf < hi
    conf_skip_mid_hi: float = None         # both must be set to activate confidence filter
    # Inverse: REQUIRE conf within a range (skip outside). Both must
    # be set to activate. Used when we want a narrow 'sweet spot'.
    conf_require_lo: float = None          # skip if conf < this
    conf_require_hi: float = None          # skip if conf >= this

    # Match-level trade cooldown: max entries on a single match within a rolling window.
    # Prevents repeatedly fading the same match when it's structurally a loser
    # (e.g. trending match, not reverting). Both must be set to activate.
    max_trades_per_match_window: int = None   # max entries per match
    trades_per_match_window_sec: int = 3600   # rolling window size (default 1h)

    # --- Smart filter E: consecutive-loss cooldown (fork) ---
    # After N losses in a row on a match, freeze that match for cooldown_sec.
    # A win anywhere in the streak resets it. Captures 'trending matches'
    # that keep losing money from fade signals.
    max_consecutive_losses_per_match: int = None      # None = off
    consecutive_loss_cooldown_sec: int = 1800         # 30 min default

    # --- Smart filter F: rolling match-WR cooldown (fork) ---
    # After N trades on a match, require WR >= threshold over the whole history.
    # If it drops below, freeze match for cooldown_sec. Different from E because
    # it doesn't reset on single lucky win.
    min_match_wr_over_n: int = None                   # None = off
    min_match_wr: float = 0.30                        # WR threshold
    match_wr_cooldown_sec: int = 900                  # 15 min default

    # --- Smart filter G: adaptive odds-band WR (fork) ---
    # Track rolling WR per odds bucket. Skip entries in any bucket whose
    # rolling WR over adaptive_odds_wr_window trades drops below min.
    # Self-adjusting: whichever bucket starts losing, we stop trading it.
    adaptive_odds_wr_window: int = None               # None = off
    adaptive_odds_min_wr: float = 0.35
    adaptive_odds_buckets: tuple = (
        (1.30, 1.50), (1.50, 1.80), (1.80, 2.00),
        (2.00, 2.50), (2.50, 3.00), (3.00, 4.00),
        (4.00, 5.00), (5.00, 10.0))

    # Rank-based entry filters (None = off). "Fader" = the player our
    # side of the signal is ON. For a BACK signal, fader is the player we
    # back; for a LAY signal, the player we lay. Rank: lower number = higher.
    min_fader_rank: int = None          # e.g., 1: must be top-1 or better (off by default)
    max_fader_rank: int = None          # e.g., 100: fader must be ranked <=100
    min_rank_gap: int = None            # e.g., 20: |fader_rank - opp_rank| >= 20
    max_rank_gap: int = None            # e.g., 200: reject if gap bigger
    # ELO-based filters (Sackmann-derived; requires name-match success)
    min_fader_elo: float = None
    max_fader_elo: float = None
    min_elo_gap: float = None           # fader_elo - opp_elo >= min_elo_gap
    max_elo_gap: float = None           # or <= max_elo_gap
    use_surface_elo: bool = False       # if True use hard/clay/grass-specific ELO
    # When True, skip the trade if we couldn't resolve either player's ELO.
    # When False (default), missing data is treated as "no opinion" - pass.
    require_elo_data: bool = False
    # When True, skip the trade if we couldn't resolve either player's rank
    # from api-tennis. Default False = pass-through.
    require_rank_data: bool = False

    # --- Tier (event_type_type) filter ---
    # When set, only allow signals on matches whose event_type_type matches one
    # of these strings (substring match, case-insensitive). e.g. {'atp','wta'}
    # would allow 'Atp Singles' and 'Wta Singles' but skip 'Challenger Men Singles'.
    allowed_event_types: frozenset = None   # None = allow all
    # Tier BLOCKLIST (applied in addition to allowed_event_types).
    # If match's event_type_type contains ANY of these substrings, REJECT.
    # Used to skip known-losing tiers (e.g. Challenger) while allowing others.
    blocked_event_types: frozenset = None   # None = block nothing
    # Entry-state BLOCKLIST. If the classified entry state (from
    # tennis_entry_state.classify_entry_state) matches any of these, REJECT.
    # State labels are CASE-INSENSITIVE (uppercased on comparison).
    # Negative-edge states from v5-madrid backtest:
    #   BEHIND_SET1_HEAVY, BEHIND_LOST_SET1_BAGEL, AHEAD_SET1_HEAVY,
    #   AHEAD_WON_SET1_FADING, EVEN_LOST_SET1
    blocked_entry_states: frozenset = None   # None = no state filter
    # Block doubles matches (players separated by '/'). Default True
    # preserves the previous hard-coded behavior. Set False to allow
    # doubles matches into the strategy.
    block_doubles: bool = True

    # --- Dominance (bagel/bread) pattern filter ---
    # 'off'      = no dominance logic (classic momentum only)
    # 'required' = momentum signal must also match a dominance pattern
    #              (hybrid: fires only when BOTH align, on the target player)
    # 'only'     = ignore momentum signals, emit dominance entries directly
    #              at the start of set 3 when pattern present
    dominance_filter_mode: str = "off"
    # Which sub-pattern(s) to accept: 'comeback', 'mirror', or 'both'
    dominance_patterns: str = "both"
    # Minimum BACK odds for dominance entry. Below this, edge negative.
    # E.g. 1.80 means we need at least 1/0.556 = 55.6% implied win prob.
    dominance_min_odds: float = 1.80
    # Maximum BACK odds: cap upside we'll go for (underdog too extreme).
    dominance_max_odds: float = 5.00

    # Directional filter: when True, reject all LAY signals.
    # Data shows LAY trades lose consistently across our sample.
    skip_lay_signals: bool = False

    # Filters
    min_odds: float = 1.20             # don't trade heavy favourites
    max_odds: float = 5.00             # don't trade big underdogs
    cooldown_sec: float = 300.0        # min between signals per (match, side)
    only_live: bool = True             # only trade in-play matches

    # Re-entry protection (acts at strategy layer)
    relose_cooldown_sec: float = 600.0 # block same side if we just SL'd it


# Group swing types into "directions" so the cooldown keys on
# (match_id, direction) instead of (match_id, exact swing). This lets a
# LAY_HOME signal fire even if BACK_HOME just fired, but blocks two
# BACK_HOMEs or two LAY_HOMEs in quick succession on the same match.
_SWING_DIRECTION: Dict[SwingType, str] = {
    SwingType.BACK_HOME: "back_home",
    SwingType.LAY_HOME:  "lay_home",
    SwingType.BACK_AWAY: "back_away",
    SwingType.LAY_AWAY:  "lay_away",
}


class TennisDetector:
    """
    Watches live tennis odds and detects momentum swings.
    Call tick() after each feed poll.
    """

    def __init__(self, feed: TennisFeed,
                 config: TennisConfig = None):
        self.feed = feed
        self.cfg = config or TennisConfig()
        # (match_id, direction) -> last signal timestamp
        self._last_signal_time: Dict[Tuple[str, str], float] = {}
        self._signals_detected = 0
        self._signals_rejected_cooldown = 0
        self._signals_rejected_extreme = 0

    def tick(self) -> List[TennisSignal]:
        """Check all live matches for momentum swings."""
        signals = []
        matches = self.feed.get_live_matches() if self.cfg.only_live \
            else self.feed.get_all_matches()

        for match in matches:
            sig = self._check_match(match)
            if sig is None:
                continue
            # Cooldown: same (match, direction) within cooldown_sec
            key = (match.match_id, _SWING_DIRECTION[sig.swing_type])
            last = self._last_signal_time.get(key, 0)
            if time.time() - last < self.cfg.cooldown_sec:
                self._signals_rejected_cooldown += 1
                continue
            self._last_signal_time[key] = time.time()
            signals.append(sig)
            self._signals_detected += 1
            logger.info(
                f"SWING [{match.label}] {sig.swing_type.value} "
                f"{sig.player} odds={sig.current_odds:.2f} "
                f"move={sig.odds_move_pct:+.1f}% "
                f"conf={sig.confidence:.2f}")

        return signals


    def _check_match(self, match: TennisMatch) -> Optional[TennisSignal]:
        """Check a single match for momentum swings, best one wins."""
        best_signal = None
        best_confidence = 0.0
        max_move = self.cfg.max_odds_move_pct

        def consider(move: float, swing: SwingType, player: str,
                     current_odds: float, window: int):
            nonlocal best_signal, best_confidence
            if current_odds <= 0:
                return
            abs_move = abs(move)
            if abs_move < self.cfg.min_odds_move_pct:
                return
            # Extreme-move filter: suspended-market / match-over artifacts
            # frequently show up as -60 to -90% readings. Reject these.
            if abs_move > max_move:
                self._signals_rejected_extreme += 1
                return
            if not (self.cfg.min_odds <= current_odds <= self.cfg.max_odds):
                return
            conf = self._score_signal(abs_move, window, current_odds)
            # Confidence is inverted in this strategy: high-score signals are
            # momentum continuations (trend), low-score signals are noise that
            # mean-reverts. We want the latter. Reject both tails.
            if conf < self.cfg.min_confidence:
                return
            if conf >= self.cfg.max_confidence:
                return
            if conf <= best_confidence:
                return
            best_confidence = conf
            best_signal = TennisSignal(
                match_id=match.match_id,
                match_label=match.label,
                swing_type=swing,
                player=player,
                current_odds=current_odds,
                odds_move_pct=move,
                window_sec=window,
                confidence=conf)

        for window in self.cfg.windows:
            home_move = match.home_odds_move(window)
            away_move = match.away_odds_move(window)
            if home_move > 0:
                consider(home_move, SwingType.BACK_HOME,
                         match.home_player, match.home_best_back, window)
            if home_move < 0:
                consider(home_move, SwingType.LAY_HOME,
                         match.home_player, match.home_best_lay, window)
            if away_move > 0:
                consider(away_move, SwingType.BACK_AWAY,
                         match.away_player, match.away_best_back, window)
            if away_move < 0:
                consider(away_move, SwingType.LAY_AWAY,
                         match.away_player, match.away_best_lay, window)

        return best_signal


    def _score_signal(self, move_pct: float, window: int,
                      odds: float) -> float:
        """Score signal confidence 0-1."""
        # Bigger move = stronger signal (capped at 2x threshold)
        move_score = min(1.0, move_pct / (self.cfg.min_odds_move_pct * 2))
        # Shorter window = faster swing = stronger
        window_score = 1.0 - (window / max(self.cfg.windows)) * 0.5
        # Odds near 2.00 (50/50) = most tradeable range
        odds_score = 1.0 - abs(odds - 2.0) / 3.0
        odds_score = max(0.0, min(1.0, odds_score))
        return move_score * 0.5 + window_score * 0.3 + odds_score * 0.2

    def get_stats(self) -> dict:
        return {
            "signals_detected": self._signals_detected,
            "signals_rejected_cooldown": self._signals_rejected_cooldown,
            "signals_rejected_extreme": self._signals_rejected_extreme,
        }
