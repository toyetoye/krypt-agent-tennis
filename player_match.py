"""
KRYPT-AGENT - Player name matching between api-tennis and Sackmann

Given an api-tennis player_name string, return the corresponding Sackmann
player_id so we can look up ELO. Best effort; returns None on ambiguity.

Matching flow:
    1. Reject doubles team names (contain "/")
    2. Check manual overrides file first
    3. Normalize name (strip accents, lowercase, collapse whitespace)
    4. Exact match on (normalized_first, normalized_last) against Sackmann index
    5. If multiple Sackmann hits, tie-break by most-recent ELO activity
    6. Fallback: last-name + first-letter-of-first-name match
    7. Cache result (positive or negative) in Redis + in-process dict

Build a singleton via get_matcher(). First use builds the index from
sackmann_data/atp_players.csv and _elo_snapshot.json.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
import unicodedata
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

try:
    import redis  # type: ignore
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False

logger = logging.getLogger("krypt.player_match")

PLAYERS_CSV = "sackmann_data/atp_players.csv"
ELO_SNAPSHOT = "_elo_snapshot.json"
OVERRIDES_FILE = "tennis_name_overrides.json"
CACHE_PREFIX = "tennis:namematch:"
CACHE_TTL = 30 * 24 * 3600          # 30 days - names are very stable


def _strip_accents(s: str) -> str:
    """NFKD decompose then drop combining marks. Turns 'Mišić' -> 'Misic'."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace, remove punct (except hyphens)."""
    if not s:
        return ""
    s = _strip_accents(s).lower().strip()
    # Keep letters, digits, spaces, hyphens. Drop dots, commas, slashes etc.
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _split_name_parts(full_name: str) -> Tuple[List[str], str]:
    """Return (first_tokens, last_name).

    first_tokens can be multiple if the input uses multiple initials like
    'T. J. Fancutt' -> (['t','j'], 'fancutt').
    Multi-word last names like 'Alex De Minaur' -> (['alex'], 'de minaur').

    Heuristic: leading single-letter tokens are treated as initials. The
    remainder (non-single-letter tokens) is the last name. If ALL tokens are
    single letters (weird), use last token as surname.
    """
    n = _normalize(full_name)
    if not n:
        return ([], "")
    parts = n.split()
    if len(parts) == 1:
        return ([], parts[0])
    initials = []
    i = 0
    while i < len(parts) - 1 and len(parts[i]) == 1:
        initials.append(parts[i])
        i += 1
    if i == 0:
        # no leading initials - classic "First Last..." form
        return ([parts[0]], " ".join(parts[1:]))
    # leading initials present; everything after them is last name
    last = " ".join(parts[i:])
    return (initials, last)


def _split_first_last(full_name: str) -> Tuple[str, str]:
    """Legacy shape: (first_token, last_name). First_token is the first
    initial if multiple were present, else the full first name. Kept for
    index building against Sackmann\'s (first, last) tuple.
    """
    tokens, last = _split_name_parts(full_name)
    if not tokens:
        return ("", last)
    # When we have multiple initials, the first-name index was built from
    # Sackmann\'s FULL first name. Return just the leading character so the
    # exact-match path will fail and we fall through to the initials path.
    if all(len(t) == 1 for t in tokens):
        return (tokens[0], last)
    return (tokens[0], last)


def _is_doubles_team(name: str) -> bool:
    """api-tennis joins doubles teams with '/' - we can't ELO a pair."""
    return "/" in (name or "")


