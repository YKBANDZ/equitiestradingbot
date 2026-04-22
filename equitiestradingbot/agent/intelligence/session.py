"""
Session awareness — classifies the current UTC time into a trading session
and provides per-session characteristics for the agent's decision-making.

Sessions (UTC):
  ASIAN   : 00:00 – 08:00   (Tokyo / Sydney)
  LONDON  : 07:00 – 16:00   (Frankfurt / London)
  OVERLAP : 13:00 – 16:00   (London + New York — highest liquidity)
  NY      : 13:00 – 22:00   (New York)
  OFF     : 22:00 – 00:00   (quiet / weekend)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# Session boundaries in UTC hours (inclusive start, exclusive end)
_SESSIONS = {
    "OVERLAP": (13, 16),   # check first — subset of both London and NY
    "LONDON":  (7,  16),
    "NY":      (13, 22),
    "ASIAN":   (0,   8),
}

# Instruments that are most liquid during each session
_SESSION_INSTRUMENTS = {
    "ASIAN":   ["USD/JPY", "AUD/USD", "NZD/USD", "Nikkei 225", "ASX 200"],
    "LONDON":  ["FTSE 100", "EUR/USD", "GBP/USD", "Gold", "Brent Crude", "DAX"],
    "OVERLAP": ["Gold", "EUR/USD", "GBP/USD", "S&P 500", "NASDAQ", "Brent Crude"],
    "NY":      ["S&P 500", "NASDAQ", "Dow Jones", "Gold", "WTI Crude", "USD pairs"],
    "OFF":     [],
}

# Typical volatility characteristics per session
_SESSION_NOTES = {
    "ASIAN":   "Lower volatility overall. JPY pairs most active. Ranges form for London open.",
    "LONDON":  "High volatility. European data releases 07:00–09:00 UTC. Gold active.",
    "OVERLAP": "Highest liquidity window. Major moves occur. US data 13:30 UTC. Best for Gold.",
    "NY":      "High volatility early (13:00–16:00). Fades toward close. NFP / FOMC impact.",
    "OFF":     "Minimal liquidity. Avoid new positions unless holding overnight with wide stops.",
}


class SessionTracker:
    """Stateless utility — call class methods directly."""

    @staticmethod
    def get_current_session(utc_dt: Optional[datetime] = None) -> str:
        """Return the session name for a given UTC datetime (defaults to now)."""
        dt = utc_dt or datetime.now(timezone.utc)
        hour = dt.hour
        # Weekends — markets largely closed
        if dt.weekday() >= 5:  # Saturday=5, Sunday=6
            return "OFF"
        for name, (start, end) in _SESSIONS.items():
            if start <= hour < end:
                return name
        return "OFF"

    @staticmethod
    def get_session_info(utc_dt: Optional[datetime] = None) -> dict:
        """
        Return a full snapshot of the current session: name, hours,
        preferred instruments, notes, and whether we're in an overlap.
        """
        dt = utc_dt or datetime.now(timezone.utc)
        session = SessionTracker.get_current_session(dt)
        hour = dt.hour

        # Secondary session running alongside (e.g. London + NY during overlap)
        secondary = None
        if session == "OVERLAP":
            secondary = ["LONDON", "NY"]

        return {
            "session":     session,
            "secondary":   secondary,
            "utc_hour":    hour,
            "utc_time":    dt.strftime("%H:%M UTC"),
            "weekday":     dt.strftime("%A"),
            "instruments": _SESSION_INSTRUMENTS.get(session, []),
            "notes":       _SESSION_NOTES.get(session, ""),
            "is_overlap":  session == "OVERLAP",
            "is_weekend":  dt.weekday() >= 5,
        }

    @staticmethod
    def get_all_sessions() -> list[dict]:
        """Return the full session schedule for reference."""
        rows = []
        for name, (start, end) in _SESSIONS.items():
            rows.append({
                "session":     name,
                "utc_start":   f"{start:02d}:00",
                "utc_end":     f"{end:02d}:00",
                "instruments": _SESSION_INSTRUMENTS[name],
                "notes":       _SESSION_NOTES[name],
            })
        rows.append({
            "session":     "OFF",
            "utc_start":   "22:00",
            "utc_end":     "00:00",
            "instruments": [],
            "notes":       _SESSION_NOTES["OFF"],
        })
        return rows

    @staticmethod
    def minutes_until_session(target_session: str, utc_dt: Optional[datetime] = None) -> int:
        """
        How many minutes until the target session opens.
        Returns 0 if it is already active.
        """
        dt = utc_dt or datetime.now(timezone.utc)
        current = SessionTracker.get_current_session(dt)
        if current == target_session:
            return 0
        bounds = _SESSIONS.get(target_session)
        if not bounds:
            return -1
        start_hour = bounds[0]
        current_minutes = dt.hour * 60 + dt.minute
        target_minutes  = start_hour * 60
        diff = target_minutes - current_minutes
        return diff if diff >= 0 else diff + 24 * 60
