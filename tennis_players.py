"""
KRYPT-AGENT - Tennis player rank / surface stats fetcher

Wraps api-tennis get_players endpoint. Caches results in Redis (24h TTL for rank,
permanent for immutable fields like DOB / country).

The get_players response structure:
  result[0].stats is a list of per-season entries, each with:
    season, type (singles|doubles), rank, titles, matches_won, matches_lost,
    hard_won, hard_lost, clay_won, clay_lost, grass_won, grass_lost

We pick the RIGHT type (singles vs doubles) based on match context, and take
the most recent season with a populated rank.

Usage:
    fetcher = PlayerRankFetcher(api_key=..., redis_url=...)
    info = fetcher.get_player(player_key=86675, event_type="Itf Men Singles")
    # info = {"rank": 1440, "country": "World", "hard_wl": (18, 22), ...}
    # or None if not found
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Optional, Tuple, List

try:
    import redis  # type: ignore
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False

logger = logging.getLogger("krypt.tennis_players")


API_BASE = "https://api.api-tennis.com/tennis/"
CACHE_TTL_SEC = 24 * 3600           # rank snapshot TTL
CACHE_NEGATIVE_TTL = 6 * 3600       # remember misses for 6h
HTTP_TIMEOUT = 15


@dataclass
class PlayerInfo:
    """Aggregated player snapshot used by entry filters."""
    player_key: int
    name: str
    country: str
    dob: Optional[str]
    rank: Optional[int]                   # most recent known rank (for the type we care about)
    rank_season: Optional[str]
    rank_type: Optional[str]              # "singles" | "doubles"
    rank_by_year: dict                    # {"2024": 45, "2023": 62, ...}
    hard_wl: Tuple[int, int]              # (wins, losses) season-latest
    clay_wl: Tuple[int, int]
    grass_wl: Tuple[int, int]
    matches_played_career: int            # sum across all seasons, our chosen type
    raw_type_selected: str                # "singles" | "doubles"
    fetched_at: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, s: str) -> Optional["PlayerInfo"]:
        try:
            d = json.loads(s)
            d["hard_wl"] = tuple(d.get("hard_wl") or (0, 0))
            d["clay_wl"] = tuple(d.get("clay_wl") or (0, 0))
            d["grass_wl"] = tuple(d.get("grass_wl") or (0, 0))
            return cls(**d)
        except Exception:
            return None


def _classify_event_type(event_type_str: str) -> str:
    """Return 'singles' or 'doubles' based on an api-tennis event_type string.
    Examples: "Atp Singles" -> singles, "Itf Men Doubles" -> doubles,
              "Challenger Men Singles" -> singles.
    """
    if not event_type_str:
        return "singles"  # safe default
    et = event_type_str.lower()
    if "doubles" in et:
        return "doubles"
    return "singles"


def _int_or_none(v) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def _int_or_zero(v) -> int:
    r = _int_or_none(v)
    return r if r is not None else 0


def _pick_latest_ranked_season(stats: List[dict], desired_type: str) -> Optional[dict]:
    """Find the most recent season entry matching the desired type that has a
    populated rank. Falls back to any recent same-type entry if rank is missing
    everywhere. Returns None if nothing of the desired type exists."""
    if not stats:
        return None
    same_type = [s for s in stats if (s.get("type") or "").lower() == desired_type]
    if not same_type:
        return None
    # Order by season desc (seasons are strings like "2025")
    try:
        ordered = sorted(same_type, key=lambda s: int(s.get("season") or 0), reverse=True)
    except (TypeError, ValueError):
        ordered = same_type
    # Prefer first with populated rank
    for s in ordered:
        if _int_or_none(s.get("rank")) is not None:
            return s
    # Fallback: latest anything
    return ordered[0] if ordered else None


def _aggregate_career_matches(stats: List[dict], desired_type: str) -> int:
    total = 0
    for s in stats:
        if (s.get("type") or "").lower() != desired_type:
            continue
        total += _int_or_zero(s.get("matches_won")) + _int_or_zero(s.get("matches_lost"))
    return total


def _parse_player_response(raw: dict, event_type_str: str) -> Optional[PlayerInfo]:
    """Turn the api-tennis get_players response into a PlayerInfo."""
    result = raw.get("result") or []
    if not result:
        return None
    r = result[0]
    pkey = _int_or_none(r.get("player_key"))
    if pkey is None:
        return None

    desired = _classify_event_type(event_type_str)
    stats = r.get("stats") or []
    chosen = _pick_latest_ranked_season(stats, desired)

    # Rank-by-year dict for chosen type
    rank_by_year: dict = {}
    for s in stats:
        if (s.get("type") or "").lower() != desired:
            continue
        season = s.get("season")
        rk = _int_or_none(s.get("rank"))
        if season and rk is not None:
            rank_by_year[str(season)] = rk

    if chosen is None:
        # Player has no entries of the desired type (e.g., pure doubles
        # player in a singles match, or vice versa). Signal "unknown".
        return PlayerInfo(
            player_key=pkey,
            name=r.get("player_name") or "",
            country=r.get("player_country") or "",
            dob=r.get("player_bday"),
            rank=None,
            rank_season=None,
            rank_type=None,
            rank_by_year={},
            hard_wl=(0, 0),
            clay_wl=(0, 0),
            grass_wl=(0, 0),
            matches_played_career=0,
            raw_type_selected=desired,
            fetched_at=time.time(),
        )

    return PlayerInfo(
        player_key=pkey,
        name=r.get("player_name") or "",
        country=r.get("player_country") or "",
        dob=r.get("player_bday"),
        rank=_int_or_none(chosen.get("rank")),
        rank_season=str(chosen.get("season") or "") or None,
        rank_type=(chosen.get("type") or desired),
        rank_by_year=rank_by_year,
        hard_wl=(_int_or_zero(chosen.get("hard_won")), _int_or_zero(chosen.get("hard_lost"))),
        clay_wl=(_int_or_zero(chosen.get("clay_won")), _int_or_zero(chosen.get("clay_lost"))),
        grass_wl=(_int_or_zero(chosen.get("grass_won")), _int_or_zero(chosen.get("grass_lost"))),
        matches_played_career=_aggregate_career_matches(stats, desired),
        raw_type_selected=desired,
        fetched_at=time.time(),
    )


class PlayerRankFetcher:
    """Fetch + cache api-tennis player records."""

    def __init__(self, api_key: Optional[str] = None,
                 redis_url: Optional[str] = None,
                 http_timeout: float = HTTP_TIMEOUT):
        self.api_key = api_key or os.getenv("APITENNIS_KEY") or ""
        if not self.api_key:
            raise ValueError("APITENNIS_KEY not set")
        self.http_timeout = http_timeout
        self._redis = None
        if _HAS_REDIS and (redis_url or os.getenv("REDIS_URL")):
            try:
                self._redis = redis.Redis.from_url(
                    redis_url or os.getenv("REDIS_URL"),
                    decode_responses=True, socket_timeout=2,
                )
                self._redis.ping()
                logger.info("PlayerRankFetcher: Redis cache enabled")
            except Exception as e:
                logger.warning(f"PlayerRankFetcher: Redis unavailable ({e}); running without cache")
                self._redis = None

        # In-process LRU fallback
        self._mem_cache: dict = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def get_player(self, player_key: int,
                   event_type: str = "Atp Singles") -> Optional[PlayerInfo]:
        """Return a PlayerInfo for the given player, matching the singles/doubles
        flavour implied by event_type. Cached aggressively.
        """
        if player_key is None:
            return None
        desired = _classify_event_type(event_type)
        cache_key = f"tennis:player:{player_key}:{desired}"

        # 1) In-process
        if cache_key in self._mem_cache:
            info, stored_at = self._mem_cache[cache_key]
            if time.time() - stored_at < CACHE_TTL_SEC:
                return info

        # 2) Redis
        if self._redis:
            try:
                cached = self._redis.get(cache_key)
                if cached == "__MISS__":
                    return None
                if cached:
                    info = PlayerInfo.from_json(cached)
                    if info:
                        self._mem_cache[cache_key] = (info, time.time())
                        return info
            except Exception as e:
                logger.warning(f"Redis get failed: {e}")

        # 3) API
        info = self._fetch_from_api(player_key, event_type)

        # 4) Store
        if info is not None:
            self._mem_cache[cache_key] = (info, time.time())
            if self._redis:
                try:
                    self._redis.set(cache_key, info.to_json(), ex=CACHE_TTL_SEC)
                except Exception as e:
                    logger.warning(f"Redis set failed: {e}")
        else:
            # Negative cache miss so we don't keep hitting the API
            if self._redis:
                try:
                    self._redis.set(cache_key, "__MISS__", ex=CACHE_NEGATIVE_TTL)
                except Exception:
                    pass
        return info

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_from_api(self, player_key: int, event_type: str) -> Optional[PlayerInfo]:
        url = API_BASE + "?" + urllib.parse.urlencode({
            "method": "get_players",
            "APIkey": self.api_key,
            "player_key": player_key,
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "krypt/1.0"})
            with urllib.request.urlopen(req, timeout=self.http_timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            logger.warning(f"get_players urlerror for key={player_key}: {e}")
            return None
        except Exception as e:
            logger.warning(f"get_players failed for key={player_key}: {e}")
            return None

        if raw.get("success") != 1:
            logger.warning(f"get_players non-success for key={player_key}: {raw.get('error')}")
            return None
        info = _parse_player_response(raw, event_type)
        return info

    # ------------------------------------------------------------------

    def warm(self, player_keys: list, event_types: list) -> dict:
        """Batch-warm many (key, event_type) pairs. Returns {key: PlayerInfo}.
        Respects cache, skips duplicates.
        """
        assert len(player_keys) == len(event_types)
        out = {}
        for k, et in zip(player_keys, event_types):
            if k is None:
                continue
            info = self.get_player(k, et)
            out[k] = info
        return out

    # ------------------------------------------------------------------
    # For diagnostics
    # ------------------------------------------------------------------
    def cache_stats(self) -> dict:
        return {
            "in_memory_entries": len(self._mem_cache),
            "redis_available": self._redis is not None,
        }
