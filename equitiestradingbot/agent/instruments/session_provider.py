"""
SessionAwareMarketProvider — replaces the static epic list with a
session-intelligent instrument queue.

Behaviour per spin of the main loop:
  1. Detect current trading session (ASIAN / LONDON / OVERLAP / NY / OFF)
  2. Load instruments for that session from InstrumentUniverse, ranked by
     recent journal performance
  3. Skip instruments that have triggered a high-impact news block
  4. Return Markets one by one via next(); raise StopIteration when the
     queue is exhausted for this session cycle
  5. On the next spin, refresh the queue (session may have changed)

Interface matches the existing MarketProvider so tradingbot.py needs
only minimal changes to use it.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from ..intelligence.session import SessionTracker
from ..intelligence.calendar import EconomicCalendar
from .universe import InstrumentUniverse

if TYPE_CHECKING:
    from ...components.broker.broker import Broker
    from ...interfaces.market import Market

log = logging.getLogger(__name__)


class SessionAwareMarketProvider:
    """
    Drop-in replacement for MarketProvider when market_source.active = 'universe'.

    Args:
        universe:  InstrumentUniverse instance
        broker:    Broker instance for fetching Market objects
        calendar:  EconomicCalendar for news filtering
        news_filter: if True, skip instruments during active high-impact news
    """

    def __init__(
        self,
        universe: InstrumentUniverse,
        broker: "Broker",
        calendar: Optional[EconomicCalendar] = None,
        news_filter: bool = True,
    ) -> None:
        self._universe    = universe
        self._broker      = broker
        self._calendar    = calendar or EconomicCalendar()
        self._news_filter = news_filter

        # State
        self._queue:          list[dict] = []  # list of instrument dicts
        self._queue_session:  str        = ""
        self._skipped_epics:  set[str]   = set()

    # ─────────────────────────────────────────────
    # MarketProvider-compatible interface
    # ─────────────────────────────────────────────

    def next(self) -> "Market":
        """
        Return the next Market to analyse.
        Raises StopIteration when the session queue is exhausted.
        """
        self._maybe_refresh_queue()

        while self._queue:
            instrument = self._queue.pop(0)
            epic = instrument["epic"]

            # News filter — skip if high-impact event is active right now
            if self._news_filter and self._calendar.is_high_impact_active(buffer_minutes=30):
                log.info(
                    "SessionProvider: skipping %s — high-impact news active", epic
                )
                self._skipped_epics.add(epic)
                continue

            try:
                market = self._broker.get_market_info(epic)
                log.debug(
                    "SessionProvider: serving %s (%s) for session %s",
                    market.name, epic, self._queue_session,
                )
                return market
            except Exception as e:
                log.warning(
                    "SessionProvider: could not fetch market info for %s — %s", epic, e
                )
                continue

        # Queue empty
        log.info(
            "SessionProvider: session queue exhausted for %s (%d skipped due to news)",
            self._queue_session, len(self._skipped_epics),
        )
        raise StopIteration

    def reset(self) -> None:
        """Force a full queue refresh on the next next() call."""
        self._queue         = []
        self._queue_session = ""
        self._skipped_epics = set()

    def get_market_from_epic(self, epic: str) -> "Market":
        """Look up a market directly by epic (used for processing open positions)."""
        return self._broker.get_market_info(epic)

    def search_market(self, search: str) -> "Market":
        """Proxy to broker search (used by backtest path)."""
        results = self._broker.search_market(search)
        if not results:
            raise ValueError(f"No market found for search: {search}")
        return results[0]

    # ─────────────────────────────────────────────
    # State / diagnostics
    # ─────────────────────────────────────────────

    @property
    def current_session(self) -> str:
        return self._queue_session

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    def get_status(self) -> dict:
        """Return a snapshot of the provider state for logging/diagnostics."""
        return {
            "session":        self._queue_session,
            "queue_remaining": len(self._queue),
            "queue_epics":    [i["epic"] for i in self._queue],
            "skipped_epics":  list(self._skipped_epics),
            "news_filter":    self._news_filter,
        }

    # ─────────────────────────────────────────────
    # Internal
    # ─────────────────────────────────────────────

    def _maybe_refresh_queue(self) -> None:
        """
        Rebuild the queue if the session has changed or the queue is empty.
        Also logs a summary of what will be traded this cycle.
        """
        current_session = SessionTracker.get_current_session()

        if current_session == self._queue_session and self._queue:
            return  # Nothing to do

        # Session changed or first run
        prev_session      = self._queue_session
        self._queue_session = current_session
        self._skipped_epics = set()

        if current_session == "OFF":
            self._queue = []
            log.info("SessionProvider: markets closed (OFF session) — queue empty")
            return

        instruments = self._universe.get_for_session(
            current_session, rank_by_performance=True
        )
        self._queue = list(instruments)

        if prev_session != current_session:
            log.info(
                "SessionProvider: session changed %s → %s — loaded %d instruments: %s",
                prev_session or "INIT",
                current_session,
                len(self._queue),
                [i["epic"] for i in self._queue],
            )
        else:
            log.info(
                "SessionProvider: refreshed queue for %s — %d instruments",
                current_session, len(self._queue),
            )
