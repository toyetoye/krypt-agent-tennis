"""
KRYPT-AGENT — Tennis Odds Feed (Phase E)
Polls The Odds API for live tennis match odds.
Tracks odds movements over time for momentum detection.

Setup:
    1. Get free API key at https://the-odds-api.com
    2. Set env var: $env:ODDS_API_KEY = "your_key"
    3. Or pass directly: TennisFeed(api_key="your_key")

API Limits (free tier): 500 requests/month
    - Poll every 60s during live matches = ~1440/day
    - Use 30s poll for active trading = ~2880/day
    - Paid tier ($79/mo): 10,000 requests/month
"""
import os
import time
import logging
import threading
import requests
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("krypt.tennis_feed")

BASE_URL = "https://api.the-odds-api.com/v4"

# Tennis sport keys on The Odds API
TENNIS_SPORTS = [
    "tennis_atp_australian_open",
    "tennis_atp_french_open",
    "tennis_atp_wimbledon",
    "tennis_atp_us_open",
    "tennis_wta_australian_open",
    "tennis_wta_french_open",
    "tennis_wta_wimbledon",
    "tennis_wta_us_open",
]


@dataclass
class OddsSnapshot:
    """A single odds reading for one player in a match."""
    price: float          # decimal odds (e.g., 1.50 means $1.50 return per $1)
    timestamp: float
    bookmaker: str = ""

    @property
    def implied_prob(self) -> float:
        """Convert decimal odds to implied probability."""
        return 1.0 / self.price if self.price > 0 else 0.0


@dataclass
class TennisMatch:
    """A live or upcoming tennis match with odds history."""
    match_id: str
    sport_key: str
    home_player: str
    away_player: str
    commence_time: str
    is_live: bool = False
    # Odds history per player (deque of OddsSnapshot)
    home_odds_history: deque = field(default_factory=lambda: deque(maxlen=500))
    away_odds_history: deque = field(default_factory=lambda: deque(maxlen=500))
    # Latest best odds across bookmakers
    home_best_back: float = 0.0   # best price to back (highest odds)
    home_best_lay: float = 0.0    # best price to lay (lowest odds)
    away_best_back: float = 0.0
    away_best_lay: float = 0.0
    last_updated: float = 0.0

    def home_odds_move(self, window_sec: float = 300) -> float:
        """% change in home player odds over window."""
        return self._odds_move(self.home_odds_history, window_sec)

    def away_odds_move(self, window_sec: float = 300) -> float:
        """% change in away player odds over window."""
        return self._odds_move(self.away_odds_history, window_sec)

    def _odds_move(self, history: deque, window_sec: float) -> float:
        if len(history) < 2:
            return 0.0
        now = time.time()
        cutoff = now - window_sec
        old = None
        for snap in history:
            if snap.timestamp >= cutoff:
                old = snap
                break
        if old is None or old.price == 0:
            return 0.0
        latest = history[-1]
        return ((latest.price - old.price) / old.price) * 100

    @property
    def label(self) -> str:
        return f"{self.home_player} vs {self.away_player}"


