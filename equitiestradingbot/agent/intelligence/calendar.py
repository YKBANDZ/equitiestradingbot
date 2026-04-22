"""
Economic calendar — fetches upcoming high-impact macro events and determines
whether it is safe to trade around them.

Primary source : Forex Factory JSON feed (free, no key required)
Fallback       : Hard-coded recurring high-impact events by weekday/time

The bot should avoid trading ±30 minutes around RED (high-impact) events.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── Forex Factory feed ────────────────────────────────────────────────────────
_FF_URL     = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_FF_TIMEOUT = 5  # seconds

# Impact levels we consider "high"
_HIGH_IMPACT = {"High", "Holiday"}

# ── Fallback: known recurring high-impact events (UTC approximate times) ──────
# Format: {weekday_name: [(utc_hour, utc_minute, event_name, currencies), ...]}
_RECURRING = {
    "Monday":    [],
    "Tuesday":   [(13, 30, "US Consumer Confidence", ["USD"]),
                  (7,  0,  "German/EU ZEW", ["EUR"])],
    "Wednesday": [(13, 30, "US ADP Employment", ["USD"]),
                  (19,  0, "FOMC Minutes (bi-weekly)", ["USD"])],
    "Thursday":  [(13, 30, "US Jobless Claims", ["USD"]),
                  (7,  45, "ECB Rate Decision (scheduled)", ["EUR"])],
    "Friday":    [(13, 30, "US Non-Farm Payrolls (1st Friday)", ["USD"]),
                  (13, 30, "US CPI / Retail Sales", ["USD"])],
}


class EconomicCalendar:
    """
    Fetches and caches the weekly economic calendar.
    Cache is refreshed automatically when stale (> 1 hour old).
    """

    def __init__(self) -> None:
        self._cache: list[dict] = []
        self._cache_ts: Optional[datetime] = None

    # ─────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────

    def get_upcoming_events(
        self,
        hours_ahead: int = 24,
        impact: Optional[str] = None,
    ) -> list[dict]:
        """
        Return events within the next `hours_ahead` hours.

        Args:
            hours_ahead: look-ahead window in hours
            impact:      filter to 'High', 'Medium', 'Low' (None = all)
        """
        events = self._load_events()
        now    = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)

        results = []
        for ev in events:
            ev_time = ev.get("datetime_utc")
            if not ev_time:
                continue
            if now <= ev_time <= cutoff:
                if impact is None or ev.get("impact") == impact:
                    results.append(self._format_event(ev, now))
        return sorted(results, key=lambda x: x["datetime_utc"])

    def is_high_impact_active(self, buffer_minutes: int = 30) -> bool:
        """
        Returns True if a high-impact event is within ±buffer_minutes of now.
        This is the primary guard used before placing trades.
        """
        return len(self.get_active_events(buffer_minutes=buffer_minutes)) > 0

    def get_active_events(self, buffer_minutes: int = 30) -> list[dict]:
        """
        Return high-impact events within ±buffer_minutes of now.
        """
        events = self._load_events()
        now    = datetime.now(timezone.utc)
        buf    = timedelta(minutes=buffer_minutes)
        active = []
        for ev in events:
            ev_time = ev.get("datetime_utc")
            if not ev_time:
                continue
            if ev.get("impact") not in _HIGH_IMPACT:
                continue
            if (now - buf) <= ev_time <= (now + buf):
                active.append(self._format_event(ev, now))
        return active

    def get_next_high_impact(self) -> Optional[dict]:
        """Return the next upcoming high-impact event, or None."""
        upcoming = self.get_upcoming_events(hours_ahead=48, impact="High")
        return upcoming[0] if upcoming else None

    def get_news_status(self, buffer_minutes: int = 30) -> dict:
        """
        Single-call summary for the agent:
        - is_high_impact_active: bool
        - active_events: list
        - next_event: dict | None
        - safe_to_trade: bool
        """
        active  = self.get_active_events(buffer_minutes=buffer_minutes)
        next_ev = self.get_next_high_impact()
        return {
            "is_high_impact_active": len(active) > 0,
            "active_events":         active,
            "next_high_impact":      next_ev,
            "safe_to_trade":         len(active) == 0,
            "buffer_minutes":        buffer_minutes,
            "checked_at_utc":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    # ─────────────────────────────────────────────
    # Internal fetch / cache
    # ─────────────────────────────────────────────

    def _load_events(self) -> list[dict]:
        """Return cached events, refreshing if stale."""
        now = datetime.now(timezone.utc)
        cache_age = (now - self._cache_ts).total_seconds() if self._cache_ts else 9999
        if cache_age > 3600 or not self._cache:
            self._cache = self._fetch_events()
            self._cache_ts = now
        return self._cache

    def _fetch_events(self) -> list[dict]:
        """Fetch from Forex Factory; fall back to recurring events on error."""
        try:
            resp = requests.get(_FF_URL, timeout=_FF_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json()
            events = self._parse_ff_events(raw)
            log.info("Calendar: fetched %d events from Forex Factory", len(events))
            return events
        except Exception as e:
            log.warning("Calendar: FF fetch failed (%s) — using fallback", e)
            return self._build_fallback_events()

    def _parse_ff_events(self, raw: list[dict]) -> list[dict]:
        """Parse the Forex Factory JSON format into normalised dicts."""
        events = []
        for item in raw:
            try:
                # FF date format: "01-13-2025" time: "8:30am"
                date_str = item.get("date", "")
                time_str = item.get("time", "")
                dt = self._parse_ff_datetime(date_str, time_str)
                events.append({
                    "datetime_utc": dt,
                    "title":        item.get("title", "Unknown"),
                    "country":      item.get("country", ""),
                    "impact":       self._normalise_impact(item.get("impact", "")),
                    "forecast":     item.get("forecast", ""),
                    "previous":     item.get("previous", ""),
                    "source":       "forex_factory",
                })
            except Exception:
                continue
        return events

    def _parse_ff_datetime(self, date_str: str, time_str: str) -> Optional[datetime]:
        """Parse 'MM-DD-YYYY' + '8:30am' into a UTC-aware datetime."""
        if not date_str:
            return None
        try:
            base = datetime.strptime(date_str, "%m-%d-%Y")
        except ValueError:
            return None
        if not time_str or time_str.strip() == "":
            return base.replace(tzinfo=timezone.utc)
        # Normalise: "8:30am" → "8:30 AM"
        time_clean = time_str.strip().upper().replace("AM", " AM").replace("PM", " PM")
        try:
            t = datetime.strptime(time_clean.strip(), "%I:%M %p")
        except ValueError:
            try:
                t = datetime.strptime(time_clean.strip(), "%I %p")
            except ValueError:
                return base.replace(tzinfo=timezone.utc)
        combined = base.replace(hour=t.hour, minute=t.minute, tzinfo=timezone.utc)
        return combined

    def _normalise_impact(self, raw: str) -> str:
        raw = raw.strip().title()
        mapping = {"High": "High", "Medium": "Medium", "Low": "Low", "Holiday": "Holiday"}
        return mapping.get(raw, raw)

    def _build_fallback_events(self) -> list[dict]:
        """Build a set of events from the hard-coded recurring schedule."""
        now    = datetime.now(timezone.utc)
        events = []
        # Look across the next 7 days
        for offset in range(7):
            day = now + timedelta(days=offset)
            weekday_name = day.strftime("%A")
            for utc_hour, utc_min, title, countries in _RECURRING.get(weekday_name, []):
                ev_dt = day.replace(hour=utc_hour, minute=utc_min,
                                    second=0, microsecond=0, tzinfo=timezone.utc)
                events.append({
                    "datetime_utc": ev_dt,
                    "title":        title,
                    "country":      "/".join(countries),
                    "impact":       "High",
                    "forecast":     "",
                    "previous":     "",
                    "source":       "fallback",
                })
        return events

    def _format_event(self, ev: dict, now: datetime) -> dict:
        """Serialise an event dict, replacing datetime objects with ISO strings."""
        ev_time = ev.get("datetime_utc")
        minutes_away = int((ev_time - now).total_seconds() / 60) if ev_time else None
        return {
            "datetime_utc":  ev_time.strftime("%Y-%m-%dT%H:%M:%SZ") if ev_time else None,
            "title":         ev.get("title", ""),
            "country":       ev.get("country", ""),
            "impact":        ev.get("impact", ""),
            "forecast":      ev.get("forecast", ""),
            "previous":      ev.get("previous", ""),
            "minutes_away":  minutes_away,
            "source":        ev.get("source", ""),
        }
