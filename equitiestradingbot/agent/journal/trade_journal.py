"""
Core CRUD operations for the trade journal.

Usage:
    journal = TradeJournal()          # uses default DB path
    journal = TradeJournal(path)      # custom path

All public methods return plain dicts (or lists of dicts) so they
are trivially serialisable for Claude tool responses.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .db import get_connection, initialise_db, set_db_path

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


class TradeJournal:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path:
            set_db_path(db_path)
        initialise_db()

    # ─────────────────────────────────────────────
    # TRADES
    # ─────────────────────────────────────────────

    def log_trade(
        self,
        deal_id: str,
        epic: str,
        direction: str,
        entry_price: float,
        size: float,
        *,
        market_name: str = "",
        limit_level: Optional[float] = None,
        stop_level: Optional[float] = None,
        strategy_used: str = "",
        market_regime: str = "",
        session: str = "",
        volatility_at_entry: Optional[float] = None,
        news_active: bool = False,
        reasoning: str = "",
        entry_time: Optional[str] = None,
    ) -> dict:
        """
        Record a newly opened trade.  Returns the inserted row as a dict.
        """
        entry_time = entry_time or _now_iso()
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (
                    deal_id, epic, market_name, direction, entry_price, entry_time,
                    size, limit_level, stop_level, strategy_used, market_regime,
                    session, volatility_at_entry, news_active, reasoning
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    deal_id, epic, market_name, direction.upper(), entry_price,
                    entry_time, size, limit_level, stop_level, strategy_used,
                    market_regime.upper() if market_regime else None,
                    session.upper() if session else None,
                    volatility_at_entry, int(news_active), reasoning,
                ),
            )
            row_id = cursor.lastrowid
        log.info("Journal: logged trade %s (id=%s)", deal_id, row_id)
        return self.get_trade_by_id(row_id)

    def log_outcome(
        self,
        deal_id: str,
        exit_price: float,
        pnl: float,
        *,
        exit_time: Optional[str] = None,
        pnl_pct: Optional[float] = None,
        outcome_reflection: str = "",
    ) -> dict:
        """
        Update a trade record when the position closes.
        """
        exit_time = exit_time or _now_iso()
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE trades
                SET exit_price = ?, exit_time = ?, pnl = ?, pnl_pct = ?,
                    status = 'CLOSED', outcome_reflection = ?
                WHERE deal_id = ?
                """,
                (exit_price, exit_time, pnl, pnl_pct, outcome_reflection, deal_id),
            )
        log.info("Journal: outcome logged for deal %s  pnl=%.2f", deal_id, pnl)
        return self.get_trade_by_deal(deal_id)

    def cancel_trade(self, deal_id: str) -> dict:
        with get_connection() as conn:
            conn.execute(
                "UPDATE trades SET status='CANCELLED' WHERE deal_id=?", (deal_id,)
            )
        return self.get_trade_by_deal(deal_id)

    def get_trade_by_id(self, trade_id: int) -> dict:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id=?", (trade_id,)
            ).fetchone()
        return _row_to_dict(row)

    def get_trade_by_deal(self, deal_id: str) -> dict:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE deal_id=?", (deal_id,)
            ).fetchone()
        return _row_to_dict(row)

    def get_open_trades(self) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status='OPEN' ORDER BY entry_time DESC"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """Return the most recent closed trades, newest first."""
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM trades
                WHERE status='CLOSED'
                ORDER BY exit_time DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_trades_by_epic(self, epic: str, limit: int = 20) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM trades WHERE epic=? AND status='CLOSED'
                ORDER BY exit_time DESC LIMIT ?
                """,
                (epic, limit),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ─────────────────────────────────────────────
    # LEARNINGS
    # ─────────────────────────────────────────────

    def add_learning(
        self,
        category: str,
        learning_text: str,
        *,
        confidence: float = 0.5,
        trade_ids: Optional[list[int]] = None,
    ) -> dict:
        """
        Persist a learning extracted by Claude from trade analysis.
        """
        trade_ids_json = json.dumps(trade_ids) if trade_ids else None
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO learnings (category, learning_text, confidence, trade_ids)
                VALUES (?,?,?,?)
                """,
                (category.lower(), learning_text, confidence, trade_ids_json),
            )
            row_id = cursor.lastrowid
        log.info("Journal: learning #%s added (category=%s)", row_id, category)
        return self.get_learning_by_id(row_id)

    def update_learning(
        self,
        learning_id: int,
        *,
        learning_text: Optional[str] = None,
        confidence: Optional[float] = None,
        superseded_by: Optional[int] = None,
    ) -> dict:
        fields, values = [], []
        if learning_text is not None:
            fields.append("learning_text=?"); values.append(learning_text)
        if confidence is not None:
            fields.append("confidence=?"); values.append(confidence)
        if superseded_by is not None:
            fields.append("superseded_by=?"); values.append(superseded_by)
        if not fields:
            return self.get_learning_by_id(learning_id)
        fields.append("updated_at=?"); values.append(_now_iso())
        values.append(learning_id)
        with get_connection() as conn:
            conn.execute(
                f"UPDATE learnings SET {', '.join(fields)} WHERE id=?", values
            )
        return self.get_learning_by_id(learning_id)

    def get_learning_by_id(self, learning_id: int) -> dict:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM learnings WHERE id=?", (learning_id,)
            ).fetchone()
        return _row_to_dict(row)

    def get_active_learnings(self, category: Optional[str] = None) -> list[dict]:
        """Return learnings that have not been superseded."""
        with get_connection() as conn:
            if category:
                rows = conn.execute(
                    """
                    SELECT * FROM learnings
                    WHERE superseded_by IS NULL AND category=?
                    ORDER BY confidence DESC, updated_at DESC
                    """,
                    (category.lower(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM learnings
                    WHERE superseded_by IS NULL
                    ORDER BY confidence DESC, updated_at DESC
                    """
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ─────────────────────────────────────────────
    # REFLECTIONS
    # ─────────────────────────────────────────────

    def write_reflection(
        self,
        period_start: str,
        period_end: str,
        summary: str,
        *,
        trades_reviewed: int = 0,
        win_rate: Optional[float] = None,
        total_pnl: Optional[float] = None,
        action_items: Optional[list[str]] = None,
    ) -> dict:
        """
        Save a reflection session written by Claude.
        """
        action_items_json = json.dumps(action_items) if action_items else None
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reflections
                    (period_start, period_end, trades_reviewed, win_rate,
                     total_pnl, summary, action_items)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    period_start, period_end, trades_reviewed,
                    win_rate, total_pnl, summary, action_items_json,
                ),
            )
            row_id = cursor.lastrowid
        log.info("Journal: reflection #%s written (%s → %s)", row_id, period_start, period_end)
        return self.get_reflection_by_id(row_id)

    def get_reflection_by_id(self, reflection_id: int) -> dict:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM reflections WHERE id=?", (reflection_id,)
            ).fetchone()
        return _row_to_dict(row)

    def get_recent_reflections(self, limit: int = 5) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM reflections ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ─────────────────────────────────────────────
    # STRATEGY PREFERENCES
    # ─────────────────────────────────────────────

    def get_active_preferences(self) -> dict:
        """Return the current active strategy preferences as a dict."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_preferences WHERE is_active=1 ORDER BY version DESC LIMIT 1"
            ).fetchone()
        if not row:
            return {}
        data = _row_to_dict(row)
        data["preferences"] = json.loads(data["preferences"])
        return data

    def save_preferences(self, preferences: dict) -> dict:
        """
        Write a new version of the strategy preferences.
        Deactivates the previous active row first.
        """
        with get_connection() as conn:
            # get current version number
            row = conn.execute(
                "SELECT version FROM strategy_preferences WHERE is_active=1 ORDER BY version DESC LIMIT 1"
            ).fetchone()
            next_version = (row["version"] + 1) if row else 1
            # deactivate old
            conn.execute(
                "UPDATE strategy_preferences SET is_active=0 WHERE is_active=1"
            )
            cursor = conn.execute(
                """
                INSERT INTO strategy_preferences (version, is_active, preferences)
                VALUES (?,1,?)
                """,
                (next_version, json.dumps(preferences)),
            )
            row_id = cursor.lastrowid
        log.info("Journal: preferences updated to v%s", next_version)
        return self.get_active_preferences()

    # ─────────────────────────────────────────────
    # PERFORMANCE SNAPSHOTS
    # ─────────────────────────────────────────────

    def save_performance_snapshot(
        self,
        period: str,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        *,
        win_rate: Optional[float] = None,
        total_pnl: Optional[float] = None,
        avg_pnl: Optional[float] = None,
        max_drawdown: Optional[float] = None,
        best_instrument: str = "",
        worst_instrument: str = "",
        notes: str = "",
    ) -> dict:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO performance_snapshots
                    (period, total_trades, winning_trades, losing_trades,
                     win_rate, total_pnl, avg_pnl, max_drawdown,
                     best_instrument, worst_instrument, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    period.upper(), total_trades, winning_trades, losing_trades,
                    win_rate, total_pnl, avg_pnl, max_drawdown,
                    best_instrument, worst_instrument, notes,
                ),
            )
            row_id = cursor.lastrowid
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM performance_snapshots WHERE id=?", (row_id,)
            ).fetchone()
        return _row_to_dict(row)

    def get_latest_snapshot(self, period: str = "DAILY") -> dict:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM performance_snapshots
                WHERE period=? ORDER BY snapshot_time DESC LIMIT 1
                """,
                (period.upper(),),
            ).fetchone()
        return _row_to_dict(row)