class PlayerMatcher:
    """Matches api-tennis player names to Sackmann player_ids."""

    def __init__(self, redis_url: Optional[str] = None):
        # player_id -> {"first": str, "last": str, "first_norm": str, "last_norm": str}
        self._sack_players: Dict[str, dict] = {}
        # (first_norm, last_norm) -> list of player_ids (usually 1)
        self._exact_idx: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        # last_norm -> list of player_ids (for fallback)
        self._last_idx: Dict[str, List[str]] = defaultdict(list)
        # player_id -> recency score (higher = more recent activity, used as tiebreaker)
        self._recency: Dict[str, int] = {}

        # Manual overrides: api_tennis_name (normalized) -> sackmann_player_id
        self._overrides: Dict[str, str] = {}

        # In-process cache of matches already resolved this session
        self._mem_cache: Dict[str, Optional[str]] = {}

        self._redis = None
        if _HAS_REDIS and (redis_url or os.getenv("REDIS_URL")):
            try:
                self._redis = redis.Redis.from_url(
                    redis_url or os.getenv("REDIS_URL"),
                    decode_responses=True, socket_timeout=2,
                )
                self._redis.ping()
                logger.info("PlayerMatcher: Redis cache enabled")
            except Exception as e:
                logger.warning(f"PlayerMatcher: Redis unavailable ({e}); memory-only")
                self._redis = None

        self._build_index()
        self._load_overrides()

    # ------------------------------------------------------------------
    # Index build
    # ------------------------------------------------------------------

    def _build_index(self):
        t0 = time.time()
        if not os.path.exists(PLAYERS_CSV):
            raise FileNotFoundError(f"missing: {PLAYERS_CSV}")

        with open(PLAYERS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = (row.get("player_id") or "").strip()
                first = (row.get("name_first") or "").strip()
                last = (row.get("name_last") or "").strip()
                if not pid or not last:
                    continue
                first_norm = _normalize(first)
                last_norm = _normalize(last)
                self._sack_players[pid] = {
                    "first": first, "last": last,
                    "first_norm": first_norm, "last_norm": last_norm,
                }
                self._exact_idx[(first_norm, last_norm)].append(pid)
                self._last_idx[last_norm].append(pid)

        # Recency tiebreaker: if we have ELO snapshot, use n_overall as proxy
        # for "more recent / more data". Players with more matches win ties.
        if os.path.exists(ELO_SNAPSHOT):
            try:
                with open(ELO_SNAPSHOT, "r", encoding="utf-8") as f:
                    elo = json.load(f)
                for pid, d in elo.items():
                    # Use n_overall + last_match_date-as-int as tiebreak score
                    lmd = d.get("last_match_date", "")
                    lmd_score = int(lmd) if lmd.isdigit() else 0
                    self._recency[pid] = d.get("n_overall", 0) * 100000000 + lmd_score
            except Exception as e:
                logger.warning(f"failed to load ELO recency: {e}")

        logger.info(
            f"PlayerMatcher index built: {len(self._sack_players):,} Sackmann "
            f"players, {len(self._exact_idx):,} unique (first,last) pairs, "
            f"{len(self._recency):,} with recency score ({time.time()-t0:.2f}s)"
        )

    def _load_overrides(self):
        if not os.path.exists(OVERRIDES_FILE):
            return
        try:
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for api_name, pid in raw.items():
                self._overrides[_normalize(api_name)] = str(pid)
            if self._overrides:
                logger.info(f"PlayerMatcher: {len(self._overrides)} manual overrides loaded")
        except Exception as e:
            logger.warning(f"overrides load failed: {e}")

    # ------------------------------------------------------------------
    # Public match
    # ------------------------------------------------------------------

    def match(self, api_tennis_name: str) -> Optional[str]:
        """Return Sackmann player_id string or None if no confident match."""
        if not api_tennis_name:
            return None
        if _is_doubles_team(api_tennis_name):
            return None

        key = _normalize(api_tennis_name)
        if not key:
            return None

        # 1. In-process cache
        if key in self._mem_cache:
            return self._mem_cache[key]

        # 2. Redis cache
        if self._redis:
            try:
                r = self._redis.get(CACHE_PREFIX + key)
                if r == "__MISS__":
                    self._mem_cache[key] = None
                    return None
                if r:
                    self._mem_cache[key] = r
                    return r
            except Exception as e:
                logger.warning(f"Redis get failed: {e}")

        # 3. Override
        if key in self._overrides:
            pid = self._overrides[key]
            self._store(key, pid)
            return pid

        # 4. Exact match first, last
        first_norm, last_norm = _split_first_last(api_tennis_name)
        pid = self._resolve(first_norm, last_norm)

        # 5. Initials path: try every leading initial against last-name hits.
        # Handles both "J. Sinner" (single initial) and "T. J. Fancutt" (multi).
        if pid is None:
            initials, true_last = _split_name_parts(api_tennis_name)
            if initials and all(len(t) == 1 for t in initials) and true_last:
                # Try each initial as the potential first-name starting letter
                all_hits = self._last_idx.get(true_last, [])
                for init in initials:
                    candidates = [p for p in all_hits
                                  if self._sack_players[p]["first_norm"].startswith(init)]
                    if candidates:
                        pid = self._tiebreak(candidates)
                        if pid:
                            break
                # Also update last_norm to the corrected value for downstream fallbacks
                last_norm = true_last

        # 6. Fallback: last name unique?
        if pid is None:
            candidates = self._last_idx.get(last_norm, [])
            if len(candidates) == 1:
                pid = candidates[0]
            elif len(candidates) > 1 and first_norm:
                # Restrict to those whose first-name shares any starting char
                filtered = [p for p in candidates
                            if self._sack_players[p]["first_norm"].startswith(first_norm[:1])]
                pid = self._tiebreak(filtered) if filtered else None

        self._store(key, pid)
        return pid

    def _resolve(self, first_norm: str, last_norm: str) -> Optional[str]:
        hits = self._exact_idx.get((first_norm, last_norm), [])
        return self._tiebreak(hits)

    def _tiebreak(self, pids: List[str]) -> Optional[str]:
        if not pids:
            return None
        if len(pids) == 1:
            return pids[0]
        # Prefer highest recency score (more matches, recent activity)
        scored = [(p, self._recency.get(p, 0)) for p in pids]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

    def _store(self, key: str, pid: Optional[str]):
        self._mem_cache[key] = pid
        if self._redis:
            try:
                self._redis.set(CACHE_PREFIX + key,
                                pid if pid is not None else "__MISS__",
                                ex=CACHE_TTL)
            except Exception as e:
                logger.warning(f"Redis set failed: {e}")

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def match_with_debug(self, api_tennis_name: str) -> dict:
        """Return a dict with the match result AND intermediate reasoning for debug."""
        out = {"input": api_tennis_name, "normalized": _normalize(api_tennis_name),
               "is_doubles": _is_doubles_team(api_tennis_name),
               "pid": None, "sackmann_name": None, "method": None,
               "candidates": []}
        if out["is_doubles"]:
            out["method"] = "doubles_team_rejected"
            return out
        key = out["normalized"]
        if key in self._overrides:
            pid = self._overrides[key]
            out["pid"] = pid
            out["method"] = "manual_override"
            out["sackmann_name"] = self._sackmann_display(pid)
            return out
        initials, true_last = _split_name_parts(api_tennis_name)
        first_norm, last_norm = _split_first_last(api_tennis_name)
        out["first_norm"] = first_norm
        out["last_norm"] = last_norm
        out["initials_detected"] = initials if all(len(t) == 1 for t in initials) else []
        exact = self._exact_idx.get((first_norm, last_norm), [])
        out["exact_candidates"] = [(p, self._sackmann_display(p)) for p in exact]
        if len(exact) == 1:
            out["pid"] = exact[0]
            out["method"] = "exact_single"
        elif len(exact) > 1:
            out["pid"] = self._tiebreak(exact)
            out["method"] = "exact_tiebreak"
        else:
            # Try initials path first (handles single + multi initial cases)
            if (initials and all(len(t) == 1 for t in initials) and true_last):
                all_hits = self._last_idx.get(true_last, [])
                for init in initials:
                    candidates = [p for p in all_hits
                                  if self._sack_players[p]["first_norm"].startswith(init)]
                    if candidates:
                        out["pid"] = self._tiebreak(candidates)
                        out["method"] = f"initials_path_via_{init}"
                        out["filtered_candidates"] = [(p, self._sackmann_display(p)) for p in candidates]
                        break
                last_norm = true_last  # correct for below fallback
            # last-name fallback
            if out["pid"] is None:
                ln = self._last_idx.get(last_norm, [])
                if len(ln) == 1:
                    out["pid"] = ln[0]
                    out["method"] = "last_name_unique"
                elif ln and first_norm:
                    filtered = [p for p in ln
                                if self._sack_players[p]["first_norm"].startswith(first_norm[:1])]
                    if filtered:
                        out["pid"] = self._tiebreak(filtered)
                        out["method"] = "last_name_plus_first_letter"
                        out["filtered_candidates"] = [(p, self._sackmann_display(p)) for p in filtered]
                if out["pid"] is None and ln:
                    out["method"] = "ambiguous_no_match"
                    out["last_name_candidates_count"] = len(ln)
        if out["pid"]:
            out["sackmann_name"] = self._sackmann_display(out["pid"])
        return out

    def _sackmann_display(self, pid: str) -> str:
        p = self._sack_players.get(pid, {})
        return f"{p.get('first','?')} {p.get('last','?')} [{pid}]"

    def stats(self) -> dict:
        return {
            "sackmann_players": len(self._sack_players),
            "unique_first_last": len(self._exact_idx),
            "with_recency": len(self._recency),
            "overrides": len(self._overrides),
            "in_mem_cache": len(self._mem_cache),
            "redis_enabled": self._redis is not None,
        }


# Singleton accessor
_singleton: Optional[PlayerMatcher] = None

def get_matcher() -> PlayerMatcher:
    global _singleton
    if _singleton is None:
        _singleton = PlayerMatcher()
    return _singleton
