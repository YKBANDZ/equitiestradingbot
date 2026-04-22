"""
Performance analytics built on top of the trade journal DB.

All query methods return plain dicts / lists so they slot cleanly into
Claude tool responses without any extra serialisation.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .db import get_connection

log = logging.getLogger(__name__)


class JournalAnalytics:
    """
    Read-only analytics layer over the trades table.
    Instantiate once and call query methods as needed.
    """

    # ─────────────────────────────────────────────
    # Overall performance
    # ─────────────────────────────────────────────

    def get_performance_summary(
        self,
        since: Optional[str] = None,
        epic: Optional[str] = None,
    ) -> dict:
        """
        Returns win rate, total PnL, avg PnL, best/worst trades.

        Args:
            since: ISO-8601 date string (inclusive lower bound on exit_time)
            epic:  filter to a single instrument
        """
        filters, params = ["status='CLOSED'"], []
        if since:
            filters.append("exit_time >= ?"); params.append(since)
        if epic:
            filters.append("epic = ?"); params.append(epic)
        where = " AND ".join(filters)

        with get_connection() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*)                                AS total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                    SUM(CASE WHEN pnl = 0 THEN 1 ELSE 0 END) AS breakevens,
                    ROUND(SUM(pnl), 2)                     AS total_pnl,
                    ROUND(AVG(pnl), 2)                     AS avg_pnl,
                    ROUND(MAX(pnl), 2)                     AS best_trade_pnl,
                    ROUND(MIN(pnl), 2)                     AS worst_trade_pnl,
                    ROUND(AVG(pnl_pct), 4)                 AS avg_pnl_pct
                FROM trades WHERE {where}
                """,
                params,
            ).fetchone()

        result = dict(row)
        total = result["total_trades"] or 0
        wins  = result["wins"] or 0
        result["win_rate"] = round(wins / total, 4) if total else 0.0
        return result

    def get_drawdown(self, since: Optional[str] = None) -> dict:
        """
        Calculates max drawdown from the cumulative PnL equity curve.
        Returns max_drawdown (absolute) and max_drawdown_pct.
        """
        filters, params = ["status='CLOSED'", "pnl IS NOT NULL"], []
        if since:
            filters.append("exit_time >= ?"); params.append(since)
        where = " AND ".join(filters)

        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT pnl FROM trades WHERE {where} ORDER BY exit_time ASC",
                params,
            ).fetchall()

        if not rows:
            return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0, "equity_curve": []}

        equity, peak, max_dd = 0.0, 0.0, 0.0
        curve = []
        for r in rows:
            equity += r["pnl"]
            curve.append(round(equity, 2))
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        max_dd_pct = round(max_dd / peak, 4) if peak > 0 else 0.0
        return {
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": max_dd_pct,
            "equity_curve": curve,
        }

    # ─────────────────────────────────────────────
    # Pattern analysis
    # ─────────────────────────────────────────────

    def get_performance_by_session(self) -> list[dict]:
        """Win rate and avg PnL broken down by trading session."""
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(session, 'UNKNOWN')             AS session,
                    COUNT(*)                                 AS total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    ROUND(SUM(pnl), 2)                       AS total_pnl,
                    ROUND(AVG(pnl), 2)                       AS avg_pnl
                FROM trades
                WHERE status='CLOSED'
                GROUP BY session
                ORDER BY total_pnl DESC
                """
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["win_rate"] = round(d["wins"] / d["total"], 4) if d["total"] else 0.0
            results.append(d)
        return results

    def get_performance_by_instrument(self) -> list[dict]:
        """Win rate and avg PnL per epic."""
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    epic,
                    COALESCE(market_name, epic) AS name,
                    COUNT(*)                                 AS total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    ROUND(SUM(pnl), 2)                       AS total_pnl,
                    ROUND(AVG(pnl), 2)                       AS avg_pnl
                FROM trades
                WHERE status='CLOSED'
                GROUP BY epic
                ORDER BY total_pnl DESC
                """
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["win_rate"] = round(d["wins"] / d["total"], 4) if d["total"] else 0.0
            results.append(d)
        return results

    def get_performance_by_strategy(self) -> list[dict]:
        """Win rate and avg PnL per strategy."""
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(strategy_used, 'UNKNOWN')       AS strategy,
                    COUNT(*)                                 AS total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    ROUND(SUM(pnl), 2)                       AS total_pnl,
                    ROUND(AVG(pnl), 2)                       AS avg_pnl
                FROM trades
                WHERE status='CLOSED'
                GROUP BY strategy_used
                ORDER BY total_pnl DESC
                """
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["win_rate"] = round(d["wins"] / d["total"], 4) if d["total"] else 0.0
            results.append(d)
        return results

    def get_performance_by_regime(self) -> list[dict]:
        """Win rate per market regime (TRENDING_UP, RANGING, etc.)."""
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(market_regime, 'UNKNOWN')       AS regime,
                    COUNT(*)                                 AS total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl), 2)                       AS avg_pnl
                FROM trades
                WHERE status='CLOSED'
                GROUP BY market_regime
                ORDER BY avg_pnl DESC
                """
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["win_rate"] = round(d["wins"] / d["total"], 4) if d["total"] else 0.0
            results.append(d)
        return results

    def get_news_impact(self) -> dict:
        """
        Compares win rate and avg PnL when high-impact news was active vs not.
        """
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    news_active,
                    COUNT(*)                                  AS total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl), 2)                        AS avg_pnl
                FROM trades
                WHERE status='CLOSED'
                GROUP BY news_active
                """
            ).fetchall()
        result = {}
        for r in rows:
            key = "news_active" if r["news_active"] else "no_news"
            d = dict(r)
            d["win_rate"] = round(d["wins"] / d["total"], 4) if d["total"] else 0.0
            result[key] = d
        return result

    # ─────────────────────────────────────────────
    # Context for Claude's system prompt
    # ─────────────────────────────────────────────

    def build_context_summary(self, recent_n: int = 20) -> dict:
        """
        Returns a compact summary dict intended to be injected into
        Claude's system prompt before each decision cycle.

        Covers:
        - Overall stats (last 30 days implied via recent_n)
        - Per-session breakdown
        - Per-instrument breakdown
        - Active learnings
        - Latest strategy preferences
        """
        summary = self.get_performance_summary()
        drawdown = self.get_drawdown()
        by_session = self.get_performance_by_session()
        by_instrument = self.get_performance_by_instrument()
        by_strategy = self.get_performance_by_strategy()
        by_regime = self.get_performance_by_regime()
        news_impact = self.get_news_impact()

        # active learnings
        with get_connection() as conn:
            learnings = conn.execute(
                """
                SELECT category, learning_text, confidence
                FROM learnings
                WHERE superseded_by IS NULL
                ORDER BY confidence DESC
                LIMIT 20
                """
            ).fetchall()

        # latest preferences
        with get_connection() as conn:
            pref_row = conn.execute(
                "SELECT preferences FROM strategy_preferences WHERE is_active=1 ORDER BY version DESC LIMIT 1"
            ).fetchone()
        preferences = json.loads(pref_row["preferences"]) if pref_row else {}

        # recent trades (lightweight)
        with get_connection() as conn:
            recent = conn.execute(
                f"""
                SELECT epic, direction, entry_price, exit_price, pnl, session,
                       market_regime, strategy_used, entry_time, exit_time
                FROM trades WHERE status='CLOSED'
                ORDER BY exit_time DESC LIMIT {recent_n}
                """
            ).fetchall()

        return {
            "performance_summary": summary,
            "drawdown": {
                "max_drawdown": drawdown["max_drawdown"],
                "max_drawdown_pct": drawdown["max_drawdown_pct"],
            },
            "by_session": by_session,
            "by_instrument": by_instrument,
            "by_strategy": by_strategy,
            "by_regime": by_regime,
            "news_impact": news_impact,
            "active_learnings": [dict(r) for r in learnings],
            "strategy_preferences": preferences,
            "recent_trades": [dict(r) for r in recent],
        }
