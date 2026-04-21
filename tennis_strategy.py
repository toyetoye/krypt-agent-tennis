"""
KRYPT-AGENT - Tennis Paper Trading Strategy (Phase E)
Acts on signals from TennisDetector.
Simulates back/lay bets and tracks paper P&L.

How P&L works in tennis trading:
    BACK bet: Stake $10 at odds 2.50
        -> Player wins: profit = $10 * (2.50 - 1) = $15
        -> Player loses: loss = -$10
    LAY bet: Stake $10 at odds 2.50
        -> Player loses: profit = $10 (keep the stake)
        -> Player wins: loss = $10 * (2.50 - 1) = -$15

    Trading (not gambling): we don't wait for match result.
    Instead we exit when odds move in our favour:
        BACK at 2.50, odds drop to 2.00 -> lay at 2.00 to lock profit
        LAY at 1.50, odds rise to 1.80 -> back at 1.80 to lock profit

Re-entry protection (added post-Apr 17 paper test):
    After a bet stops out, the (match_id, direction) it was on is blocked
    from new entries for relose_cooldown_sec. This prevents the
    "S. Lamens BACK stopped out 3 times in a row" pattern seen in
    the 61-min paper run. Opposite-direction entries on the same match
    are still allowed.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from collections import deque

from tennis_feed import TennisFeed, TennisMatch
from tennis_dominance import classify_dominance, is_set3_or_later
from tennis_detector import (TennisDetector, TennisSignal,
                              TennisConfig, SwingType,
                              _SWING_DIRECTION)

# Rank/ELO enrichment (fail-soft: if imports fail, filters pass through)
try:
    from tennis_players import PlayerRankFetcher
    from player_match import get_matcher
    _RANK_ELO_AVAILABLE = True
except ImportError as _e:  # noqa
    _RANK_ELO_AVAILABLE = False
    PlayerRankFetcher = None  # type: ignore
    get_matcher = None  # type: ignore

# Redis is optional. If missing or unreachable the prior filter is a no-op.
try:
    import redis  # type: ignore
except ImportError:
    redis = None  # type: ignore

logger = logging.getLogger("krypt.tennis_strat")

COMMISSION_RATE = 0.05  # Betfair-style 5% commission on net winnings


@dataclass
class TennisBet:
    """A paper bet being tracked."""
    bet_id: str
    match_id: str
    match_label: str
    player: str
    bet_type: str          # "back" or "lay"
    swing_type: SwingType  # original signal type (for re-entry blocking)
    odds: float            # decimal odds at entry
    stake: float           # $ risked
    entry_time: float = field(default_factory=time.time)
    # Exit
    exit_odds: float = 0.0
    exit_time: float = 0.0
    pnl: float = 0.0
    exit_reason: str = ""
    closed: bool = False

    @property
    def age(self) -> float:
        return time.time() - self.entry_time

    @property
    def liability(self) -> float:
        """Max loss if bet loses."""
        if self.bet_type == "back":
            return self.stake
        else:  # lay
            return self.stake * (self.odds - 1)


class TennisStrategy:
    """Paper trading strategy for tennis odds momentum."""

    def __init__(self, feed: TennisFeed, detector: TennisDetector,
                 config: TennisConfig = None,
                 redis_url: Optional[str] = None,
                 prior_min_confidence: float = 0.60,
                 prior_min_divergence: float = 0.05):
        self.feed = feed
        self.detector = detector
        self.cfg = config or TennisConfig()
        self._bets: Dict[str, TennisBet] = {}
        self._bet_counter = 0
        # (match_id, direction) -> timestamp of last stop-loss exit
        self._recent_sl: Dict[Tuple[str, str], float] = {}
        # match_id -> deque of entry timestamps (for match cooldown filter)
        self._match_trade_times: Dict[str, deque] = {}
        self.entries_blocked_match_cooldown = 0
        # Smart filter E state
        self._match_loss_streaks: Dict[str, int] = {}
        self._match_cooldown_until: Dict[str, float] = {}
        self.entries_blocked_loss_streak = 0
        # Smart filter F state
        self._match_outcomes: Dict[str, deque] = {}
        self._match_wr_cooldown_until: Dict[str, float] = {}
        self.entries_blocked_match_wr = 0
        # Smart filter G state
        self._odds_bucket_outcomes: Dict[tuple, deque] = {}
        self.entries_blocked_adaptive_odds = 0
        self.total_pnl = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_commission = 0.0
        self.entries_blocked_relose = 0
        self.entries_blocked_doubles = 0
        self.entries_blocked_lay = 0
        # Prior-based filter counters (added Apr 18)
        self.entries_blocked_prior_agree = 0
        self.entries_blocked_prior_disagree = 0
        self.entries_passed_prior = 0
        self.entries_no_prior = 0
        # Connect to Redis lazily. If unreachable, prior filter is a no-op.
        self.prior_min_confidence = prior_min_confidence
        self.prior_min_divergence = prior_min_divergence
        self._redis: Optional[Any] = None
        effective_url = (redis_url if redis_url is not None
                         else os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
        if redis is None:
            logger.info("redis-py not installed; prior filter disabled")
        elif not effective_url:
            logger.info("No REDIS_URL; prior filter disabled")
        else:
            try:
                client = redis.Redis.from_url(effective_url, decode_responses=True,
                                              socket_timeout=1.0,
                                              socket_connect_timeout=1.0)
                client.ping()
                self._redis = client
                logger.info(f"Prior filter enabled "
                            f"(conf>={prior_min_confidence}, "
                            f"divergence>={prior_min_divergence})")
            except Exception as e:
                logger.warning(f"Redis unreachable ({e}); prior filter disabled")

        # Rank/ELO enrichment layer
        self._rank_fetcher = None
        self._name_matcher = None
        self._elo_snapshot = {}
        # Dominance pattern state
        self._dom_entered_matches: set = set()  # match_ids we've already entered dominance on
        self.dom_entries = 0
        self.dom_blocked_no_pattern = 0
        self.dom_blocked_odds = 0
        self.dom_blocked_not_set3 = 0
        self.dom_blocked_already_entered = 0
        self.entries_blocked_tier = 0
        self.entries_blocked_entry_state = 0
        self.entries_blocked_dom_mismatch = 0
        # Counters for new filter
        self.entries_blocked_rank = 0
        self.entries_blocked_elo = 0
        self.entries_missing_rank = 0
        self.entries_missing_elo = 0
        self.entries_passed_rank_elo = 0
        if _RANK_ELO_AVAILABLE:
            try:
                self._rank_fetcher = PlayerRankFetcher()
                self._name_matcher = get_matcher()
                elo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "_elo_snapshot.json")
                if not os.path.exists(elo_path):
                    elo_path = "_elo_snapshot.json"
                if os.path.exists(elo_path):
                    with open(elo_path, "r", encoding="utf-8") as _f:
                        self._elo_snapshot = json.load(_f)
                    logger.info(f"Rank/ELO layer ready: {len(self._elo_snapshot)} ELO players loaded")
                else:
                    logger.warning("_elo_snapshot.json not found; ELO filters will pass through")
            except Exception as e:
                logger.warning(f"Rank/ELO layer init failed ({e}); filters will pass through")
                self._rank_fetcher = None
                self._name_matcher = None
        else:
            logger.info("Rank/ELO modules not importable; filters disabled")

    def tick(self):
        """Run one cycle: check signals, manage bets."""
        # Manage open bets first
        self._manage_bets()
        # Check for new signals
        signals = self.detector.tick()
        for sig in signals:
            if self._can_enter(sig):
                self._enter(sig)

    def process_signals(self, signals):
        """Public method for multi-strategy runners.

        Performs the same cycle as tick() but uses a pre-computed signal list
        instead of calling detector.tick() itself. This allows N strategies
        to share one detector without consuming its cooldown state.
        """
        self._manage_bets()
        # For dominance 'only' mode, skip momentum signals entirely.
        # For 'off' and 'required', momentum signals flow as normal.
        if self.cfg.dominance_filter_mode != "only":
            for sig in signals:
                if self._can_enter(sig):
                    self._enter(sig)
        # Fire dominance-only entries if mode == 'only'
        if self.cfg.dominance_filter_mode == "only":
            self._check_dominance_entries()

    def _can_enter(self, sig: TennisSignal) -> bool:
        # --- Tier filter (allowed_event_types) ---
        if self.cfg.allowed_event_types is not None:
            meta = self.feed.get_match_meta(sig.match_id) if hasattr(self.feed, "get_match_meta") else {}
            event_type = (meta.get("event_type") or "").lower()
            allowed = {s.lower() for s in self.cfg.allowed_event_types}
            # Match if any allowed substring appears in event_type
            if not any(a in event_type for a in allowed):
                self.entries_blocked_tier += 1
                return False

        # --- Tier BLOCKLIST (blocked_event_types) ---
        if self.cfg.blocked_event_types is not None:
            meta = self.feed.get_match_meta(sig.match_id) if hasattr(self.feed, "get_match_meta") else {}
            event_type = (meta.get("event_type") or "").lower()
            blocked = {s.lower() for s in self.cfg.blocked_event_types}
            if any(b in event_type for b in blocked):
                self.entries_blocked_tier += 1
                return False

        # --- Entry-state BLOCKLIST (blocked_entry_states) ---
        # Skip entries when the live match state matches known-losing patterns.
        # Based on v5-madrid backtest: BEHIND_SET1_HEAVY, BEHIND_LOST_SET1_BAGEL,
        # AHEAD_SET1_HEAVY, AHEAD_WON_SET1_FADING, EVEN_LOST_SET1 all have
        # significantly negative edge per trade.
        if self.cfg.blocked_entry_states is not None:
            meta = self.feed.get_match_meta(sig.match_id) if hasattr(self.feed, "get_match_meta") else {}
            try:
                from tennis_entry_state import classify_entry_state
                backed_side = "home" if sig.swing_type in (SwingType.BACK_HOME, SwingType.LAY_HOME) else "away"
                # Note: for BACK signals, backed_side is the side we're backing.
                # swing_type BACK_HOME means we BACK home; BACK_AWAY means we BACK away.
                if sig.swing_type == SwingType.BACK_HOME:
                    backed_side = "home"
                elif sig.swing_type == SwingType.BACK_AWAY:
                    backed_side = "away"
                elif sig.swing_type == SwingType.LAY_HOME:
                    # LAY home means we're shorting home, effectively "backing" away to lose
                    backed_side = "away"
                else:  # LAY_AWAY
                    backed_side = "home"
                entry_state = classify_entry_state(meta, backed_side)
                blocked_upper = {s.upper() for s in self.cfg.blocked_entry_states}
                if entry_state.state in blocked_upper:
                    self.entries_blocked_entry_state += 1
                    return False
            except Exception:
                # Defensive: if classifier fails for any reason, don't crash the trade
                # — just skip the filter check.
                pass

        # --- Hybrid-mode dominance: momentum signal must be on target player ---
        if self.cfg.dominance_filter_mode == "required":
            meta = self.feed.get_match_meta(sig.match_id) if hasattr(self.feed, "get_match_meta") else {}
            if not is_set3_or_later(meta):
                self.entries_blocked_dom_mismatch += 1
                return False
            pat = classify_dominance(meta)
            if pat is None:
                self.entries_blocked_dom_mismatch += 1
                return False
            # Pattern filter
            if self.cfg.dominance_patterns != "both" and pat.pattern_type != self.cfg.dominance_patterns:
                self.entries_blocked_dom_mismatch += 1
                return False
            # Check momentum signal is on the target player side
            sig_on_home = sig.swing_type in (SwingType.BACK_HOME, SwingType.LAY_HOME)
            target_is_home = pat.target_side == "home"
            sig_is_back = sig.swing_type in (SwingType.BACK_HOME, SwingType.BACK_AWAY)
            # Require: BACK signal AND signal side == target side
            if not sig_is_back or sig_on_home != target_is_home:
                self.entries_blocked_dom_mismatch += 1
                return False
            # Odds threshold check
            if not (self.cfg.dominance_min_odds <= sig.current_odds <= self.cfg.dominance_max_odds):
                self.entries_blocked_dom_mismatch += 1
                return False

        # Exclude doubles matches (players separated by '/'). Default behavior
        # per Apr 18 retro — doubles contributed to tail-end drawdowns. Skip only
        # if cfg.block_doubles is True (default).
        if self.cfg.block_doubles and (
                "/" in (sig.player or "") or "/" in (sig.match_label or "")):
            self.entries_blocked_doubles = getattr(self, "entries_blocked_doubles", 0) + 1
            return False
        # Directional filter: reject LAY signals when configured.
        if self.cfg.skip_lay_signals and sig.swing_type in (
                SwingType.LAY_HOME, SwingType.LAY_AWAY):
            self.entries_blocked_lay = getattr(self, "entries_blocked_lay", 0) + 1
            return False
        # Odds-band filter: skip entries where entry_odds falls in any configured band.
        # Configured via skip_odds_bands=((lo,hi), ...) on TennisConfig.
        if self.cfg.skip_odds_bands:
            odds = sig.current_odds
            for lo, hi in self.cfg.skip_odds_bands:
                if lo <= odds < hi:
                    self.entries_blocked_odds_band = getattr(self, "entries_blocked_odds_band", 0) + 1
                    return False
        # Confidence-band filter: skip signals in the "mid" confidence region where
        # data showed poor results. Keep both low-conf and high-conf signals.
        if (self.cfg.conf_skip_mid_lo is not None and
                self.cfg.conf_skip_mid_hi is not None):
            if self.cfg.conf_skip_mid_lo <= sig.confidence < self.cfg.conf_skip_mid_hi:
                self.entries_blocked_conf_band = getattr(self, "entries_blocked_conf_band", 0) + 1
                return False
        # Inverse conf-band filter: REQUIRE conf within a range.
        if (self.cfg.conf_require_lo is not None and
                self.cfg.conf_require_hi is not None):
            if not (self.cfg.conf_require_lo <= sig.confidence < self.cfg.conf_require_hi):
                self.entries_blocked_conf_req = getattr(self, "entries_blocked_conf_req", 0) + 1
                return False
        open_bets = [b for b in self._bets.values() if not b.closed]
        if len(open_bets) >= self.cfg.max_open_bets:
            return False
        # Don't double up on same match (any direction)
        for b in open_bets:
            if b.match_id == sig.match_id:
                return False
        # Re-entry protection: if we recently stopped out on the
        # same (match, direction), block until cooldown expires.
        direction = _SWING_DIRECTION[sig.swing_type]
        key = (sig.match_id, direction)
        sl_time = self._recent_sl.get(key, 0.0)
        if sl_time and (time.time() - sl_time) < self.cfg.relose_cooldown_sec:
            self.entries_blocked_relose += 1
            logger.info(
                f"BLOCK re-entry {direction} on {sig.match_label}: "
                f"recent SL {time.time() - sl_time:.0f}s ago "
                f"(cooldown {self.cfg.relose_cooldown_sec:.0f}s)")
            return False
        # Match-level cooldown: if we've taken too many trades on this match
        # in the configured window, skip. Prune old entries as we check.
        if self.cfg.max_trades_per_match_window is not None:
            now = time.time()
            window_sec = self.cfg.trades_per_match_window_sec
            dq = self._match_trade_times.get(sig.match_id)
            if dq is not None:
                while dq and (now - dq[0]) > window_sec:
                    dq.popleft()
                if len(dq) >= self.cfg.max_trades_per_match_window:
                    self.entries_blocked_match_cooldown += 1
                    logger.info(
                        f"BLOCK match-cooldown {sig.match_label} @ {sig.current_odds:.2f}: "
                        f"{len(dq)} trades in last {window_sec}s "
                        f"(max={self.cfg.max_trades_per_match_window})")
                    return False
        # Smart filter E: consecutive-loss cooldown (match frozen while losing streak active)
        if self.cfg.max_consecutive_losses_per_match is not None:
            cd_until = self._match_cooldown_until.get(sig.match_id, 0.0)
            if time.time() < cd_until:
                self.entries_blocked_loss_streak += 1
                logger.info(
                    f"BLOCK loss-streak {sig.match_label} @ {sig.current_odds:.2f}: "
                    f"streak={self._match_loss_streaks.get(sig.match_id, 0)} "
                    f"cooldown_remaining={cd_until - time.time():.0f}s")
                return False
        # Smart filter F: rolling match-WR cooldown
        if self.cfg.min_match_wr_over_n is not None:
            cd_until = self._match_wr_cooldown_until.get(sig.match_id, 0.0)
            if time.time() < cd_until:
                self.entries_blocked_match_wr += 1
                logger.info(
                    f"BLOCK match-WR {sig.match_label} @ {sig.current_odds:.2f}: "
                    f"cooldown_remaining={cd_until - time.time():.0f}s")
                return False
        # Smart filter G: adaptive odds-band
        if self.cfg.adaptive_odds_wr_window is not None:
            bucket = self._odds_bucket_for(sig.current_odds)
            if bucket is not None:
                dq = self._odds_bucket_outcomes.get(bucket)
                if dq is not None and len(dq) >= self.cfg.adaptive_odds_wr_window:
                    wr = sum(1 for w in dq if w) / len(dq)
                    if wr < self.cfg.adaptive_odds_min_wr:
                        self.entries_blocked_adaptive_odds += 1
                        logger.info(
                            f"BLOCK adaptive-odds {bucket} @ {sig.current_odds:.2f}: "
                            f"rolling WR={wr*100:.0f}% < {self.cfg.adaptive_odds_min_wr*100:.0f}%")
                        return False
        # Rank/ELO filter (new in Phase 4): apply config-driven checks.
        if not self._check_rank_elo(sig):
            return False
        # Prior-based filter: reject when fundamentals contradict the fade thesis.
        if not self._check_prior(sig):
            return False
        return True

    def _enrich_signal(self, sig: TennisSignal) -> Optional[dict]:
        """Look up rank + ELO for both players in this signal's match.

        Returns a dict with fader_* and opp_* keys when lookups succeed, or
        None when we lack the data to apply any rank/ELO filter.

        Identification:
            - sig.player is the player our signal side is ON
            - sig.swing_type tells us home vs away (BACK_HOME/LAY_HOME -> fader=home)
            - feed.get_match_meta(match_id) gives {p1_key, p2_key, event_type}
        """
        if self._rank_fetcher is None or self._name_matcher is None:
            return None
        meta = self.feed.get_match_meta(sig.match_id) if hasattr(self.feed, "get_match_meta") else {}
        p1k = meta.get("p1_key") or ""
        p2k = meta.get("p2_key") or ""
        event_type = meta.get("event_type", "") or "Atp Singles"
        if not p1k or not p2k:
            return None
        # Home = first_player_key; determine fader/opponent from swing type
        if sig.swing_type in (SwingType.BACK_HOME, SwingType.LAY_HOME):
            fader_key, opp_key = p1k, p2k
        else:
            fader_key, opp_key = p2k, p1k
        try:
            fader_info = self._rank_fetcher.get_player(int(fader_key), event_type)
            opp_info = self._rank_fetcher.get_player(int(opp_key), event_type)
        except Exception as e:
            logger.debug(f"rank lookup failed for {sig.match_id}: {e}")
            return None
        out = {
            "fader_key": fader_key,
            "opp_key": opp_key,
            "fader_name": fader_info.name if fader_info else "",
            "opp_name": opp_info.name if opp_info else "",
            "fader_rank": fader_info.rank if fader_info else None,
            "opp_rank": opp_info.rank if opp_info else None,
            "fader_elo": None,
            "opp_elo": None,
            "fader_sackmann_id": None,
            "opp_sackmann_id": None,
            "event_type": event_type,
        }
        # Name-match to Sackmann, then ELO lookup
        if fader_info and fader_info.name:
            fid = self._name_matcher.match(fader_info.name)
            if fid:
                out["fader_sackmann_id"] = fid
                rec = self._elo_snapshot.get(fid)
                if rec:
                    out["fader_elo"] = rec.get("overall_elo")
        if opp_info and opp_info.name:
            oid = self._name_matcher.match(opp_info.name)
            if oid:
                out["opp_sackmann_id"] = oid
                rec = self._elo_snapshot.get(oid)
                if rec:
                    out["opp_elo"] = rec.get("overall_elo")
        return out

    def _check_rank_elo(self, sig: TennisSignal) -> bool:
        """Apply rank/ELO-based entry filters from config.
        Returns True to allow entry, False to reject.

        Behavior:
            - If no rank/ELO config fields are set, returns True (fast path)
            - If enrichment fails AND require_*=True, returns False (strict mode)
            - If enrichment succeeds, applies each configured filter
        """
        cfg = self.cfg
        # Fast path: if no rank/ELO config is set, nothing to check
        any_rank = (cfg.min_fader_rank is not None or cfg.max_fader_rank is not None
                    or cfg.min_rank_gap is not None or cfg.max_rank_gap is not None
                    or cfg.require_rank_data)
        any_elo = (cfg.min_fader_elo is not None or cfg.max_fader_elo is not None
                   or cfg.min_elo_gap is not None or cfg.max_elo_gap is not None
                   or cfg.require_elo_data)
        if not any_rank and not any_elo:
            return True
        enriched = self._enrich_signal(sig)
        if enriched is None:
            # Enrichment unavailable (module missing, or no match meta)
            if cfg.require_rank_data or cfg.require_elo_data:
                self.entries_missing_rank += 1
                return False
            return True
        # ----- Rank filters -----
        if any_rank:
            fr = enriched.get("fader_rank")
            orank = enriched.get("opp_rank")
            if fr is None or orank is None:
                if cfg.require_rank_data:
                    self.entries_missing_rank += 1
                    logger.info(
                        f"BLOCK rank-missing {sig.player} @ {sig.current_odds:.2f}: "
                        f"fader_rank={fr} opp_rank={orank}")
                    return False
                # else: pass-through on missing rank
            else:
                if cfg.min_fader_rank is not None and fr < cfg.min_fader_rank:
                    self.entries_blocked_rank += 1
                    return False
                if cfg.max_fader_rank is not None and fr > cfg.max_fader_rank:
                    self.entries_blocked_rank += 1
                    logger.info(
                        f"BLOCK rank {sig.player} @ {sig.current_odds:.2f}: "
                        f"fader_rank={fr} > max={cfg.max_fader_rank}")
                    return False
                gap = abs(fr - orank)
                if cfg.min_rank_gap is not None and gap < cfg.min_rank_gap:
                    self.entries_blocked_rank += 1
                    return False
                if cfg.max_rank_gap is not None and gap > cfg.max_rank_gap:
                    self.entries_blocked_rank += 1
                    return False
        # ----- ELO filters -----
        if any_elo:
            fe = enriched.get("fader_elo")
            oe = enriched.get("opp_elo")
            if fe is None or oe is None:
                if cfg.require_elo_data:
                    self.entries_missing_elo += 1
                    logger.info(
                        f"BLOCK elo-missing {sig.player} @ {sig.current_odds:.2f}: "
                        f"fader_elo={fe} opp_elo={oe} (sack ids "
                        f"{enriched.get('fader_sackmann_id')}/{enriched.get('opp_sackmann_id')})")
                    return False
                # else: pass-through on missing ELO
            else:
                if cfg.min_fader_elo is not None and fe < cfg.min_fader_elo:
                    self.entries_blocked_elo += 1
                    return False
                if cfg.max_fader_elo is not None and fe > cfg.max_fader_elo:
                    self.entries_blocked_elo += 1
                    return False
                elo_gap = fe - oe
                if cfg.min_elo_gap is not None and elo_gap < cfg.min_elo_gap:
                    self.entries_blocked_elo += 1
                    logger.info(
                        f"BLOCK elo-gap {sig.player} @ {sig.current_odds:.2f}: "
                        f"elo_gap={elo_gap:+.0f} < min={cfg.min_elo_gap}")
                    return False
                if cfg.max_elo_gap is not None and elo_gap > cfg.max_elo_gap:
                    self.entries_blocked_elo += 1
                    return False
        self.entries_passed_rank_elo += 1
        return True

    def _check_prior(self, sig: TennisSignal) -> bool:
        """Apply the pre-match prior as an entry filter.
        Returns False to veto the entry.
        If Redis is unavailable or the prior is missing / low-confidence,
        we pass through (returns True). Only reject on a confident disagreement.
        """
        if self._redis is None:
            return True  # filter disabled
        try:
            raw = self._redis.get(f"tennis:prior:{sig.match_id}")
        except Exception as e:
            logger.debug(f"Redis GET failed for {sig.match_id}: {e}")
            self.entries_no_prior += 1
            return True
        if raw is None:
            # No prior computed yet for this match - fall through
            self.entries_no_prior += 1
            return True
        try:
            prior = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            self.entries_no_prior += 1
            return True
        if prior.get("confidence", 0) < self.prior_min_confidence:
            # Low-confidence prior, treat as missing
            self.entries_no_prior += 1
            return True
        p_prior_home = float(prior.get("p_home", 0.5))
        # Market-implied probability from current odds.
        # BACK odds imply P(this player wins) = 1/odds.
        # The signal refers to ONE player (sig.player) and we know its side.
        p_market_this = 1.0 / max(sig.current_odds, 1.01)
        # Which player is "this"?
        if sig.swing_type in (SwingType.BACK_HOME, SwingType.LAY_HOME):
            p_prior_this = p_prior_home
        else:
            p_prior_this = 1.0 - p_prior_home
        divergence = p_prior_this - p_market_this
        # BACK thesis: prior > market (player stronger than market prices)
        # LAY thesis:  prior < market (player weaker than market prices)
        if sig.swing_type in (SwingType.BACK_HOME, SwingType.BACK_AWAY):
            # Want divergence >= +min_divergence
            if divergence < self.prior_min_divergence:
                self.entries_blocked_prior_disagree += 1
                logger.info(
                    f"BLOCK prior (BACK) {sig.player} "
                    f"@ {sig.current_odds:.2f}: "
                    f"prior={p_prior_this:.2f} vs market={p_market_this:.2f} "
                    f"(div {divergence:+.2f}, need>={self.prior_min_divergence})")
                return False
        else:  # LAY_HOME or LAY_AWAY
            # Want divergence <= -min_divergence
            if divergence > -self.prior_min_divergence:
                self.entries_blocked_prior_disagree += 1
                logger.info(
                    f"BLOCK prior (LAY) {sig.player} "
                    f"@ {sig.current_odds:.2f}: "
                    f"prior={p_prior_this:.2f} vs market={p_market_this:.2f} "
                    f"(div {divergence:+.2f}, need<=-{self.prior_min_divergence})")
                return False
        self.entries_passed_prior += 1
        return True


    def _enter(self, sig: TennisSignal):
        self._bet_counter += 1
        bid = f"TB-{self._bet_counter:04d}"

        # Determine bet type from swing type
        if sig.swing_type in (SwingType.BACK_HOME, SwingType.BACK_AWAY):
            bet_type = "back"
        else:
            bet_type = "lay"

        stake = self._compute_stake(sig)
        bet = TennisBet(
            bet_id=bid,
            match_id=sig.match_id,
            match_label=sig.match_label,
            player=sig.player,
            bet_type=bet_type,
            swing_type=sig.swing_type,
            odds=sig.current_odds,
            stake=stake,
        )
        self._bets[bid] = bet
        logger.info(f"ENTER {bid} {bet_type.upper()} {sig.player} "
                    f"@ {sig.current_odds:.2f} | ${bet.stake:.2f} (conf={sig.confidence:.2f}) | "
                    f"{sig.match_label} | move={sig.odds_move_pct:+.1f}%")
        # Record entry time for match cooldown tracking
        if self.cfg.max_trades_per_match_window is not None:
            dq = self._match_trade_times.get(sig.match_id)
            if dq is None:
                dq = deque()
                self._match_trade_times[sig.match_id] = dq
            dq.append(time.time())

    def _odds_bucket_for(self, odds: float):
        """Return (lo, hi) tuple for the bucket containing odds, or None."""
        for lo, hi in self.cfg.adaptive_odds_buckets:
            if lo <= odds < hi:
                return (lo, hi)
        return None

    def _compute_stake(self, sig) -> float:
        """Compute stake for this signal based on cfg.stake_scheme.

        'flat'       -> cfg.stake_amount (unchanged)
        'conviction' -> scales with signal confidence, clipped.
        """
        scheme = getattr(self.cfg, "stake_scheme", "flat")
        base = self.cfg.stake_amount
        if scheme == "conviction":
            conf = sig.confidence
            baseline = getattr(self.cfg, "stake_conf_baseline", 0.70)
            smin = getattr(self.cfg, "stake_min", 5.0)
            smax = getattr(self.cfg, "stake_max", 20.0)
            scaled = base * (conf / baseline) if baseline > 0 else base
            return max(smin, min(smax, scaled))
        # default: flat
        return base

    def _record_outcome_for_smart_filters(self, bet, reason: str):
        """Update smart-filter state after a bet closes.

        reason: 'take_profit' | 'stop_loss' | 'hard_cap' | 'max_hold'
        Treat take_profit as win, stop_loss+hard_cap as loss, max_hold as neutral.
        """
        is_win = (reason == "take_profit")
        is_loss = (reason in ("stop_loss", "hard_cap"))
        is_neutral = (reason == "max_hold")
        now = time.time()
        mid = bet.match_id

        # ---- E: consecutive-loss streak ----
        if self.cfg.max_consecutive_losses_per_match is not None:
            if is_win:
                self._match_loss_streaks[mid] = 0
            elif is_loss:
                s = self._match_loss_streaks.get(mid, 0) + 1
                self._match_loss_streaks[mid] = s
                if s >= self.cfg.max_consecutive_losses_per_match:
                    self._match_cooldown_until[mid] = now + self.cfg.consecutive_loss_cooldown_sec
                    logger.info(
                        f"MATCH COOLDOWN (loss-streak) {bet.match_label}: "
                        f"{s} consecutive losses -> freeze {self.cfg.consecutive_loss_cooldown_sec}s")
                    # Reset streak so we don't re-fire on next loss
                    self._match_loss_streaks[mid] = 0

        # ---- F: rolling match WR ----
        if self.cfg.min_match_wr_over_n is not None and not is_neutral:
            dq = self._match_outcomes.get(mid)
            if dq is None:
                dq = deque()
                self._match_outcomes[mid] = dq
            dq.append(is_win)
            if len(dq) >= self.cfg.min_match_wr_over_n:
                wr = sum(1 for w in dq if w) / len(dq)
                if wr < self.cfg.min_match_wr:
                    self._match_wr_cooldown_until[mid] = now + self.cfg.match_wr_cooldown_sec
                    logger.info(
                        f"MATCH COOLDOWN (low-WR) {bet.match_label}: "
                        f"WR={wr*100:.0f}% over {len(dq)} trades -> freeze {self.cfg.match_wr_cooldown_sec}s")
                    # Clear outcomes so we start fresh after cooldown
                    dq.clear()

        # ---- G: adaptive odds-band ----
        if self.cfg.adaptive_odds_wr_window is not None and not is_neutral:
            bucket = self._odds_bucket_for(bet.odds)
            if bucket is not None:
                dq = self._odds_bucket_outcomes.get(bucket)
                if dq is None:
                    dq = deque(maxlen=self.cfg.adaptive_odds_wr_window)
                    self._odds_bucket_outcomes[bucket] = dq
                dq.append(is_win)

    def _check_dominance_entries(self):
        """For mode='only': scan all live matches, fire BACK entries on dominance patterns.
        Each match fires at most ONCE per strategy session.
        """
        if self.cfg.dominance_filter_mode != "only":
            return
        live = self.feed.get_live_matches() if hasattr(self.feed, "get_live_matches") else []
        # Enforce max_open_bets guard
        open_bets = [b for b in self._bets.values() if not b.closed]
        if len(open_bets) >= self.cfg.max_open_bets:
            return
        # Build set of match_ids we already have open bets on (avoid doubling up)
        open_match_ids = {b.match_id for b in open_bets}
        for match in live:
            mid = match.match_id
            if mid in self._dom_entered_matches:
                self.dom_blocked_already_entered += 1
                continue
            if mid in open_match_ids:
                continue
            meta = self.feed.get_match_meta(mid) if hasattr(self.feed, "get_match_meta") else {}
            # Tier filter applies here too
            if self.cfg.allowed_event_types is not None:
                event_type = (meta.get("event_type") or "").lower()
                allowed = {s.lower() for s in self.cfg.allowed_event_types}
                if not any(a in event_type for a in allowed):
                    continue
            # Tier BLOCKLIST applies too
            if self.cfg.blocked_event_types is not None:
                event_type = (meta.get("event_type") or "").lower()
                blocked = {s.lower() for s in self.cfg.blocked_event_types}
                if any(b in event_type for b in blocked):
                    continue
            if not is_set3_or_later(meta):
                self.dom_blocked_not_set3 += 1
                continue
            pat = classify_dominance(meta)
            if pat is None:
                self.dom_blocked_no_pattern += 1
                continue
            if self.cfg.dominance_patterns != "both" and pat.pattern_type != self.cfg.dominance_patterns:
                continue
            # Doubles check (respects cfg.block_doubles)
            if self.cfg.block_doubles and (
                    "/" in (match.home_player or "") or "/" in (match.away_player or "")):
                continue
            # Target side -> current odds
            if pat.target_side == "home":
                target_player = match.home_player
                target_odds = match.home_best_back
                swing_type = SwingType.BACK_HOME
            else:
                target_player = match.away_player
                target_odds = match.away_best_back
                swing_type = SwingType.BACK_AWAY
            if target_odds <= 0:
                continue
            if not (self.cfg.dominance_min_odds <= target_odds <= self.cfg.dominance_max_odds):
                self.dom_blocked_odds += 1
                continue
            # Fire. Synthesize a TennisSignal so _enter/_check_prior/etc work.
            synth_sig = TennisSignal(
                match_id=mid,
                match_label=f"{match.home_player} vs {match.away_player}",
                swing_type=swing_type,
                player=target_player,
                current_odds=target_odds,
                odds_move_pct=0.0,
                window_sec=0.0,
                confidence=0.75,  # synthetic — used by conviction staking if enabled
            )
            # Bypass _can_enter's momentum-specific checks: we've done our own.
            # But still honor max_open_bets, relose cooldown, doubles, match cooldown.
            # The simplest/safest: go straight to _enter, mark match as entered.
            self._dom_entered_matches.add(mid)
            self.dom_entries += 1
            logger.info(
                f"DOMINANCE ENTRY {pat.pattern_type} {target_player} @ {target_odds:.2f} | "
                f"sets: {pat.set1_score} / {pat.set2_score} | "
                f"{match.home_player} vs {match.away_player}")
            self._enter(synth_sig)

    def _manage_bets(self):
        """Check open bets for exit conditions."""
        for bet in list(self._bets.values()):
            if bet.closed:
                continue

            match = self.feed.get_match(bet.match_id)
            if not match:
                continue

            # Get current odds for the player we bet on
            if bet.player == match.home_player:
                current_odds = match.home_best_back if bet.bet_type == "lay" \
                    else match.home_best_lay
            else:
                current_odds = match.away_best_back if bet.bet_type == "lay" \
                    else match.away_best_lay

            if current_odds <= 0:
                continue

            # Calculate unrealised P&L (% odds move in our direction)
            if bet.bet_type == "back":
                # Backed at high odds, profit if odds shortened
                odds_move = ((bet.odds - current_odds) / bet.odds) * 100
            else:
                # Laid at low odds, profit if odds drifted
                odds_move = ((current_odds - bet.odds) / bet.odds) * 100

            # Hard dollar cap (runs first so it catches gap losses before odds-move SL)
            if self.cfg.hard_cap_dollars is not None:
                unrealized = self._unrealized_pnl(bet, current_odds)
                if unrealized <= -self.cfg.hard_cap_dollars:
                    self._close_bet(bet, current_odds, "hard_cap")
                    continue
            # Take profit
            if odds_move >= self.cfg.target_odds_move_pct:
                self._close_bet(bet, current_odds, "take_profit")
            # Stop loss
            elif odds_move <= -self.cfg.stop_odds_move_pct:
                self._close_bet(bet, current_odds, "stop_loss")
            # Max hold
            elif bet.age >= self.cfg.max_hold_sec:
                self._close_bet(bet, current_odds, "max_hold")


    def _unrealized_pnl(self, bet, current_odds):
        """Compute pre-commission PnL if bet closed NOW at current_odds."""
        if current_odds <= 0:
            return 0.0
        if bet.bet_type == "back":
            back_return = bet.stake * bet.odds
            lay_stake = back_return / current_odds
            return lay_stake - bet.stake
        else:
            back_stake = bet.stake * bet.odds / current_odds
            return bet.stake - back_stake

    def _close_bet(self, bet: TennisBet, exit_odds: float, reason: str):
        """Close a bet and calculate P&L."""
        bet.exit_odds = exit_odds
        bet.exit_time = time.time()
        bet.exit_reason = reason
        bet.closed = True

        # P&L calculation
        if bet.bet_type == "back":
            # Backed at bet.odds, closing by laying at exit_odds
            if exit_odds > 0:
                back_return = bet.stake * bet.odds
                lay_stake = back_return / exit_odds
                pnl = lay_stake - bet.stake  # guaranteed profit/loss
            else:
                pnl = -bet.stake
        else:
            # Laid at bet.odds, closing by backing at exit_odds
            if exit_odds > 0:
                lay_liability = bet.stake * (bet.odds - 1)
                back_stake = bet.stake * bet.odds / exit_odds
                pnl = bet.stake - back_stake
            else:
                pnl = -bet.liability

        # Commission on net winnings
        commission = 0.0
        if pnl > 0:
            commission = pnl * COMMISSION_RATE
            pnl -= commission

        bet.pnl = round(pnl, 4)
        self.total_pnl += bet.pnl
        self.total_trades += 1
        self.total_commission += commission
        if bet.pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1

        # Re-entry protection: remember stop-loss exits by (match, direction)
        if reason == "stop_loss":
            direction = _SWING_DIRECTION[bet.swing_type]
            self._recent_sl[(bet.match_id, direction)] = bet.exit_time

        # Smart filter state update (fork filters E/F/G)
        self._record_outcome_for_smart_filters(bet, reason)

        logger.info(
            f"EXIT {bet.bet_id} {bet.bet_type.upper()} {bet.player} "
            f"@ {exit_odds:.2f} (entry {bet.odds:.2f}) | "
            f"${bet.pnl:+.4f} | reason={reason} | "
            f"held={bet.age:.0f}s | comm=${commission:.4f}")

    def get_stats(self) -> dict:
        open_bets = [b for b in self._bets.values() if not b.closed]
        det_stats = self.detector.get_stats()
        return {
            "total_pnl": round(self.total_pnl, 4),
            "total_trades": self.total_trades,
            "winning": self.winning_trades,
            "losing": self.losing_trades,
            "win_rate": self.winning_trades / max(self.total_trades, 1),
            "total_commission": round(self.total_commission, 4),
            "open_bets": len(open_bets),
            "signals_detected": det_stats["signals_detected"],
            "signals_rejected_cooldown": det_stats.get("signals_rejected_cooldown", 0),
            "signals_rejected_extreme": det_stats.get("signals_rejected_extreme", 0),
            "entries_blocked_relose": self.entries_blocked_relose,
            "entries_blocked_doubles": self.entries_blocked_doubles,
            "entries_blocked_lay": self.entries_blocked_lay,
            "entries_blocked_match_cooldown": self.entries_blocked_match_cooldown,
            "entries_blocked_loss_streak": self.entries_blocked_loss_streak,
            "entries_blocked_match_wr": self.entries_blocked_match_wr,
            "entries_blocked_adaptive_odds": self.entries_blocked_adaptive_odds,
            "entries_blocked_odds_band": getattr(self, "entries_blocked_odds_band", 0),
            "entries_blocked_conf_band": getattr(self, "entries_blocked_conf_band", 0),
            "entries_blocked_conf_req": getattr(self, "entries_blocked_conf_req", 0),
            "entries_blocked_prior": self.entries_blocked_prior_disagree,
            "entries_passed_prior": self.entries_passed_prior,
            "entries_no_prior": self.entries_no_prior,
            "prior_filter_enabled": self._redis is not None,
            "entries_blocked_rank": self.entries_blocked_rank,
            "entries_blocked_elo": self.entries_blocked_elo,
            "entries_missing_rank": self.entries_missing_rank,
            "entries_missing_elo": self.entries_missing_elo,
            "entries_passed_rank_elo": self.entries_passed_rank_elo,
            "rank_elo_enabled": self._rank_fetcher is not None,
            "entries_blocked_tier": self.entries_blocked_tier,
            "entries_blocked_entry_state": self.entries_blocked_entry_state,
            "entries_blocked_dom_mismatch": self.entries_blocked_dom_mismatch,
            "dom_entries": self.dom_entries,
            "dom_blocked_no_pattern": self.dom_blocked_no_pattern,
            "dom_blocked_odds": self.dom_blocked_odds,
            "dom_blocked_not_set3": self.dom_blocked_not_set3,
            "dom_blocked_already_entered": self.dom_blocked_already_entered,
            "dominance_mode": self.cfg.dominance_filter_mode,
        }

    def get_bets_list(self) -> List[dict]:
        """Return all bets as dicts for dashboard."""
        bets = []
        for b in sorted(self._bets.values(),
                        key=lambda x: x.entry_time, reverse=True):
            bets.append({
                "id": b.bet_id, "match": b.match_label,
                "player": b.player, "type": b.bet_type,
                "odds": b.odds, "stake": b.stake,
                "exit_odds": b.exit_odds if b.closed else None,
                "pnl": b.pnl if b.closed else None,
                "reason": b.exit_reason if b.closed else "open",
                "held": round(b.age, 0), "closed": b.closed,
            })
        return bets[:50]
