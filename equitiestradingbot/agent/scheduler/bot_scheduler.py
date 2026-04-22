"""
BotScheduler — drives 24/7 operation by replacing the fixed spin_interval
with session-aware sleep durations.

Key behaviours:
  • Scans frequently during high-liquidity sessions (OVERLAP/LONDON/NY)
  • Scans less frequently during ASIAN session
  • Sleeps until the next session open when in OFF hours (22:00–00:00 UTC)
  • Sleeps from Friday 22:00 UTC until Sunday 22:00 UTC over the weekend
  • Provides a full status snapshot for logging and diagnostics
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..intelligence.session import SessionTracker, _SESSIONS

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Default spin intervals (seconds) per session
# Override via [scheduler] block in live_trading_bot.toml
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_INTERVALS: dict[str, int] = {
    "OVERLAP": 60,     # 13:00–16:00 UTC — peak liquidity, scan every minute
    "LONDON":  120,    # 07:00–16:00 UTC — high activity
    "NY":      120,    # 13:00–22:00 UTC — high activity
    "ASIAN":   300,    # 00:00–08:00 UTC — lower liquidity
    "OFF":     1800,   # 22:00–00:00 UTC — almost no activity; check every 30 min
}

# Markets open Sunday 22:00 UTC and close Friday 22:00 UTC (CFD norm)
_MARKET_OPEN_WEEKDAY  = 6   # Sunday (weekday index)
_MARKET_OPEN_HOUR     = 22
_MARKET_CLOSE_WEEKDAY = 4   # Friday
_MARKET_CLOSE_HOUR    = 22

# Minimum sleep so the bot never hammers the API
_MIN_SLEEP_SECS = 30


class BotScheduler:
    """
    Calculates how long the bot should sleep between each main-loop spin.

    Args:
        intervals: optional dict overriding DEFAULT_INTERVALS per session
    """

    def __init__(self, intervals: Optional[dict[str, int]] = None) -> None:
        self._intervals = {**DEFAULT_INTERVALS, **(intervals or {})}

    # ─────────────────────────────────────────────
    # Primary interface
    # ─────────────────────────────────────────────

    def get_sleep_duration(self, utc_dt: Optional[datetime] = None) -> int:
        """
        Return the number of seconds to sleep before the next main-loop spin.

        Logic:
          1. Weekend → sleep until Sunday 22:00 UTC
          2. Session is OFF → sleep until the next session opens
          3. Active session → return the configured interval for that session
        """
        now = utc_dt or datetime.now(timezone.utc)

        if self._is_weekend(now):
            secs = self._seconds_until_sunday_open(now)
            log.info(
                "Scheduler: weekend — sleeping %d s (%.1f h) until Sunday open",
                secs, secs / 3600,
            )
            return max(_MIN_SLEEP_SECS, secs)

        session = SessionTracker.get_current_session(now)

        if session == "OFF":
            secs = self._seconds_until_next_session(now)
            log.info(
                "Scheduler: OFF session — sleeping %d s (%.1f min) until next session",
                secs, secs / 60,
            )
            return max(_MIN_SLEEP_SECS, secs)

        secs = self._intervals.get(session, DEFAULT_INTERVALS["LONDON"])
        log.debug("Scheduler: %s session — sleeping %d s", session, secs)
        return secs

    def get_status(self, utc_dt: Optional[datetime] = None) -> dict:
        """
        Full scheduler snapshot for logging / diagnostics.
        """
        now     = utc_dt or datetime.now(timezone.utc)
        session = SessionTracker.get_current_session(now)
        sleep   = self.get_sleep_duration(now)

        next_open_secs = self._seconds_until_next_session(now) if session == "OFF" else 0
        next_session   = self._next_session_name(now) if session == "OFF" else None

        return {
            "utc_time":           now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "weekday":            now.strftime("%A"),
            "session":            session,
            "is_weekend":         self._is_weekend(now),
            "sleep_secs":         sleep,
            "sleep_human":        _fmt_duration(sleep),
            "next_session":       next_session,
            "next_session_in_s":  next_open_secs if session == "OFF" else None,
            "intervals":          self._intervals,
        }

    def should_trade(self, utc_dt: Optional[datetime] = None) -> bool:
        """
        Returns True if the bot should be actively looking for trades right now.
        False on weekends and during the OFF session.
        """
        now = utc_dt or datetime.now(timezone.utc)
        if self._is_weekend(now):
            return False
        return SessionTracker.get_current_session(now) != "OFF"

    # ─────────────────────────────────────────────
    # Weekend detection
    # ─────────────────────────────────────────────

    @staticmethod
    def _is_weekend(now: datetime) -> bool:
        """
        True from Friday 22:00 UTC to Sunday 22:00 UTC.
        """
        wd = now.weekday()
        # Full Saturday (weekday 5) is always weekend
        if wd == 5:
            return True
        # Friday after 22:00
        if wd == _MARKET_CLOSE_WEEKDAY and now.hour >= _MARKET_CLOSE_HOUR:
            return True
        # Sunday before 22:00
        if wd == _MARKET_OPEN_WEEKDAY and now.hour < _MARKET_OPEN_HOUR:
            return True
        return False

    @staticmethod
    def _seconds_until_sunday_open(now: datetime) -> int:
        """
        Seconds until Sunday 22:00 UTC from a weekend datetime.
        """
        # Find the next Sunday
        days_ahead = (_MARKET_OPEN_WEEKDAY - now.weekday()) % 7
        if days_ahead == 0 and now.hour >= _MARKET_OPEN_HOUR:
            days_ahead = 7  # already past Sunday open → next week
        target = (now + timedelta(days=days_ahead)).replace(
            hour=_MARKET_OPEN_HOUR, minute=0, second=0, microsecond=0
        )
        return max(0, int((target - now).total_seconds()))

    # ─────────────────────────────────────────────
    # Session timing
    # ─────────────────────────────────────────────

    @staticmethod
    def _seconds_until_next_session(now: datetime) -> int:
        """
        Seconds until the earliest upcoming session open from now.
        Considers only: ASIAN (00:00), LONDON (07:00), NY (13:00).
        OVERLAP is a sub-window, not independently scheduled.
        """
        scheduled = {
            "ASIAN":  0,
            "LONDON": 7,
            "NY":     13,
        }
        current_minutes = now.hour * 60 + now.minute
        min_secs = None
        for _name, open_hour in scheduled.items():
            target_minutes = open_hour * 60
            diff_minutes   = target_minutes - current_minutes
            if diff_minutes <= 0:
                diff_minutes += 24 * 60  # next day
            diff_secs = diff_minutes * 60
            if min_secs is None or diff_secs < min_secs:
                min_secs = diff_secs
        return min_secs or 1800

    @staticmethod
    def _next_session_name(now: datetime) -> str:
        """Return the name of the next session that will open."""
        scheduled = {"ASIAN": 0, "LONDON": 7, "NY": 13}
        current_minutes = now.hour * 60 + now.minute
        best_name, best_diff = "ASIAN", float("inf")
        for name, open_hour in scheduled.items():
            diff = open_hour * 60 - current_minutes
            if diff <= 0:
                diff += 24 * 60
            if diff < best_diff:
                best_diff = diff
                best_name = name
        return best_name


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_duration(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"
