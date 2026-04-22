"""
PreferencesManager — owns the agent's living strategy rulebook.

The rulebook starts with sensible defaults and evolves over time as Claude
calls save_strategy_preferences() from the reflection/decision loops.

Key responsibilities:
  - Load active preferences from the journal DB (or seed defaults if none exist)
  - Validate incoming preference updates against the schema
  - Render preferences as a human-readable block for system prompt injection
  - Provide typed accessors so other components can query rules without
    parsing raw JSON
"""

from __future__ import annotations

import json
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..journal.trade_journal import TradeJournal

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Default rulebook — Claude starts here before it has performance data
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_PREFERENCES: dict = {
    # ── Risk management ────────────────────────────────────────────────────
    "max_risk_per_trade_pct": 1.0,      # % of account balance risked per trade
    "max_daily_loss_pct":     3.0,      # halt trading if daily drawdown hits this
    "max_open_positions":     1,         # number of concurrent trades
    "min_risk_reward_ratio":  1.5,      # minimum R:R before taking a trade
    "default_stop_atr_mult":  1.5,      # stop = entry ± (ATR × multiplier)
    "default_limit_atr_mult": 2.5,      # limit = entry ± (ATR × multiplier)

    # ── Session rules ──────────────────────────────────────────────────────
    "preferred_sessions":     ["LONDON", "OVERLAP", "NY"],
    "avoid_sessions":         ["OFF"],
    "session_size_scale": {             # multiply default size by this per session
        "ASIAN":   0.75,                # smaller — lower liquidity
        "LONDON":  1.0,
        "OVERLAP": 1.25,                # best liquidity window
        "NY":      1.0,
    },

    # ── News rules ─────────────────────────────────────────────────────────
    "avoid_news_buffer_minutes": 30,    # do not trade ±N min around high-impact news
    "reduce_size_on_news":       True,  # halve size when news < 60 min away

    # ── Entry conditions ───────────────────────────────────────────────────
    "require_regime_confirmation": True,
    "allowed_regimes":  ["TRENDING_UP", "TRENDING_DOWN"],
    "trade_ranging":    False,          # only change to True after evidence it works
    "avoid_volatile":   True,           # skip VOLATILE regime

    # ── Instrument rules ───────────────────────────────────────────────────
    "instrument_overrides": {
        # Per-epic overrides.  Example:
        # "CS.D.USCGC.TODAY.IP": {"max_risk_per_trade_pct": 1.5}
    },

    # ── Exit rules ─────────────────────────────────────────────────────────
    "move_to_breakeven_at_rr": 1.0,     # move stop to B/E once R:R reaches this
    "trailing_stop":           False,   # not implemented yet; set True when ready

    # ── Meta ───────────────────────────────────────────────────────────────
    "notes": (
        "Initial default preferences. "
        "Claude will update these fields based on accumulated trade performance."
    ),
    "version_notes": [],                # list of strings; Claude appends change notes
}

# ─────────────────────────────────────────────────────────────────────────────
# Schema — documents what each key means and its valid type
# Allows validation before saving
# ─────────────────────────────────────────────────────────────────────────────
_SCHEMA: dict[str, type] = {
    "max_risk_per_trade_pct":   float,
    "max_daily_loss_pct":       float,
    "max_open_positions":       int,
    "min_risk_reward_ratio":    float,
    "default_stop_atr_mult":    float,
    "default_limit_atr_mult":   float,
    "preferred_sessions":       list,
    "avoid_sessions":           list,
    "session_size_scale":       dict,
    "avoid_news_buffer_minutes":int,
    "reduce_size_on_news":      bool,
    "require_regime_confirmation": bool,
    "allowed_regimes":          list,
    "trade_ranging":            bool,
    "avoid_volatile":           bool,
    "instrument_overrides":     dict,
    "move_to_breakeven_at_rr":  float,
    "trailing_stop":            bool,
    "notes":                    str,
    "version_notes":            list,
}


