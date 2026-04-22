"""
Tool-callable wrappers for the intelligence layer.

These are passed to the Anthropic API `tools=` list alongside the journal
tools so the Claude agent can query market context before making decisions.

IntelligenceTools requires the Broker instance for price data fetching.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from .session import SessionTracker
from .calendar import EconomicCalendar
from .regime import RegimeDetector

if TYPE_CHECKING:
    from ...components.broker.broker import Broker

log = logging.getLogger(__name__)


class IntelligenceTools:
    # Price data cache: {epic: (timestamp, history)} — avoids hammering IG API
    _CACHE_TTL_SECONDS = 300  # re-fetch after 5 minutes

    def __init__(self, broker: "Broker") -> None:
        self._broker   = broker
        self._calendar = EconomicCalendar()
        self._session  = SessionTracker()
        self._price_cache: dict = {}  # epic → (fetched_at, MarketHistory)

    # ─────────────────────────────────────────────
    # Tool callables
    # ─────────────────────────────────────────────

    def get_session(self) -> dict:
        """Return the current trading session and its characteristics."""
        return self._session.get_session_info()

    def get_all_sessions(self) -> list[dict]:
        """Return the full session schedule (hours, instruments, notes)."""
        return self._session.get_all_sessions()

    def get_economic_calendar(
        self,
        hours_ahead: int = 24,
        impact: str = "",
    ) -> list[dict]:
        """
        Return upcoming economic events within the next hours_ahead hours.
        impact: 'High' | 'Medium' | 'Low' | '' (empty = all)
        """
        return self._calendar.get_upcoming_events(
            hours_ahead=hours_ahead,
            impact=impact or None,
        )

    def get_news_status(self, buffer_minutes: int = 30) -> dict:
        """
        Check whether high-impact news is active right now.
        Returns is_high_impact_active, active_events, next event, and safe_to_trade flag.
        Recommended buffer is 30 minutes before/after events.
        """
        return self._calendar.get_news_status(buffer_minutes=buffer_minutes)

    def _get_prices_cached(self, epic: str, interval: str, bars: int):
        """Return cached price history, re-fetching only if TTL has expired."""
        import time
        cache_key = f"{epic}:{interval}:{bars}"
        now = time.monotonic()
        if cache_key in self._price_cache:
            fetched_at, history = self._price_cache[cache_key]
            if now - fetched_at < self._CACHE_TTL_SECONDS:
                log.debug("IntelligenceTools: cache hit for %s", cache_key)
                return history
        iv      = self._resolve_interval(interval)
        market  = self._broker.get_market_info(epic)
        history = self._broker.get_prices(market, iv, bars)
        self._price_cache[cache_key] = (now, history)
        return history

    def get_market_regime(self, epic: str, interval: str = "HOUR", bars: int = 100) -> dict:
        """
        Detect the market regime for a given instrument.

        Args:
            epic:     IG epic ID, e.g. 'CS.D.USCGC.TODAY.IP'
            interval: price bar interval — MINUTE_5 | MINUTE_15 | HOUR | HOUR_4 | DAY
            bars:     number of bars to fetch (more = better EMA accuracy)

        Returns a dict with: regime, ema_50, ema_200, adx_14, rsi_14, atr_14,
        volatility_elevated, trending, current_close, and more.
        """
        return self._fetch_regime(epic, interval, bars)

    def get_volatility(self, epic: str, interval: str = "HOUR", bars: int = 50) -> dict:
        """
        Return the current ATR-based volatility reading for an instrument.
        Lighter than get_market_regime — only ATR computation.
        """
        try:
            history = self._get_prices_cached(epic, interval, bars)
            if history is None or history.dataframe is None:
                return {"error": "No price data", "atr": None}
            df  = history.dataframe
            atr = RegimeDetector.get_volatility_atr(df)
            return {"epic": epic, "interval": interval, "atr_14": atr}
        except Exception as e:
            log.warning("IntelligenceTools.get_volatility failed for %s: %s", epic, e)
            return {"error": str(e), "atr": None}

    def get_market_context(self, epic: str) -> dict:
        """
        Full intelligence snapshot for a single instrument — combines session,
        news status, and regime into one call. Use this before making a trade
        decision to get complete situational awareness.
        """
        session    = self.get_session()
        news       = self.get_news_status()
        regime     = self.get_market_regime(epic)
        next_event = self._calendar.get_next_high_impact()

        return {
            "epic":         epic,
            "session":      session,
            "news_status":  news,
            "next_event":   next_event,
            "regime":       regime,
            "safe_to_trade": (
                not news["is_high_impact_active"]
                and session["session"] != "OFF"
            ),
        }

    # ─────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────

    def _fetch_regime(self, epic: str, interval_str: str, bars: int) -> dict:
        try:
            history = self._get_prices_cached(epic, interval_str, bars)
            if history is None or history.dataframe is None:
                return {"regime": "UNKNOWN", "error": "No price data returned"}
            analysis = RegimeDetector.get_full_analysis(history.dataframe)
            analysis["epic"]     = epic
            analysis["interval"] = interval_str
            return analysis
        except Exception as e:
            log.warning("IntelligenceTools.get_market_regime failed for %s: %s", epic, e)
            return {"regime": "UNKNOWN", "error": str(e)}

    @staticmethod
    def _resolve_interval(interval_str: str):
        from ...components.utils import Interval
        mapping = {
            "MINUTE_1":  Interval.MINUTE_1,
            "MINUTE_5":  Interval.MINUTE_5,
            "MINUTE_15": Interval.MINUTE_15,
            "MINUTE_30": Interval.MINUTE_30,
            "HOUR":      Interval.HOUR,
            "HOUR_2":    Interval.HOUR_2,
            "HOUR_4":    Interval.HOUR_4,
            "DAY":       Interval.DAY,
            "WEEK":      Interval.WEEK,
        }
        iv = mapping.get(interval_str.upper())
        if iv is None:
            raise ValueError(f"Unknown interval: {interval_str}. Valid: {list(mapping)}")
        return iv

    # ─────────────────────────────────────────────
    # Anthropic API tool definitions
    # ─────────────────────────────────────────────

    def get_tool_map(self) -> dict:
        return {
            "get_session":           self.get_session,
            "get_all_sessions":      self.get_all_sessions,
            "get_economic_calendar": self.get_economic_calendar,
            "get_news_status":       self.get_news_status,
            "get_market_regime":     self.get_market_regime,
            "get_volatility":        self.get_volatility,
            "get_market_context":    self.get_market_context,
        }

    def get_tool_definitions(self) -> list[dict]:
        return [
            {
                "name": "get_session",
                "description": (
                    "Return the current trading session (ASIAN/LONDON/OVERLAP/NY/OFF), "
                    "active hours, preferred instruments for this session, and session notes. "
                    "Call this at the start of every decision cycle."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_all_sessions",
                "description": "Return the full trading session schedule with hours and instruments.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_economic_calendar",
                "description": (
                    "Return upcoming economic events. Use hours_ahead=2 before placing "
                    "a trade to check for imminent high-impact releases. "
                    "Use hours_ahead=24 for a daily overview."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "hours_ahead": {
                            "type": "integer",
                            "description": "Look-ahead window in hours (default 24)",
                            "default": 24,
                        },
                        "impact": {
                            "type": "string",
                            "enum": ["High", "Medium", "Low", ""],
                            "description": "Filter by impact level. Empty string = all.",
                        },
                    },
                },
            },
            {
                "name": "get_news_status",
                "description": (
                    "Check whether high-impact news is active right now or within "
                    "buffer_minutes. Returns safe_to_trade=false when active. "
                    "ALWAYS call this before placing a new trade."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "buffer_minutes": {
                            "type": "integer",
                            "description": "Minutes before/after event to flag as active (default 30)",
                            "default": 30,
                        }
                    },
                },
            },
            {
                "name": "get_market_regime",
                "description": (
                    "Detect the market regime for an instrument using EMAs, ADX, ATR, and RSI. "
                    "Returns: TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE. "
                    "Use interval='HOUR' and bars=100 for intraday decisions. "
                    "Use interval='DAY' for a macro view."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "epic": {
                            "type": "string",
                            "description": "IG epic ID",
                        },
                        "interval": {
                            "type": "string",
                            "enum": ["MINUTE_5", "MINUTE_15", "MINUTE_30",
                                     "HOUR", "HOUR_2", "HOUR_4", "DAY"],
                            "default": "HOUR",
                        },
                        "bars": {
                            "type": "integer",
                            "description": "Number of bars to fetch (min 50, recommend 100)",
                            "default": 250,
                        },
                    },
                    "required": ["epic"],
                },
            },
            {
                "name": "get_volatility",
                "description": (
                    "Return the current ATR-14 volatility reading for an instrument. "
                    "Lighter alternative to get_market_regime when you only need the ATR."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "epic":     {"type": "string"},
                        "interval": {"type": "string", "default": "HOUR"},
                        "bars":     {"type": "integer", "default": 50},
                    },
                    "required": ["epic"],
                },
            },
            {
                "name": "get_market_context",
                "description": (
                    "Full intelligence snapshot for one instrument — session, news status, "
                    "market regime, and safe_to_trade flag in a single call. "
                    "Use this as your primary pre-trade situational awareness check."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "epic": {
                            "type": "string",
                            "description": "IG epic ID of the instrument to analyse",
                        }
                    },
                    "required": ["epic"],
                },
            },
        ]
