"""
KRYPT-AGENT - Tennis Odds Feed (api-tennis.com adapter)
Drop-in replacement for TennisFeed that talks to api-tennis.com.

Exposes the SAME public interface as tennis_feed.TennisFeed so tennis_detector.py
and tennis_strategy.py keep working unchanged. Swap the import and the detector
gets api-tennis.com data transparently.

Setup:
    1. Sign up at https://api-tennis.com
    2. Set env var: $env:APITENNIS_KEY = "your_key"
    3. Or pass directly: TennisFeedAPITennis(api_key="your_key")

Business tier ($80/mo) is required for get_live_odds.

Live-odds schema (verified 2026-04-17):
  result is dict keyed by event_key.
  Each event has live_odds as list of {odd_name, suspended, type, value,
  handicap, upd}. Main match-winner market is odd_name="To Win" with
  type="Home" / "Away". Player names (event_first_player / event_second_player)
  are NULL on live odds payloads; we cache names from get_livescore.
"""
import os
import time
import logging
import threading
import requests
from typing import Dict, List, Optional, Tuple

from tennis_feed import OddsSnapshot, TennisMatch

logger = logging.getLogger("krypt.tennis_feed_at")

BASE_URL = "https://api.api-tennis.com/tennis"

# Market names seen on the match-winner line. Order = probe priority.
# "To Win" is what live payloads use; "Home/Away" is pre-match naming.
MATCH_WINNER_CANDIDATES = (
    "To Win",
    "Home/Away",
    "Match Winner",
    "Home / Away",
    "Winner",
    "To Win Match",
    "Match Result",
)
MIN_OK_ODDS = 1.01
MAX_OK_ODDS = 50.0

# How often to refresh player-name cache from get_livescore.
PLAYER_NAME_REFRESH_SEC = 300.0


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _is_first_player(outcome_type):
    if not outcome_type:
        return None
    t = outcome_type.strip().lower()
    if t in ("1", "home", "first", "first player", "player 1", "p1"):
        return True
    if t in ("2", "away", "second", "second player", "player 2", "p2"):
        return False
    return None