class TennisFeed:
    """
    Polls The Odds API for live tennis odds.
    Thread-safe: runs polling in background, main thread reads via get_*.

    Usage:
        feed = TennisFeed(api_key="your_key")
        feed.start(poll_interval=60)
        matches = feed.get_live_matches()
        feed.stop()
    """

    def __init__(self, api_key: str = None,
                 regions: str = "uk,eu",
                 markets: str = "h2h"):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY", "")
        if not self.api_key:
            logger.warning("No ODDS_API_KEY set. Use demo mode or set env var.")
        self.regions = regions
        self.markets = markets
        self._matches: Dict[str, TennisMatch] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._requests_used = 0
        self._requests_remaining = 0
        self._last_poll = 0.0
        self._active_sports: List[str] = []

    def start(self, poll_interval: float = 60.0):
        """Start background polling thread."""
        self._running = True
        self._poll_interval = poll_interval
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="tennis_feed")
        self._thread.start()
        logger.info(f"Tennis feed started (poll every {poll_interval}s)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Tennis feed stopped")

    def _poll_loop(self):
        """Background polling loop."""
        # First discover which tennis sports are active
        self._discover_active_sports()
        while self._running:
            try:
                self._fetch_odds()
            except Exception as e:
                logger.error(f"Poll error: {e}")
            time.sleep(self._poll_interval)

    def _discover_active_sports(self):
        """Find which tennis tournaments are currently active."""
        try:
            resp = requests.get(
                f"{BASE_URL}/sports",
                params={"apiKey": self.api_key},
                timeout=10)
            if resp.status_code == 200:
                sports = resp.json()
                self._active_sports = [
                    s["key"] for s in sports
                    if s.get("active") and "tennis" in s["key"]]
                logger.info(f"Active tennis sports: {self._active_sports}")
                # Track usage from headers
                self._update_usage(resp.headers)
            else:
                logger.error(f"Sports API error: {resp.status_code}")
                # Fall back to known sport keys
                self._active_sports = TENNIS_SPORTS
        except Exception as e:
            logger.error(f"Sports discovery failed: {e}")
            self._active_sports = TENNIS_SPORTS

    def _update_usage(self, headers):
        """Track API usage from response headers."""
        used = headers.get("x-requests-used")
        remaining = headers.get("x-requests-remaining")
        if used:
            self._requests_used = int(used)
        if remaining:
            self._requests_remaining = int(remaining)

    def _fetch_odds(self):
        """Fetch current odds for all active tennis sports."""
        now = time.time()
        for sport_key in self._active_sports:
            try:
                resp = requests.get(
                    f"{BASE_URL}/sports/{sport_key}/odds",
                    params={
                        "apiKey": self.api_key,
                        "regions": self.regions,
                        "markets": self.markets,
                        "oddsFormat": "decimal",
                    },
                    timeout=10)
                self._update_usage(resp.headers)

                if resp.status_code == 200:
                    events = resp.json()
                    self._process_events(events, sport_key, now)
                elif resp.status_code == 401:
                    logger.error("Invalid API key")
                    return
                elif resp.status_code == 429:
                    logger.warning("Rate limited — backing off")
                    time.sleep(60)
                    return
            except Exception as e:
                logger.error(f"Fetch {sport_key}: {e}")

        self._last_poll = now
        live_count = sum(1 for m in self._matches.values() if m.is_live)
        logger.info(f"Poll done: {len(self._matches)} matches "
                    f"({live_count} live) | "
                    f"API: {self._requests_used} used, "
                    f"{self._requests_remaining} remaining")

    def _process_events(self, events: list, sport_key: str, now: float):
        """Process API response into TennisMatch objects."""
        with self._lock:
            for event in events:
                mid = event["id"]
                # Determine if live
                commence = event.get("commence_time", "")
                is_live = False
                if commence:
                    from datetime import datetime, timezone
                    try:
                        ct = datetime.fromisoformat(
                            commence.replace("Z", "+00:00"))
                        is_live = ct.timestamp() < now
                    except Exception:
                        pass

                # Get or create match
                if mid not in self._matches:
                    self._matches[mid] = TennisMatch(
                        match_id=mid,
                        sport_key=sport_key,
                        home_player=event.get("home_team", "?"),
                        away_player=event.get("away_team", "?"),
                        commence_time=commence,
                    )
                match = self._matches[mid]
                match.is_live = is_live
                match.last_updated = now

                # Extract odds from bookmakers
                home_prices = []
                away_prices = []
                for bm in event.get("bookmakers", []):
                    bm_key = bm.get("key", "")
                    for market in bm.get("markets", []):
                        if market.get("key") != "h2h":
                            continue
                        for outcome in market.get("outcomes", []):
                            price = outcome.get("price", 0)
                            name = outcome.get("name", "")
                            if name == match.home_player:
                                home_prices.append(price)
                                match.home_odds_history.append(
                                    OddsSnapshot(price, now, bm_key))
                            elif name == match.away_player:
                                away_prices.append(price)
                                match.away_odds_history.append(
                                    OddsSnapshot(price, now, bm_key))

                # Best back = highest odds, best lay = lowest odds
                if home_prices:
                    match.home_best_back = max(home_prices)
                    match.home_best_lay = min(home_prices)
                if away_prices:
                    match.away_best_back = max(away_prices)
                    match.away_best_lay = min(away_prices)

    # ── Public API (thread-safe reads) ──────────────────────────

    def get_live_matches(self) -> List[TennisMatch]:
        """Return all currently live matches."""
        with self._lock:
            return [m for m in self._matches.values() if m.is_live]

    def get_all_matches(self) -> List[TennisMatch]:
        """Return all tracked matches (live + upcoming)."""
        with self._lock:
            return list(self._matches.values())

    def get_match(self, match_id: str) -> Optional[TennisMatch]:
        with self._lock:
            return self._matches.get(match_id)

    def get_match_meta(self, match_id: str) -> dict:
        """Return cached metadata for a match (player_keys, tournament, etc).
        Base feed has no metadata; override in subclasses with richer data.
        """
        return {}

    def get_stats(self) -> dict:
        with self._lock:
            live = [m for m in self._matches.values() if m.is_live]
            return {
                "total_matches": len(self._matches),
                "live_matches": len(live),
                "active_sports": len(self._active_sports),
                "requests_used": self._requests_used,
                "requests_remaining": self._requests_remaining,
                "last_poll": self._last_poll,
                "poll_interval": getattr(self, '_poll_interval', 0),
            }
