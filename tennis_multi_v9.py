"""KRYPT-AGENT - Tennis Multi Runner v9 (9-variant loss-cap + fav-only experiment)

Default port 8888.

Trimmed down from v8's 11 variants. Removed V1/V2 (dominated by V3),
V4/V5 (doubles question resolved), V9 (marginal gain over V6), V11
(too-stacked — we want isolated effects, not kitchen sink).

Added V12/V13 (hard_cap variations) and V14/V15 (fav-only variations)
based on madrid hardcap-leak + fav-side analysis:
  - V6 baseline simulates to +$57 / 809 trades (+$0.070/trade).
  - V6 + cap=$0.50 simulates (realistic) to +$203 / 809 trades (3.6x).
  - V6 + cap=$0.35 simulates to +$292 / 809 trades (~max loss $0.50 live).
  - V6 + FAV-only (entry<2.00) simulates +$0.104/trade on 305 trades.
  - V6 + FAV-only + cap=$0.50 simulates to +$48 / 305 trades (+$0.157/trade).
  - Hard-caps account for -$422 on v5-madrid. Largest single leak.

Code fix (this revision):
  - hard_cap exits now trigger re-entry cooldown (same as stop_loss).
    Without this, V12/V13 took 2 extra trades per 7-trade V6 baseline
    because they didn't honor _recent_sl, partially explaining extra
    losses seen on Railway's 1st run.

Per-variant stake override (Tier A staking):
  Each variant spec can include an optional "stake": <float> key. When set,
  that variant uses that stake instead of the --stake CLI default. Left
  unset everywhere for now (all variants at CLI default). Enables bumping
  proven-winner variants with a one-line config change. Note: hard_cap_dollars
  is absolute $, not a % of stake — if you raise a variant's stake, consider
  scaling its hard_cap proportionally to preserve the cap-as-% behaviour.

Variants in this run:
  V3  = skip_lay + skip Challenger + skip odds [1.60, 1.80)       — state-filter-null control
  V6  = V3 + skip 5 neg states (full v7 stack)                    — anchor / current best
  V7  = V3 duplicate                                              — A/A variance control
  V8  = V6 + smart-E (3-loss cooldown 30min)                      — consecutive-loss cooldown
  V10 = V6 but ATP/WTA-only (block ITF too)                       — tier concentration
  V12 = V6 + hard_cap=$0.50                                       — user-requested loss cap
  V13 = V6 + hard_cap=$0.35                                       — strict (~max_loss ~$0.50 live)
  V14 = V6 + FAV-only (entry<2.00) + cap=$0.50                    — simple "back the fav"
  V15 = V6 + STRONG-FAV-only + cap=$0.50                          — [1.40,1.80)u[1.90,2.00); skips [1.80,1.90) loser sub-band
  V16 = V14 but ATP/WTA-only                                      — fav-only, main tour
  V17 = V15 but ATP/WTA-only                                      — strong-fav, main tour
  V18 = V14 but Chall/ITF-only (no ATP/WTA)                       — fav-only, lower tour
  V19 = V15 but Chall/ITF-only (no ATP/WTA)                       — strong-fav, lower tour

V14/V15 both use cap=$0.50 because backtests showed fav-side edge is
strongest WITH the cap on top. V15 adds 2 extra skip bands to test whether
[1.80,1.90) is truly a loser sub-band (n=27, -$0.106/trade in madrid —
small sample, could be noise).

Key comparisons this run answers:
  - V6 vs V3 : does the state filter work? (live only; not simulable on CSV)
  - V3 vs V7 : A/A variance floor — minimum dollar gap considered "signal"
  - V8 vs V6 : does consecutive-loss cooldown pay off?
  - V10 vs V6: is ITF worth blocking?
  - V12 vs V6: does tighter cap deliver simulated 3.6x?
  - V13 vs V12: does going stricter add edge or eat into winners?
  - V13 vs V8 : which lever is stronger — cap or cooldown?
  - V14 vs V12: does the fav-only filter add edge over V12?
  - V15 vs V14: is [1.80,1.90) really a loser, or was the madrid n=27 noise?
  - V16 vs V14: is fav-only edge stronger when restricted to main tour?
  - V18 vs V14: is fav-only edge stronger when restricted to lower tour?
  - V16 vs V18: which tier hosts the fav-side edge — main tour or lower tour?
  - V17 vs V19: same question for strong-fav
  - V10 vs V16: on main tour, is dog-side or fav-side the better direction?

Kill-switch: --max-session-loss only. $25/variant protects each independently.
"""
import argparse
import json
import logging
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

