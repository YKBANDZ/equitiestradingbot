"""
InstrumentUniverse — loads and queries the instrument registry from
data/instrument_universe.json.

Provides methods to:
  - Get all enabled instruments
  - Get instruments for the current/specified session
  - Get instruments filtered by asset class
  - Look up a single instrument profile by epic
  - Rank instruments by recent performance (using journal analytics)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ...agent.journal.analytics import JournalAnalytics

log = logging.getLogger(__name__)

DEFAULT_UNIVERSE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "instrument_universe.json"
)

# Valid session names (must match session.py)
VALID_SESSIONS = {"ASIAN", "LONDON", "OVERLAP", "NY", "OFF"}


class InstrumentUniverse:
    """
    Loads the instrument registry and answers queries about it.

    Args:
        path: Path to instrument_universe.json. Defaults to data/instrument_universe.json.
        analytics: Optional JournalAnalytics for performance-weighted ordering.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        analytics: Optional["JournalAnalytics"] = None,
    ) -> None:
        self._path      = Path(path) if path else DEFAULT_UNIVERSE_PATH
        self._analytics = analytics
        self._instruments: list[dict] = []
        self._load()

    # ─────────────────────────────────────────────
    # Public queries
    # ─────────────────────────────────────────────

    def get_all(self, enabled_only: bool = True) -> list[dict]:
        """Return all instruments, optionally filtered to enabled ones."""
        if enabled_only:
            return [i for i in self._instruments if i.get("enabled", True)]
        return list(self._instruments)

    def get_for_session(
        self,
        session: str,
        enabled_only: bool = True,
        rank_by_performance: bool = True,
    ) -> list[dict]:
        """
        Return instruments active during `session`, sorted by priority then
        optionally re-ranked by recent journal performance.

        Returns an empty list for session='OFF'.
        """
        session = session.upper()
        if session == "OFF":
            return []

        candidates = [
            i for i in self.get_all(enabled_only=enabled_only)
            if session in [s.upper() for s in i.get("sessions", [])]
        ]

        # Sort by priority (lower = higher priority) as base order
        candidates.sort(key=lambda x: x.get("priority", 99))

        # Re-rank by recent journal performance if analytics available
        if rank_by_performance and self._analytics:
            candidates = self._rank_by_performance(candidates)

        return candidates

    def get_by_asset_class(
        self,
        asset_class: str,
        session: Optional[str] = None,
        enabled_only: bool = True,
    ) -> list[dict]:
        """
        Return instruments of a specific asset class (forex/index/commodity).
        Optionally filter to a session.
        """
        instruments = (
            self.get_for_session(session, enabled_only=enabled_only)
            if session
            else self.get_all(enabled_only=enabled_only)
        )
        return [i for i in instruments if i.get("asset_class", "").lower() == asset_class.lower()]

    def get_by_epic(self, epic: str) -> Optional[dict]:
        """Return the profile for a specific epic, or None if not found."""
        for inst in self._instruments:
            if inst.get("epic") == epic:
                return inst
        return None

    def get_epics_for_session(self, session: str, enabled_only: bool = True) -> list[str]:
        """Convenience method — just the epic strings for a session."""
        return [i["epic"] for i in self.get_for_session(session, enabled_only=enabled_only)]

    def summary(self) -> dict:
        """Return a compact summary of the universe for logging/debugging."""
        all_inst = self.get_all(enabled_only=False)
        enabled  = [i for i in all_inst if i.get("enabled", True)]
        by_session: dict[str, list[str]] = {}
        for sess in VALID_SESSIONS - {"OFF"}:
            by_session[sess] = self.get_epics_for_session(sess)
        return {
            "total":      len(all_inst),
            "enabled":    len(enabled),
            "by_session": by_session,
            "asset_classes": list({i.get("asset_class", "unknown") for i in enabled}),
        }

    # ─────────────────────────────────────────────
    # Performance ranking
    # ─────────────────────────────────────────────

    def _rank_by_performance(self, instruments: list[dict]) -> list[dict]:
        """
        Re-order instruments by descending average PnL from the journal.
        Instruments with no trade history retain their priority order.
        """
        try:
            breakdown = self._analytics.get_performance_by_instrument()
            perf_map  = {row["epic"]: row.get("avg_pnl", 0.0) for row in breakdown}
            # Instruments with history first, ranked by avg_pnl desc
            # Instruments with no history retain their existing order at the back
            with_perf    = [i for i in instruments if i["epic"] in perf_map]
            without_perf = [i for i in instruments if i["epic"] not in perf_map]
            with_perf.sort(key=lambda x: perf_map.get(x["epic"], 0.0), reverse=True)
            return with_perf + without_perf
        except Exception as e:
            log.debug("InstrumentUniverse: performance ranking skipped — %s", e)
            return instruments

    # ─────────────────────────────────────────────
    # Load
    # ─────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            log.warning(
                "InstrumentUniverse: file not found at %s — universe will be empty", self._path
            )
            self._instruments = []
            return
        with self._path.open("r") as f:
            data = json.load(f)
        self._instruments = data.get("instruments", [])
        enabled = sum(1 for i in self._instruments if i.get("enabled", True))
        log.info(
            "InstrumentUniverse: loaded %d instruments (%d enabled) from %s",
            len(self._instruments), enabled, self._path,
        )

    def reload(self) -> None:
        """Hot-reload the universe file without restarting the bot."""
        self._load()
