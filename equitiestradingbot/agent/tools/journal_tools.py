"""
Tool-callable wrappers for the Claude agent.

Each method maps directly to a tool definition that will be passed to the
Anthropic API as a `tools` list entry.  The method signature IS the tool
interface — keep arguments JSON-serialisable (str, int, float, bool, list,
dict) and return plain dicts.

Usage:
    tools = JournalTools(journal, analytics)
    tool_map = tools.get_tool_map()         # {name: callable}
    tool_defs = tools.get_tool_definitions() # list[dict] for Anthropic API
"""

from __future__ import annotations

from typing import Optional

from ..journal.trade_journal import TradeJournal
from ..journal.analytics import JournalAnalytics


class JournalTools:
    def __init__(self, journal: TradeJournal, analytics: JournalAnalytics) -> None:
        self._j = journal
        self._a = analytics

    # ─────────────────────────────────────────────
    # Tool callables
    # ─────────────────────────────────────────────

    def log_trade(
        self,
        deal_id: str,
        epic: str,
        direction: str,
        entry_price: float,
        size: float,
        market_name: str = "",
        limit_level: Optional[float] = None,
        stop_level: Optional[float] = None,
        strategy_used: str = "",
        market_regime: str = "",
        session: str = "",
        volatility_at_entry: Optional[float] = None,
        news_active: bool = False,
        reasoning: str = "",
    ) -> dict:
        """Record a newly opened trade in the journal."""
        return self._j.log_trade(
            deal_id=deal_id,
            epic=epic,
            direction=direction,
            entry_price=entry_price,
            size=size,
            market_name=market_name,
            limit_level=limit_level,
            stop_level=stop_level,
            strategy_used=strategy_used,
            market_regime=market_regime,
            session=session,
            volatility_at_entry=volatility_at_entry,
            news_active=news_active,
            reasoning=reasoning,
        )

    def log_outcome(
        self,
        deal_id: str,
        exit_price: float,
        pnl: float,
        pnl_pct: Optional[float] = None,
        outcome_reflection: str = "",
    ) -> dict:
        """Record the outcome of a closed position."""
        return self._j.log_outcome(
            deal_id=deal_id,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            outcome_reflection=outcome_reflection,
        )

    def get_past_trades(self, limit: int = 20, epic: str = "") -> list[dict]:
        """Retrieve recent closed trades, optionally filtered by instrument."""
        if epic:
            return self._j.get_trades_by_epic(epic, limit=limit)
        return self._j.get_recent_trades(limit=limit)

    def get_open_trades(self) -> list[dict]:
        """Return all currently open trades in the journal."""
        return self._j.get_open_trades()

    def get_performance(self, since: str = "", epic: str = "") -> dict:
        """
        Return performance summary (win rate, PnL, drawdown).
        since: optional ISO-8601 lower bound, e.g. '2025-01-01'
        epic:  optional instrument filter
        """
        summary = self._a.get_performance_summary(
            since=since or None,
            epic=epic or None,
        )
        drawdown = self._a.get_drawdown(since=since or None)
        summary["max_drawdown"] = drawdown["max_drawdown"]
        summary["max_drawdown_pct"] = drawdown["max_drawdown_pct"]
        return summary

    def get_learnings(self, category: str = "") -> list[dict]:
        """Return active learnings, optionally filtered by category."""
        return self._j.get_active_learnings(category=category or None)

    def add_learning(
        self,
        category: str,
        learning_text: str,
        confidence: float = 0.5,
        trade_ids: Optional[list[int]] = None,
    ) -> dict:
        """
        Persist a new learning extracted from trade analysis.
        category: one of timing | risk | entry | exit | instrument | regime
        confidence: 0.0–1.0 indicating how certain this learning is
        """
        return self._j.add_learning(
            category=category,
            learning_text=learning_text,
            confidence=confidence,
            trade_ids=trade_ids,
        )

    def write_reflection(
        self,
        period_start: str,
        period_end: str,
        summary: str,
        trades_reviewed: int = 0,
        win_rate: Optional[float] = None,
        total_pnl: Optional[float] = None,
        action_items: Optional[list[str]] = None,
    ) -> dict:
        """Save a reflection session after reviewing a batch of trades."""
        return self._j.write_reflection(
            period_start=period_start,
            period_end=period_end,
            summary=summary,
            trades_reviewed=trades_reviewed,
            win_rate=win_rate,
            total_pnl=total_pnl,
            action_items=action_items,
        )

    def get_recent_reflections(self, limit: int = 3) -> list[dict]:
        """Return the most recent reflection sessions."""
        return self._j.get_recent_reflections(limit=limit)

    def get_strategy_preferences(self) -> dict:
        """Return the current active strategy preferences."""
        prefs = self._j.get_active_preferences()
        return prefs.get("preferences", {}) if prefs else {}

    def save_strategy_preferences(self, preferences: dict) -> dict:
        """
        Update the living strategy preferences (Claude's own rulebook).
        Replaces the entire preferences object — include all keys.
        """
        return self._j.save_preferences(preferences)

    def get_context_summary(self) -> dict:
        """
        Return the full context summary for injection into Claude's
        system prompt: performance, learnings, preferences, recent trades.
        """
        return self._a.build_context_summary()

    def get_performance_breakdown(self) -> dict:
        """
        Return per-session, per-instrument, per-strategy, and per-regime
        performance breakdowns plus news impact analysis.
        """
        return {
            "by_session": self._a.get_performance_by_session(),
            "by_instrument": self._a.get_performance_by_instrument(),
            "by_strategy": self._a.get_performance_by_strategy(),
            "by_regime": self._a.get_performance_by_regime(),
            "news_impact": self._a.get_news_impact(),
        }

    # ─────────────────────────────────────────────
    # Anthropic API tool definitions
    # ─────────────────────────────────────────────

    def get_tool_map(self) -> dict:
        """Returns {tool_name: callable} for dispatching tool_use blocks."""
        return {
            "log_trade":                self.log_trade,
            "log_outcome":              self.log_outcome,
            "get_past_trades":          self.get_past_trades,
            "get_open_trades":          self.get_open_trades,
            "get_performance":          self.get_performance,
            "get_learnings":            self.get_learnings,
            "add_learning":             self.add_learning,
            "write_reflection":         self.write_reflection,
            "get_recent_reflections":   self.get_recent_reflections,
            "get_strategy_preferences": self.get_strategy_preferences,
            "save_strategy_preferences":self.save_strategy_preferences,
            "get_context_summary":      self.get_context_summary,
            "get_performance_breakdown":self.get_performance_breakdown,
        }

    def get_tool_definitions(self) -> list[dict]:
        """
        Returns the list of tool dicts to pass as `tools=` to the
        Anthropic API client.
        """
        return [
            {
                "name": "log_trade",
                "description": (
                    "Record a newly opened trade in the persistent journal. "
                    "Call this immediately after a BUY or SELL order is placed."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "deal_id":             {"type": "string",  "description": "IG deal reference"},
                        "epic":                {"type": "string",  "description": "Market epic/symbol"},
                        "direction":           {"type": "string",  "enum": ["BUY", "SELL"]},
                        "entry_price":         {"type": "number"},
                        "size":                {"type": "number",  "description": "Position size in units"},
                        "market_name":         {"type": "string"},
                        "limit_level":         {"type": "number",  "description": "Take-profit level"},
                        "stop_level":          {"type": "number",  "description": "Stop-loss level"},
                        "strategy_used":       {"type": "string"},
                        "market_regime":       {"type": "string",  "enum": ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE", ""]},
                        "session":             {"type": "string",  "enum": ["ASIAN", "LONDON", "NY", "OVERLAP", ""]},
                        "volatility_at_entry": {"type": "number"},
                        "news_active":         {"type": "boolean", "description": "True if high-impact news was active at entry"},
                        "reasoning":           {"type": "string",  "description": "Your reasoning for taking this trade"},
                    },
                    "required": ["deal_id", "epic", "direction", "entry_price", "size"],
                },
            },
            {
                "name": "log_outcome",
                "description": "Record the result of a closed position. Call this when a position is confirmed closed.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "deal_id":             {"type": "string"},
                        "exit_price":          {"type": "number"},
                        "pnl":                 {"type": "number", "description": "Absolute profit/loss in account currency"},
                        "pnl_pct":             {"type": "number", "description": "Percentage return on margin"},
                        "outcome_reflection":  {"type": "string", "description": "Brief reflection on what happened and why"},
                    },
                    "required": ["deal_id", "exit_price", "pnl"],
                },
            },
            {
                "name": "get_past_trades",
                "description": "Retrieve recent closed trades from the journal.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 20},
                        "epic":  {"type": "string",  "description": "Optional instrument filter"},
                    },
                },
            },
            {
                "name": "get_open_trades",
                "description": "Return all currently open trades tracked in the journal.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_performance",
                "description": "Return performance stats: win rate, total PnL, avg PnL, max drawdown.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "since": {"type": "string", "description": "ISO-8601 date lower bound, e.g. '2025-01-01'"},
                        "epic":  {"type": "string", "description": "Optional instrument filter"},
                    },
                },
            },
            {
                "name": "get_learnings",
                "description": "Return the active learnings stored in the journal.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["timing", "risk", "entry", "exit", "instrument", "regime", ""],
                        }
                    },
                },
            },
            {
                "name": "add_learning",
                "description": (
                    "Persist a new learning after analysing trade outcomes. "
                    "Use this to record patterns, mistakes, or observations that should "
                    "influence future trading decisions."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "category":      {"type": "string", "enum": ["timing", "risk", "entry", "exit", "instrument", "regime"]},
                        "learning_text": {"type": "string"},
                        "confidence":    {"type": "number", "description": "0.0–1.0"},
                        "trade_ids":     {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["category", "learning_text"],
                },
            },
            {
                "name": "write_reflection",
                "description": (
                    "Save a reflection session after reviewing a batch of trades. "
                    "Call this at the end of each daily/weekly review."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "period_start":    {"type": "string", "description": "ISO-8601"},
                        "period_end":      {"type": "string", "description": "ISO-8601"},
                        "summary":         {"type": "string"},
                        "trades_reviewed": {"type": "integer"},
                        "win_rate":        {"type": "number"},
                        "total_pnl":       {"type": "number"},
                        "action_items":    {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["period_start", "period_end", "summary"],
                },
            },
            {
                "name": "get_recent_reflections",
                "description": "Return the most recent reflection sessions.",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 3}},
                },
            },
            {
                "name": "get_strategy_preferences",
                "description": "Return the current active strategy preferences (your living rulebook).",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "save_strategy_preferences",
                "description": (
                    "Update your living strategy preferences. This replaces the full "
                    "preferences object — always include all keys you want to keep."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "preferences": {
                            "type": "object",
                            "description": (
                                "Full preferences dict. Suggested keys: "
                                "avoid_trading (list), preferred_sessions (list), "
                                "position_sizing_rules (object), "
                                "instruments_by_session (object), "
                                "max_trades_per_day (int), notes (str)."
                            ),
                        }
                    },
                    "required": ["preferences"],
                },
            },
            {
                "name": "get_context_summary",
                "description": (
                    "Return the full context summary: performance stats, active learnings, "
                    "strategy preferences, and recent trades. Call this at the start of each "
                    "decision cycle to load your experience into context."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_performance_breakdown",
                "description": (
                    "Return performance broken down by session, instrument, strategy, "
                    "market regime, and news impact. Useful for reflection sessions."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
