"""Entry-state classifier for live tennis matches.

Given match metadata (scores list) and which side we're about to BACK,
classify the CURRENT state into one of the labels we identified as
profitable/unprofitable in backtesting.

This is pure functions, no I/O. Used by TennisStrategy._can_enter() as a
filter: if the state is in cfg.blocked_entry_states, block the trade.

STATE LABELS (must match the simulation script exactly):
  PRE_MATCH                      — no sets started yet
  EVEN_SET1                      — set 1 in progress, backed within 1 game of opp
  AHEAD_SET1_LIGHT               — set 1 ongoing, ahead by 2-3 games
  AHEAD_SET1_HEAVY               — set 1 ongoing, ahead by 4+ games
  BEHIND_SET1_LIGHT              — set 1 ongoing, behind by 2-3 games
  BEHIND_SET1_HEAVY              — set 1 ongoing, behind by 4+ games
  AHEAD_WON_SET1                 — past set 1, won normally, set 2 roughly even
  AHEAD_WON_SET1_LEADING         — past set 1 normally, ahead in set 2
  AHEAD_WON_SET1_FADING          — past set 1 normally, now trailing in set 2
  DOMINATING_WON_SET1_BAGEL      — bageled set 1 (6-0/6-1), winning or even set 2
  FADING_WON_SET1_BAGEL          — bageled set 1 but trailing in set 2
  BEHIND_LOST_SET1               — lost set 1 normally, behind in set 2
  EVEN_LOST_SET1                 — lost set 1 normally, set 2 tied
  RECOVERING_LOST_SET1           — lost set 1 normally, ahead in set 2
  CRUSHED_LOST_SET1_BAGEL        — got bageled set 1, losing set 2
  BEHIND_LOST_SET1_BAGEL         — got bageled set 1, tied in set 2
  REVIVING_LOST_SET1_BAGEL       — got bageled set 1, ahead in set 2
  FINAL_SET                      — in the deciding set (3rd set of best-of-3)

NEGATIVE-edge states (recommended block candidates, based on 1169-trade
backtest edge/trade):
  BEHIND_SET1_HEAVY        (-$2.29/trade, small n)
  BEHIND_LOST_SET1_BAGEL   (-$0.40/trade)
  AHEAD_SET1_HEAVY         (-$0.33/trade — counterintuitive, but backtest shows drag)
  AHEAD_WON_SET1_FADING    (-$0.19/trade)
  EVEN_LOST_SET1           (-$0.13/trade, biggest dollar leak)
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

# Import the shared helpers from tennis_dominance so we don't duplicate logic
from tennis_dominance import _parse_set_score, _is_lopsided, _completed_set


@dataclass
class EntryState:
    """Classified state of a live match from the BACKED player's perspective."""
    state: str                   # label from the catalog above
    set_idx: int                 # 0 = set 1, 1 = set 2, 2 = set 3, -1 = pre-match
    backed_games_current_set: int
    opp_games_current_set: int
    sets_won_by_backed: int
    sets_won_by_opp: int
    set1_bageled_against_backed: bool
    set1_bageled_by_backed: bool


def _determine_current_set(scores: List[Dict[str, Any]]) -> int:
    """Return which set is currently being played (0-indexed), or -1 if pre-match.

    A set is "current" if it exists in scores but is NOT completed.
    If every set in scores is complete, the current set is the NEXT one.
    If no scores at all, pre-match.
    """
    if not scores:
        return -1
    # Iterate scores, find first incomplete set
    for i, s in enumerate(scores):
        if not isinstance(s, dict):
            continue
        parsed = _parse_set_score(s)
        if parsed is None:
            continue
        h, a = parsed
        if not _completed_set(h, a):
            return i
    # All sets present are complete — current is the next one (rare in live)
    return len(scores)