class PreferencesManager:
    """
    Manages the agent's living strategy preferences.

    Args:
        journal: TradeJournal instance — preferences are persisted in the DB.
    """

    def __init__(self, journal: "TradeJournal") -> None:
        self._journal = journal
        self._prefs: dict = {}
        self._load()

    # ─────────────────────────────────────────────
    # Public accessors
    # ─────────────────────────────────────────────

    def get(self, key: str, default=None):
        """Return a preference value by key."""
        return self._prefs.get(key, DEFAULT_PREFERENCES.get(key, default))

    def get_all(self) -> dict:
        """Return a merged copy: defaults ← db prefs (db wins)."""
        merged = {**DEFAULT_PREFERENCES, **self._prefs}
        return merged

    def reload(self) -> None:
        """Reload from the journal DB (call after reflection engine updates prefs)."""
        self._load()

    def save(self, updates: dict) -> dict:
        """
        Merge `updates` into the current preferences and persist to DB.
        Validates types and logs what changed.
        Returns the saved preferences.
        """
        current = self.get_all()
        validated = self._validate(updates)
        merged = {**current, **validated}
        saved = self._journal.save_preferences(merged)
        self._prefs = merged
        log.info("Preferences: saved %d key(s): %s", len(validated), list(validated))
        return saved

    # ─────────────────────────────────────────────
    # Typed convenience helpers (used by risk_tools and decision_engine)
    # ─────────────────────────────────────────────

    @property
    def max_risk_per_trade_pct(self) -> float:
        return float(self.get("max_risk_per_trade_pct", 1.0))

    @property
    def max_daily_loss_pct(self) -> float:
        return float(self.get("max_daily_loss_pct", 3.0))

    @property
    def max_open_positions(self) -> int:
        return int(self.get("max_open_positions", 1))

    @property
    def min_risk_reward_ratio(self) -> float:
        return float(self.get("min_risk_reward_ratio", 1.5))

    @property
    def allowed_regimes(self) -> list[str]:
        return self.get("allowed_regimes", ["TRENDING_UP", "TRENDING_DOWN"])

    @property
    def require_regime_confirmation(self) -> bool:
        return bool(self.get("require_regime_confirmation", True))

    @property
    def avoid_volatile(self) -> bool:
        return bool(self.get("avoid_volatile", True))

    @property
    def avoid_news_buffer_minutes(self) -> int:
        return int(self.get("avoid_news_buffer_minutes", 30))

    @property
    def preferred_sessions(self) -> list[str]:
        return self.get("preferred_sessions", ["LONDON", "OVERLAP", "NY"])

    @property
    def default_stop_atr_mult(self) -> float:
        return float(self.get("default_stop_atr_mult", 1.5))

    @property
    def default_limit_atr_mult(self) -> float:
        return float(self.get("default_limit_atr_mult", 2.5))

    def session_size_scale(self, session: str) -> float:
        scales = self.get("session_size_scale", {})
        return float(scales.get(session, 1.0))

    def instrument_override(self, epic: str, key: str, default=None):
        overrides = self.get("instrument_overrides", {})
        return overrides.get(epic, {}).get(key, default)

    # ─────────────────────────────────────────────
    # Prompt rendering
    # ─────────────────────────────────────────────

    def render_for_prompt(self) -> str:
        """
        Return a concise, readable text block for injection into the
        Claude system prompt.
        """
        p = self.get_all()
        lines = [
            "## Your current strategy preferences",
            "",
            "### Risk management",
            f"- Max risk per trade : {p['max_risk_per_trade_pct']}% of account balance",
            f"- Max daily loss     : {p['max_daily_loss_pct']}% (halt trading if breached)",
            f"- Max open positions : {p['max_open_positions']}",
            f"- Min R:R ratio      : {p['min_risk_reward_ratio']}:1",
            f"- Stop distance      : ATR × {p['default_stop_atr_mult']}",
            f"- Limit distance     : ATR × {p['default_limit_atr_mult']}",
            "",
            "### Session rules",
            f"- Preferred sessions : {', '.join(p['preferred_sessions'])}",
            f"- Avoid sessions     : {', '.join(p['avoid_sessions'])}",
            "- Session size scale : "
            + ", ".join(f"{s}={v}×" for s, v in p.get("session_size_scale", {}).items()),
            "",
            "### News rules",
            f"- Avoid ±{p['avoid_news_buffer_minutes']} min around high-impact news",
            f"- Reduce size when news < 60 min away: {p['reduce_size_on_news']}",
            "",
            "### Entry conditions",
            f"- Require regime confirmation : {p['require_regime_confirmation']}",
            f"- Allowed regimes            : {', '.join(p['allowed_regimes'])}",
            f"- Trade ranging markets      : {p['trade_ranging']}",
            f"- Avoid VOLATILE regime      : {p['avoid_volatile']}",
            "",
            "### Notes",
            p.get("notes", ""),
        ]
        if p.get("version_notes"):
            lines += ["", "### Recent preference changes"]
            lines += [f"- {n}" for n in p["version_notes"][-5:]]
        return "\n".join(lines)

    # ─────────────────────────────────────────────
    # Internal
    # ─────────────────────────────────────────────

    def _load(self) -> None:
        try:
            row = self._journal.get_active_preferences()
            if row and row.get("preferences"):
                prefs = row["preferences"]
                if isinstance(prefs, str):
                    prefs = json.loads(prefs)
                self._prefs = prefs
                log.info("Preferences: loaded v%s from journal DB", row.get("version", "?"))
            else:
                # First run — seed with defaults
                self._prefs = {}
                self._journal.save_preferences(DEFAULT_PREFERENCES)
                self._prefs = DEFAULT_PREFERENCES.copy()
                log.info("Preferences: seeded defaults into journal DB")
        except Exception as e:
            log.warning("Preferences: load failed (%s) — using defaults in memory", e)
            self._prefs = DEFAULT_PREFERENCES.copy()

    def _validate(self, updates: dict) -> dict:
        """Validate types; skip keys with wrong type and log a warning."""
        validated = {}
        for key, value in updates.items():
            expected = _SCHEMA.get(key)
            if expected is None:
                # Allow unknown keys (Claude may add new ones)
                validated[key] = value
                continue
            if not isinstance(value, expected):
                try:
                    value = expected(value)
                except (TypeError, ValueError):
                    log.warning(
                        "Preferences: skipping '%s' — expected %s got %s",
                        key, expected.__name__, type(value).__name__,
                    )
                    continue
            validated[key] = value
        return validated
