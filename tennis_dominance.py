"""Dominance pattern classification from live match scores.

Pure functions — no I/O, no state. Given a match metadata dict (as returned
by TennisFeed.get_match_meta), classify whether a 'dominance pattern' exists
and identify which player (home/away) the BACK signal is on.

The pattern fires at the start of set 3 when:
  - Sets 1 and 2 are complete
  - Sets split (one each)
  - At least ONE set was won 6-0 or 6-1
  - The OTHER set was NOT also 6-0 or 6-1 (i.e. opponent didn't also blow out)
  - Player who won the lopsided set = target player

Two sub-patterns:
  'comeback' = target player LOST set 1 (possibly narrowly), WON set 2 lopsided
               → back them for set 3
  'mirror'   = target player WON set 1 lopsided, LOST set 2 (possibly narrowly)
               → back them for set 3

If both sets 1 and 2 were lopsided in opposite directions, the signal cancels
(two bageler/breaders = coin flip at tier skill gap).

Returns None if pattern not present (or pre-set-3, or not 3-set match, etc.)
Returns DominancePattern otherwise.
"""
from dataclasses import dataclass
from typing import Optional, List, Dict, Any


@dataclass
class DominancePattern:
    """Classification of a live match's set-1+2 pattern."""
    target_side: str           # 'home' or 'away'  (which player to BACK)
    pattern_type: str          # 'comeback' or 'mirror'
    set1_score: str            # e.g. '6-4' (from target player's perspective — games they won - games opp won)
    set2_score: str            # e.g. '6-1'
    lopsided_set: int          # 1 or 2 — which set was lopsided
    home_score_1: int          # raw games — first_player in set 1
    away_score_1: int          # raw games — second_player in set 1
    home_score_2: int
    away_score_2: int


def _parse_set_score(score_dict: Dict[str, Any]) -> Optional[tuple]:
    """Extract (home_games, away_games) as integers from an api-tennis score dict.
    Returns None if unparseable.

    score_dict shape: {'score_first': '6', 'score_second': '2', 'score_set': '1'}
    Tiebreak sets come as '6.4' / '7.7' — we need int parts only for games-won.
    """
    sf = score_dict.get("score_first", "")
    ss = score_dict.get("score_second", "")
    try:
        # Tiebreak notation like "6.4" means "6 games, lost tiebreak 4-7" —
        # take just the integer (games won).
        hf = int(str(sf).split(".")[0])
        ha = int(str(ss).split(".")[0])
        return (hf, ha)
    except (ValueError, TypeError):
        return None


def _is_lopsided(home_g: int, away_g: int) -> bool:
    """Set is lopsided if score is 6-0 or 6-1 either direction."""
    hi, lo = max(home_g, away_g), min(home_g, away_g)
    return hi == 6 and lo in (0, 1)


def _completed_set(home_g: int, away_g: int) -> bool:
    """Set is complete if someone reached 6 (winning by 2) or 7 (won tiebreak / 7-5)."""
    # 6-0, 6-1, 6-2, 6-3, 6-4 — winner has 6, loser has <=4
    # 7-5, 7-6 — winner has 7, loser has 5 or 6
    hi, lo = max(home_g, away_g), min(home_g, away_g)
    if hi == 6 and lo <= 4:
        return True
    if hi == 7 and lo in (5, 6):
        return True
    return False


def classify_dominance(match_meta: Dict[str, Any]) -> Optional[DominancePattern]:
    """Classify a live match's set-1+2 dominance pattern.

    Returns None when:
      - We don't have 2+ completed sets yet
      - Sets didn't split (same player won both)
      - Neither set was lopsided (no signal)
      - BOTH sets were lopsided in opposite directions (signal cancels)

    Returns DominancePattern otherwise.
    """
    scores = match_meta.get("scores") or []
    if not isinstance(scores, list) or len(scores) < 2:
        return None

    # Look specifically for set 1 and set 2 entries. The api-tennis 'scores'
    # list typically has objects with score_set = "1", "2", "3". We pick the
    # first two by that field (or by position if score_set is missing).
    s1 = s2 = None
    for s in scores:
        if not isinstance(s, dict):
            continue
        ssnum = str(s.get("score_set", "")).strip()
        if ssnum == "1" and s1 is None:
            s1 = s
        elif ssnum == "2" and s2 is None:
            s2 = s
    # Fallback: if score_set field absent, take first two entries
    if s1 is None and len(scores) >= 1:
        s1 = scores[0] if isinstance(scores[0], dict) else None
    if s2 is None and len(scores) >= 2:
        s2 = scores[1] if isinstance(scores[1], dict) else None
    if s1 is None or s2 is None:
        return None

    p1 = _parse_set_score(s1)
    p2 = _parse_set_score(s2)
    if p1 is None or p2 is None:
        return None

    h1, a1 = p1
    h2, a2 = p2

    # Must be completed
    if not _completed_set(h1, a1) or not _completed_set(h2, a2):
        return None

    # Determine winners
    s1_home_won = h1 > a1
    s2_home_won = h2 > a2

    # Must split (one each)
    if s1_home_won == s2_home_won:
        return None

    lop1 = _is_lopsided(h1, a1)
    lop2 = _is_lopsided(h2, a2)

    # Both lopsided in opposite directions -> signal cancels (see Rule 3)
    if lop1 and lop2:
        return None

    # Neither lopsided -> no signal (two competitive sets, priced correctly)
    if not lop1 and not lop2:
        return None

    # Exactly one is lopsided. Target = winner of the lopsided set.
    if lop1:
        target_is_home = s1_home_won
        lopsided_set = 1
    else:
        target_is_home = s2_home_won
        lopsided_set = 2

    # Sub-pattern: comeback vs mirror
    # comeback: target lost set 1, won set 2 lopsided
    # mirror:   target won set 1 lopsided, lost set 2
    if lopsided_set == 2:
        pattern_type = "comeback"
    else:
        pattern_type = "mirror"

    # Labels from target perspective (their games first)
    if target_is_home:
        s1_label = f"{h1}-{a1}"
        s2_label = f"{h2}-{a2}"
    else:
        s1_label = f"{a1}-{h1}"
        s2_label = f"{a2}-{h2}"

    return DominancePattern(
        target_side="home" if target_is_home else "away",
        pattern_type=pattern_type,
        set1_score=s1_label,
        set2_score=s2_label,
        lopsided_set=lopsided_set,
        home_score_1=h1, away_score_1=a1,
        home_score_2=h2, away_score_2=a2,
    )


def is_set3_or_later(match_meta: Dict[str, Any]) -> bool:
    """Return True if the match is currently in or past set 3."""
    status = str(match_meta.get("event_status", "")).strip().lower()
    if "set 3" in status or "set 4" in status or "set 5" in status:
        return True
    # Also check if scores list has 3+ entries (defensive)
    scores = match_meta.get("scores") or []
    if isinstance(scores, list) and len(scores) >= 3:
        return True
    return False