def classify_entry_state(match_meta: Dict[str, Any], backed_side: str) -> EntryState:
    """Classify the live match state from the BACKED player's perspective.

    Args:
      match_meta: dict returned by TennisFeed.get_match_meta(), with at minimum
                  a 'scores' list of {score_first, score_second, score_set}.
      backed_side: 'home' or 'away' — which side we are about to BACK.

    Returns EntryState. Always succeeds (returns PRE_MATCH or a specific state).
    """
    if backed_side not in ("home", "away"):
        raise ValueError(f"backed_side must be 'home' or 'away', got {backed_side!r}")

    scores = match_meta.get("scores") or []
    if not isinstance(scores, list):
        scores = []

    # If status says "not started" or event_status is blank, pre-match
    status = str(match_meta.get("event_status", "")).strip().lower()
    if status in ("", "not started", "scheduled", "postponed"):
        # If there are no scores, pre-match
        if not scores or all(
            not isinstance(s, dict) or _parse_set_score(s) in (None, (0, 0))
            for s in scores
        ):
            return EntryState(
                state="PRE_MATCH", set_idx=-1,
                backed_games_current_set=0, opp_games_current_set=0,
                sets_won_by_backed=0, sets_won_by_opp=0,
                set1_bageled_against_backed=False, set1_bageled_by_backed=False,
            )

    current_set = _determine_current_set(scores)
    if current_set == -1:
        return EntryState(
            state="PRE_MATCH", set_idx=-1,
            backed_games_current_set=0, opp_games_current_set=0,
            sets_won_by_backed=0, sets_won_by_opp=0,
            set1_bageled_against_backed=False, set1_bageled_by_backed=False,
        )

    # Walk completed sets to count set wins and detect lopsided set 1
    sets_won_backed = 0
    sets_won_opp = 0
    set1_bageled_against = False
    set1_bageled_by = False

    for i, s in enumerate(scores[:current_set]):
        parsed = _parse_set_score(s)
        if parsed is None:
            continue
        h, a = parsed
        if backed_side == "home":
            bg, og = h, a
        else:
            bg, og = a, h
        if bg > og:
            sets_won_backed += 1
        else:
            sets_won_opp += 1
        if i == 0:
            # Check lopsided set 1
            if og == 6 and bg <= 1:
                set1_bageled_against = True
            if bg == 6 and og <= 1:
                set1_bageled_by = True

    # Current-set games so far
    bg_cur = og_cur = 0
    if current_set < len(scores):
        parsed = _parse_set_score(scores[current_set])
        if parsed is not None:
            h, a = parsed
            if backed_side == "home":
                bg_cur, og_cur = h, a
            else:
                bg_cur, og_cur = a, h

    # Now classify
    state = _label_for(
        set_idx=current_set,
        bg_cur=bg_cur, og_cur=og_cur,
        sets_won_backed=sets_won_backed, sets_won_opp=sets_won_opp,
        set1_bageled_against=set1_bageled_against,
        set1_bageled_by=set1_bageled_by,
    )

    return EntryState(
        state=state,
        set_idx=current_set,
        backed_games_current_set=bg_cur,
        opp_games_current_set=og_cur,
        sets_won_by_backed=sets_won_backed,
        sets_won_by_opp=sets_won_opp,
        set1_bageled_against_backed=set1_bageled_against,
        set1_bageled_by_backed=set1_bageled_by,
    )


def _label_for(set_idx, bg_cur, og_cur, sets_won_backed, sets_won_opp,
               set1_bageled_against, set1_bageled_by):
    """Return the state label given the numeric inputs."""
    lead = bg_cur - og_cur

    if set_idx == 0:
        # Set 1 in progress
        if abs(lead) <= 1:
            return "EVEN_SET1"
        if lead >= 4:
            return "AHEAD_SET1_HEAVY"
        if lead >= 2:
            return "AHEAD_SET1_LIGHT"
        if lead <= -4:
            return "BEHIND_SET1_HEAVY"
        return "BEHIND_SET1_LIGHT"

    if set_idx == 1:
        # Set 2 in progress, set 1 complete
        if sets_won_backed == 1:
            # Backed won set 1
            if set1_bageled_by:
                if lead <= -2:
                    return "FADING_WON_SET1_BAGEL"
                return "DOMINATING_WON_SET1_BAGEL"
            # Normal set 1 win
            if lead >= 2:
                return "AHEAD_WON_SET1_LEADING"
            if lead <= -2:
                return "AHEAD_WON_SET1_FADING"
            return "AHEAD_WON_SET1"
        # Backed lost set 1
        if set1_bageled_against:
            if lead <= -2:
                return "CRUSHED_LOST_SET1_BAGEL"
            if lead >= 2:
                return "REVIVING_LOST_SET1_BAGEL"
            return "BEHIND_LOST_SET1_BAGEL"
        # Normal set 1 loss
        if lead >= 2:
            return "RECOVERING_LOST_SET1"
        if lead <= -2:
            return "BEHIND_LOST_SET1"
        return "EVEN_LOST_SET1"

    # set_idx >= 2 — in the deciding set (or beyond, for 5-set tournaments)
    return "FINAL_SET"