class TennisFeedAPITennis:
    """Polls api-tennis.com get_live_odds. Same public API as TennisFeed."""

    def __init__(self, api_key=None, request_timeout=15.0,
                 max_odds_staleness_sec=600.0):
        self.api_key = api_key or os.environ.get("APITENNIS_KEY", "")
        if not self.api_key:
            logger.warning("No APITENNIS_KEY set. Feed will not fetch.")
        self.request_timeout = request_timeout
        self.max_odds_staleness_sec = max_odds_staleness_sec
        self._matches: Dict[str, TennisMatch] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._poll_interval = 60.0
        self._requests_used_session = 0
        self._last_poll = 0.0
        self._event_winner_market: Dict[str, str] = {}
        # player_key -> display name, populated from get_livescore
        self._player_names: Dict[str, str] = {}
        # event_key -> (first_player_key, second_player_key, tournament_name,
        #               tournament_round, event_type_type)
        self._event_meta: Dict[str, dict] = {}
        self._last_livescore_refresh = 0.0

    def start(self, poll_interval=60.0, fast_poll_interval=None,
              open_positions_callback=None):
        self._running = True
        self._poll_interval = poll_interval
        # Fast poll when there are open positions (tighter SL window).
        # Defaults to min(5s, poll_interval).
        self._fast_poll_interval = (
            fast_poll_interval if fast_poll_interval is not None
            else min(5.0, poll_interval))
        self._open_positions_callback = open_positions_callback
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="tennis_feed_at")
        self._thread.start()
        if open_positions_callback is not None:
            logger.info(f"api-tennis feed started (poll every {poll_interval}s, "
                        f"fast {self._fast_poll_interval}s when positions open)")
        else:
            logger.info(f"api-tennis feed started (poll every {poll_interval}s)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("api-tennis feed stopped")

    def _poll_loop(self):
        while self._running:
            try:
                # Refresh player-name cache before odds so newly live matches
                # can be labeled on the very first poll.
                self._maybe_refresh_livescore()
                self._fetch_live_odds()
            except Exception as e:
                logger.error(f"Poll error: {e}")
            # Choose poll interval based on open positions
            has_positions = False
            if self._open_positions_callback is not None:
                try:
                    has_positions = bool(self._open_positions_callback())
                except Exception:
                    has_positions = False
            interval = (self._fast_poll_interval if has_positions
                        else self._poll_interval)
            for _ in range(int(max(1, interval))):
                if not self._running:
                    break
                time.sleep(1)

    def _call(self, method, **params):
        if not self.api_key:
            return None
        params["method"] = method
        params["APIkey"] = self.api_key
        try:
            resp = requests.get(BASE_URL, params=params,
                                timeout=self.request_timeout)
            self._requests_used_session += 1
            if resp.status_code != 200:
                logger.error(f"{method}: HTTP {resp.status_code} "
                             f"body={resp.text[:200]}")
                return None
            data = resp.json()
            if data.get("success") != 1:
                logger.error(f"{method}: api-tennis error "
                             f"body={str(data)[:200]}")
                return None
            return data
        except requests.RequestException as e:
            logger.error(f"{method}: request failed: {e}")
            return None
        except ValueError as e:
            logger.error(f"{method}: bad JSON: {e}")
            return None

    def _maybe_refresh_livescore(self):
        """Pull get_livescore periodically to learn player names + meta.

        live_odds payload has NULL player names — only player_keys. We need
        get_livescore (which has names + keys) to label matches.
        """
        now = time.time()
        if now - self._last_livescore_refresh < PLAYER_NAME_REFRESH_SEC:
            return
        data = self._call("get_livescore")
        if data is None:
            return
        result = data.get("result") or []
        if not isinstance(result, list):
            return
        new_names = 0
        for m in result:
            if not isinstance(m, dict):
                continue
            ekey = str(m.get("event_key", "")).strip()
            if not ekey:
                continue
            p1k = str(m.get("first_player_key", "")).strip()
            p2k = str(m.get("second_player_key", "")).strip()
            p1n = m.get("event_first_player") or ""
            p2n = m.get("event_second_player") or ""
            if p1k and p1n and p1k not in self._player_names:
                self._player_names[p1k] = p1n
                new_names += 1
            if p2k and p2n and p2k not in self._player_names:
                self._player_names[p2k] = p2n
                new_names += 1
            # Extract set-by-set scores (list of dicts with score_set/score_first/score_second)
            scores = m.get("scores") or []
            if not isinstance(scores, list):
                scores = []
            self._event_meta[ekey] = {
                "p1_key": p1k,
                "p2_key": p2k,
                "tournament": m.get("tournament_name", ""),
                "round": m.get("tournament_round", ""),
                "event_type": m.get("event_type_type", ""),
                "event_date": m.get("event_date", ""),
                "event_time": m.get("event_time", ""),
                "event_status": m.get("event_status", "") or "",
                "scores": scores,  # list of per-set scores as returned by api-tennis
            }
        self._last_livescore_refresh = now
        if new_names:
            logger.info(f"Livescore refresh: +{new_names} player names, "
                        f"{len(self._event_meta)} matches in meta cache, "
                        f"{len(self._player_names)} players total")

    def _fetch_live_odds(self):
        data = self._call("get_live_odds")
        if data is None:
            return
        result = data.get("result") or {}
        if not isinstance(result, dict):
            logger.warning(f"get_live_odds: result is {type(result).__name__}")
            return
        now = time.time()
        parsed = 0
        skipped_no_names = 0
        skipped_no_odds = 0
        for event_key, event in result.items():
            outcome = self._process_live_event(str(event_key), event, now)
            if outcome == "ok":
                parsed += 1
            elif outcome == "no_names":
                skipped_no_names += 1
            elif outcome == "no_odds":
                skipped_no_odds += 1
        self._last_poll = now
        self._prune_stale(now)
        with self._lock:
            live_count = sum(1 for m in self._matches.values() if m.is_live)
        logger.info(f"Poll done: {parsed} events with H2H odds, "
                    f"{live_count} live tracked "
                    f"(skip: {skipped_no_names} no-names, "
                    f"{skipped_no_odds} no-odds) | "
                    f"session calls: {self._requests_used_session}")

    def _process_live_event(self, event_key, event, now) -> str:
        """Returns: 'ok' | 'no_names' | 'no_odds' | 'bad'."""
        if not isinstance(event, dict):
            return "bad"

        # Resolve player names. Live odds payload has these as NULL, so we
        # merge from meta cache (populated by get_livescore).
        first_name = (event.get("event_first_player") or "").strip()
        second_name = (event.get("event_second_player") or "").strip()
        p1k = str(event.get("first_player_key", "")).strip()
        p2k = str(event.get("second_player_key", "")).strip()
        if not first_name and p1k:
            first_name = self._player_names.get(p1k, "")
        if not second_name and p2k:
            second_name = self._player_names.get(p2k, "")

        meta = self._event_meta.get(str(event_key), {})
        if not first_name:
            first_name = (self._player_names.get(meta.get("p1_key", ""))
                          or (f"Player #{p1k}" if p1k else "?"))
        if not second_name:
            second_name = (self._player_names.get(meta.get("p2_key", ""))
                           or (f"Player #{p2k}" if p2k else "?"))

        # Still no useful names — odds payload must be truly anonymous.
        # Keep the match rather than skip, using key-based placeholders.
        # This ensures odds-drift detection can still fire; downstream UI
        # will just show "Player #86675" until livescore refresh fills it.
        if first_name in ("", "?") or second_name in ("", "?"):
            if not (p1k and p2k):
                return "no_names"

        tournament = (event.get("tournament_name")
                      or meta.get("tournament") or "")
        commence = (f"{event.get('event_date') or meta.get('event_date','')} "
                    f"{event.get('event_time') or meta.get('event_time','')}"
                    ).strip()

        live_odds = event.get("live_odds") or []
        if not isinstance(live_odds, list) or not live_odds:
            return "no_odds"

        first_price, second_price = self._extract_h2h_prices(
            event_key, live_odds)
        if first_price <= 0 or second_price <= 0:
            return "no_odds"

        with self._lock:
            match = self._matches.get(event_key)
            if match is None:
                match = TennisMatch(
                    match_id=event_key,
                    sport_key=tournament or "tennis",
                    home_player=first_name,
                    away_player=second_name,
                    commence_time=commence,
                )
                self._matches[event_key] = match
            else:
                # Late-arriving names can upgrade placeholder labels.
                if match.home_player.startswith("Player #") and first_name \
                        and not first_name.startswith("Player #"):
                    match.home_player = first_name
                if match.away_player.startswith("Player #") and second_name \
                        and not second_name.startswith("Player #"):
                    match.away_player = second_name
            match.is_live = True
            match.last_updated = now
            match.home_best_back = first_price
            match.home_best_lay = first_price
            match.away_best_back = second_price
            match.away_best_lay = second_price
            match.home_odds_history.append(
                OddsSnapshot(first_price, now, "apitennis"))
            match.away_odds_history.append(
                OddsSnapshot(second_price, now, "apitennis"))
        return "ok"

    def _extract_h2h_prices(self, event_key, live_odds):
        cached = self._event_winner_market.get(event_key)
        candidates = []
        if cached:
            candidates.append(cached)
        candidates.extend(n for n in MATCH_WINNER_CANDIDATES if n != cached)
        by_name: Dict[str, List[dict]] = {}
        for o in live_odds:
            if not isinstance(o, dict):
                continue
            name = o.get("odd_name")
            if not name:
                continue
            by_name.setdefault(name, []).append(o)
        # Any other 2-outcome market is a fallback if no canonical names hit.
        extra = [n for n, items in by_name.items()
                 if n not in candidates and len(items) >= 2]
        candidates.extend(extra)
        for name in candidates:
            items = by_name.get(name)
            if not items:
                continue
            first_prices, second_prices = [], []
            for o in items:
                if str(o.get("suspended", "")).strip().lower() == "yes":
                    continue
                side = _is_first_player(o.get("type", ""))
                price = _as_float(o.get("value"))
                if side is None:
                    continue
                if not (MIN_OK_ODDS <= price <= MAX_OK_ODDS):
                    continue
                if side:
                    first_prices.append(price)
                else:
                    second_prices.append(price)
            if first_prices and second_prices:
                self._event_winner_market[event_key] = name
                return first_prices[0], second_prices[0]
        return 0.0, 0.0

    def _prune_stale(self, now):
        with self._lock:
            for mid, m in self._matches.items():
                if m.is_live and (now - m.last_updated) > self.max_odds_staleness_sec:
                    m.is_live = False

    # ── Public API (matches tennis_feed.TennisFeed) ────────────────────

    def get_live_matches(self):
        with self._lock:
            return [m for m in self._matches.values() if m.is_live]

    def get_all_matches(self):
        with self._lock:
            return list(self._matches.values())

    def get_match(self, match_id):
        with self._lock:
            return self._matches.get(match_id)

    def get_match_meta(self, match_id):
        """Return cached meta dict for a match_id (str event_key).
        Includes p1_key, p2_key, tournament, round, event_type, dates.
        Returns {} if not found.
        """
        with self._lock:
            return dict(self._event_meta.get(str(match_id), {}))

    def get_live_point_state(self, match_id):
        """Fetch FRESH point-level state for a specific live match.

        Unlike get_match_meta (which returns cached set-level scores), this
        hits api-tennis get_livescore?match_key=<id> live, parsing:
          - current game score (e.g. "0 - 30")
          - which player is serving ("First Player" / "Second Player")
          - active break_point / set_point / match_point flags from the
            most recent point in pointbypoint

        Returns dict with keys, or None on failure:
          match_id: str
          game_score: str            e.g. "0 - 30"
          server: str                "first" | "second" | "unknown"
          break_point_for: str       "first" | "second" | None
          set_point_for: str         "first" | "second" | None
          match_point_for: str       "first" | "second" | None
          fetched_ts: float          wall-clock timestamp

        Cost: 1 api-tennis call per invocation. Should be used sparingly
        (e.g. only when we're about to place a trade).
        """
        data = self._call("get_livescore", match_key=str(match_id))
        if data is None:
            return None
        result = data.get("result") or []
        if not isinstance(result, list) or not result:
            return None
        m = result[0]
        if not isinstance(m, dict):
            return None

        # Who's serving
        srv_raw = (m.get("event_serve") or "").strip().lower()
        if "first" in srv_raw:
            server = "first"
        elif "second" in srv_raw:
            server = "second"
        else:
            server = "unknown"

        # Current game score (e.g. "0 - 30")
        game_score = (m.get("event_game_result") or "").strip()

        # Walk pointbypoint to find the LATEST point and its pressure flags.
        # api-tennis seems to list games in chronological order; the last
        # point in the last game is "current".
        break_point_for = None
        set_point_for = None
        match_point_for = None
        pbp = m.get("pointbypoint") or []
        if isinstance(pbp, list) and pbp:
            last_game = pbp[-1]
            if isinstance(last_game, dict):
                points = last_game.get("points") or []
                if isinstance(points, list) and points:
                    last_point = points[-1]
                    if isinstance(last_point, dict):
                        bp = last_point.get("break_point")
                        sp = last_point.get("set_point")
                        mp = last_point.get("match_point")
                        # api-tennis marks as "First Play" / "Second Play"
                        # meaning "first/second player HAS the break point",
                        # i.e. the non-serving player is about to break.
                        def _side(v):
                            if not v:
                                return None
                            s = str(v).strip().lower()
                            if "first" in s:
                                return "first"
                            if "second" in s:
                                return "second"
                            return None
                        break_point_for = _side(bp)
                        set_point_for = _side(sp)
                        match_point_for = _side(mp)

        return {
            "match_id": str(match_id),
            "game_score": game_score,
            "server": server,
            "break_point_for": break_point_for,
            "set_point_for": set_point_for,
            "match_point_for": match_point_for,
            "fetched_ts": time.time(),
        }

    def get_stats(self):
        with self._lock:
            live = [m for m in self._matches.values() if m.is_live]
            return {
                "total_matches": len(self._matches),
                "live_matches": len(live),
                "active_sports": 0,
                "requests_used": self._requests_used_session,
                "requests_remaining": -1,
                "last_poll": self._last_poll,
                "poll_interval": self._poll_interval,
                "provider": "api-tennis.com",
            }