logger = logging.getLogger("krypt.tennis_multi_v9")

_shared = {
    "strategies": [],
    "start_ts": 0.0,
    "history": [],
    "history_lock": None,
    "feed": None,
}


class DashHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path in ("/api", "/api/"):
            self._send_api()
        elif self.path in ("/", "/index.html", "/dashboard"):
            self._send_html()
        else:
            self.send_error(404)

    def _send_api(self):
        elapsed = (time.time() - _shared["start_ts"]) / 60 if _shared["start_ts"] else 0
        strategies_data = []
        for label, desc, strat in _shared["strategies"]:
            st = strat.get_stats()
            # Pull bets for this variant. get_bets_list() returns up to 50 most
            # recent (newest first) with entry/exit odds, pnl, reason, held.
            try:
                bets = strat.get_bets_list()
            except Exception:
                bets = []
            # Enrich each bet with entry/exit wall-clock timestamps so the
            # dashboard can show time and group identical trades across variants.
            enriched_bets = []
            for b in bets:
                rec = dict(b)
                # Look up the underlying TennisBet by id for timestamps the
                # dict form omits (entry_time is private to the dataclass).
                raw = strat._bets.get(rec.get("id"))
                if raw is not None:
                    rec["entry_ts"] = round(raw.entry_time, 2)
                    rec["exit_ts"] = (round(raw.exit_time, 2)
                                      if raw.closed and raw.exit_time else None)
                else:
                    rec["entry_ts"] = None
                    rec["exit_ts"] = None
                enriched_bets.append(rec)
            strategies_data.append({
                "label": label,
                "desc": desc,
                "stake_amount": strat.cfg.stake_amount,
                "hard_cap_dollars": strat.cfg.hard_cap_dollars,
                "total_pnl": round(st.get("total_pnl", 0.0), 4),
                "trades": st.get("total_trades", 0),
                "winning": st.get("winning", 0),
                "losing": st.get("losing", 0),
                "win_rate": round(st.get("win_rate", 0.0) * 100, 1),
                "open_bets": st.get("open_bets", 0),
                "total_commission": round(st.get("total_commission", 0.0), 4),
                "signals_detected": st.get("signals_detected", 0),
                "entries_blocked_tier": st.get("entries_blocked_tier", 0),
                "entries_blocked_entry_state": st.get("entries_blocked_entry_state", 0),
                "entries_blocked_odds_band": st.get("entries_blocked_odds_band", 0),
                "entries_blocked_doubles": st.get("entries_blocked_doubles", 0),
                "entries_blocked_lay": st.get("entries_blocked_lay", 0),
                "entries_blocked_loss_streak": st.get("entries_blocked_loss_streak", 0),
                "rank_elo_enabled": st.get("rank_elo_enabled", False),
                "bets": enriched_bets,
            })
        feed_stats = (_shared["feed"].get_stats() if _shared["feed"] else {})
        history = []
        if _shared["history_lock"]:
            with _shared["history_lock"]:
                history = list(_shared["history"])
        payload = {
            "elapsed_min": round(elapsed, 2),
            "feed": feed_stats,
            "strategies": strategies_data,
            "history": history,
        }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        try:
            with open("tennis_multi_dashboard.html", "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)


def _serve_dashboard(port):
    server = HTTPServer(("0.0.0.0", port), DashHandler)
    logger.info(f"Dashboard on http://localhost:{port}/")
    server.serve_forever()


def _snapshot_loop():
    while True:
        time.sleep(30)
        try:
            now = time.time() - _shared["start_ts"]
            row = {"t": round(now, 1), "pnl": {}}
            for label, desc, strat in _shared["strategies"]:
                row["pnl"][label] = round(strat.get_stats().get("total_pnl", 0.0), 4)
            with _shared["history_lock"]:
                _shared["history"].append(row)
                if len(_shared["history"]) > 1000:
                    _shared["history"] = _shared["history"][-1000:]
        except Exception as e:
            logger.error(f"snapshot error: {e}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=int, default=540)
    p.add_argument("--poll", type=int, default=5)
    p.add_argument("--stake", type=float, default=10.0)
    p.add_argument("--port", type=int, default=8888)
    p.add_argument("--max-session-loss", type=float, default=None)
    p.add_argument("--hard-cap", type=float, default=None)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not os.environ.get("APITENNIS_KEY"):
        print("ERROR: APITENNIS_KEY env var not set.")
        return

    from tennis_feed_apitennis import TennisFeedAPITennis
    from tennis_detector import TennisDetector, TennisConfig
    from tennis_strategy import TennisStrategy

    feed = TennisFeedAPITennis()
    base_cfg = TennisConfig(stake_amount=args.stake)
    detector = TennisDetector(feed, base_cfg)

    # v5-madrid backtest identified these 5 states as negative-edge:
    NEG_STATES = frozenset({
        "BEHIND_SET1_HEAVY",
        "BEHIND_LOST_SET1_BAGEL",
        "AHEAD_SET1_HEAVY",
        "AHEAD_WON_SET1_FADING",
        "EVEN_LOST_SET1",
    })

    strat_configs = [
        {
            "label": "V3",
            "desc": "skip_lay + skip Chall + skip [1.60,1.80)",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger"}),
                "skip_odds_bands": ((1.60, 1.80),),
            },
        },
        {
            "label": "V6",
            "desc": "V3 + skip 5 neg states (full v7 stack)",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger"}),
                "skip_odds_bands": ((1.60, 1.80),),
                "blocked_entry_states": NEG_STATES,
            },
        },
        {
            "label": "V7",
            "desc": "V3 duplicate (A/A variance control)",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger"}),
                "skip_odds_bands": ((1.60, 1.80),),
            },
        },
        {
            "label": "V8",
            "desc": "V6 + smart-E (3-loss cooldown 30min)",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger"}),
                "skip_odds_bands": ((1.60, 1.80),),
                "blocked_entry_states": NEG_STATES,
                "max_consecutive_losses_per_match": 3,
                "consecutive_loss_cooldown_sec": 1800,
            },
        },
        {
            "label": "V10",
            "desc": "V6 but ATP/WTA-only (block ITF too)",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger", "itf"}),
                "skip_odds_bands": ((1.60, 1.80),),
                "blocked_entry_states": NEG_STATES,
            },
        },
        {
            "label": "V12",
            "desc": "V6 + hard_cap=$0.50 (tighter loss cap)",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger"}),
                "skip_odds_bands": ((1.60, 1.80),),
                "blocked_entry_states": NEG_STATES,
                # Overrides the CLI --hard-cap for this variant only.
                "hard_cap_dollars": 0.50,
            },
        },
        {
            "label": "V13",
            "desc": "V6 + hard_cap=$0.35 (strict; target ~$0.50 max live)",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger"}),
                "skip_odds_bands": ((1.60, 1.80),),
                "blocked_entry_states": NEG_STATES,
                "hard_cap_dollars": 0.35,
            },
        },
        {
            "label": "V14",
            "desc": "V6 + FAV-only (entry<2.00) + cap=$0.50",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger"}),
                # Skip [1.60,1.80) AND everything 2.00+. Leaves:
                # [1.20,1.60) and [1.80,2.00) — i.e. current-odds favs only.
                "skip_odds_bands": ((1.60, 1.80), (2.00, 99.0)),
                "blocked_entry_states": NEG_STATES,
                "hard_cap_dollars": 0.50,
            },
        },
        {
            "label": "V15",
            "desc": "V6 + STRONG-FAV-only + cap=$0.50",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger"}),
                # Fav bands with the 2 softest sub-bands dropped. Leaves:
                # [1.40,1.60) and [1.90,2.00). Drops [1.20,1.40) (mediocre,
                # +$0.059/trade) and [1.80,1.90) (negative, -$0.106/trade
                # but small n=27 — this variant tests whether that was noise).
                "skip_odds_bands": ((1.20, 1.40), (1.60, 1.80), (1.80, 1.90),
                                    (2.00, 99.0)),
                "blocked_entry_states": NEG_STATES,
                "hard_cap_dollars": 0.50,
            },
        },
        # ------------------------------------------------------------------
        # TIER x ODDS 2x2 MATRIX (Apr 22 — driven by V10's +$0.45/trade edge
        # on Railway suggesting tier is a stronger axis than odds filter).
        #
        # Baseline on Railway: V10 = V6 + ATP/WTA-only, dog-allowed.
        # These 4 test whether fav-only / strong-fav wins MORE on main tour
        # (V16/V17) than it does on lower tour (V18/V19), or vice versa.
        #
        # Volume estimates (extrapolating from V14/V15/V10 13.75h totals):
        #   V16: ~140 trades / 13.75h  (fav x ATP/WTA  - slow)
        #   V17: ~80 trades / 13.75h   (strong-fav x ATP/WTA - very slow)
        #   V18: ~330 trades / 13.75h  (fav x Chall/ITF - fast)
        #   V19: ~190 trades / 13.75h  (strong-fav x Chall/ITF - decent)
        # ------------------------------------------------------------------
        {
            "label": "V16",
            "desc": "V14 but ATP/WTA-only (fav-only on main tour)",
            "kwargs": {
                "skip_lay_signals": True,
                # Block Challenger AND ITF -> ATP/WTA only (same as V10).
                "blocked_event_types": frozenset({"challenger", "itf"}),
                "skip_odds_bands": ((1.60, 1.80), (2.00, 99.0)),
                "blocked_entry_states": NEG_STATES,
                "hard_cap_dollars": 0.50,
            },
        },
        {
            "label": "V17",
            "desc": "V15 but ATP/WTA-only (strong-fav on main tour)",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"challenger", "itf"}),
                "skip_odds_bands": ((1.20, 1.40), (1.60, 1.80), (1.80, 1.90),
                                    (2.00, 99.0)),
                "blocked_entry_states": NEG_STATES,
                "hard_cap_dollars": 0.50,
            },
        },
        {
            "label": "V18",
            "desc": "V14 but Chall/ITF-only (fav-only on lower tour)",
            "kwargs": {
                "skip_lay_signals": True,
                # Block ATP and WTA -> Challenger + ITF only.
                "blocked_event_types": frozenset({"atp", "wta"}),
                "skip_odds_bands": ((1.60, 1.80), (2.00, 99.0)),
                "blocked_entry_states": NEG_STATES,
                "hard_cap_dollars": 0.50,
            },
        },
        {
            "label": "V19",
            "desc": "V15 but Chall/ITF-only (strong-fav on lower tour)",
            "kwargs": {
                "skip_lay_signals": True,
                "blocked_event_types": frozenset({"atp", "wta"}),
                "skip_odds_bands": ((1.20, 1.40), (1.60, 1.80), (1.80, 1.90),
                                    (2.00, 99.0)),
                "blocked_entry_states": NEG_STATES,
                "hard_cap_dollars": 0.50,
            },
        },
    ]

    strategies = []
    for spec in strat_configs:
        cfg_kwargs = dict(spec["kwargs"])
        if args.hard_cap is not None:
            cfg_kwargs.setdefault("hard_cap_dollars", args.hard_cap)
        # Optional per-variant stake override. Default: CLI --stake value.
        # Tier A infrastructure: lets us bump a winning variant (e.g. V14=$15)
        # with one config line without touching anything else. Hard_cap is
        # still absolute dollars — if you scale stake, consider scaling cap
        # too to keep cap/stake ratio constant (e.g. stake $20 + cap $1.00).
        variant_stake = spec.get("stake", args.stake)
        cfg = TennisConfig(
            stake_amount=variant_stake,
            max_open_bets=100,
            **cfg_kwargs,
        )
        strat = TennisStrategy(feed, detector, cfg)
        strat._label = spec["label"]
        strat._kill_override = spec.get("kill_override", None)
        strategies.append((spec["label"], spec["desc"], strat))
        stake_note = (f" stake=${variant_stake:.2f}"
                      if variant_stake != args.stake else "")
        logger.info(f"Variant {spec['label']}: {spec['desc']}"
                    f" (kill-switch: ${strat._kill_override or args.max_session_loss or 'off'}"
                    f"{stake_note})")

    import threading
    _shared["start_ts"] = time.time()
    _shared["strategies"] = strategies
    _shared["history_lock"] = threading.Lock()
    _shared["feed"] = feed

    dash_thread = Thread(target=_serve_dashboard, args=(args.port,), daemon=True)
    dash_thread.start()
    snap_thread = Thread(target=_snapshot_loop, daemon=True)
    snap_thread.start()

    feed.start(
        poll_interval=args.poll,
        open_positions_callback=lambda: any(
            any(not b.closed for b in s._bets.values()) for _, _, s in strategies
        ),
    )

    end_time = time.time() + args.minutes * 60
    tick = 0
    start = time.time()
    print(f"Running for {args.minutes}m on port {args.port}...")
    for label, desc, _ in strategies:
        print(f"  {label}: {desc}")

    try:
        while time.time() < end_time:
            try:
                signals = detector.tick()
            except Exception as e:
                logger.error(f"detector.tick() error: {e}")
                signals = []
            for label, desc, strat in strategies:
                if getattr(strat, "_killed", False):
                    continue
                kill_threshold = getattr(strat, "_kill_override", None)
                if kill_threshold is None:
                    kill_threshold = args.max_session_loss
                if kill_threshold is not None:
                    if strat.total_pnl <= -abs(kill_threshold):
                        strat._killed = True
                        logger.warning(f"KILL-SWITCH strat {label}: "
                                       f"session_loss={strat.total_pnl:.2f} "
                                       f"<= -{kill_threshold}")
                        continue
                # Note: no consecutive-loss kill-switch in v7 — relying on
                # session-loss only. See tennis_multi_v7.py docstring.
                try:
                    strat.process_signals(signals)
                except Exception as e:
                    logger.error(f"strat {label} process_signals error: {e}")
            tick += 1
            if tick % 12 == 0:
                parts = []
                for label, desc, strat in strategies:
                    st = strat.get_stats()
                    parts.append(f"{label}=" + format(st['total_pnl'], '+.2f') + "/" + str(st['total_trades']))
                print(f"[{int((time.time()-start)/60)}m] " + "  ".join(parts))
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        feed.stop()
        for label, desc, strat in strategies:
            st = strat.get_stats()
            print(f"FINAL {label} ({desc}): PnL=$" + format(st['total_pnl'], '+.2f') + " "
                  f"trades=" + str(st['total_trades']) + " "
                  f"W/L=" + str(st['winning']) + "/" + str(st['losing']))


if __name__ == "__main__":
    main()
