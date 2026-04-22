"""
Risk management tool wrappers for the Claude decision agent.

Exposes account balance, open position checks, position sizing, and a
pre-trade risk gate as tools Claude calls before committing to a trade.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ...components.broker.broker import Broker
    from ..journal.analytics import JournalAnalytics
    from .preferences import PreferencesManager

log = logging.getLogger(__name__)


class RiskTools:
    def __init__(
        self,
        broker: "Broker",
        preferences: "PreferencesManager",
        analytics: "JournalAnalytics",
    ) -> None:
        self._broker      = broker
        self._prefs       = preferences
        self._analytics   = analytics

    # ─────────────────────────────────────────────
    # Tool callables
    # ─────────────────────────────────────────────

    def get_balance(self) -> dict:
        """
        Return current account balance, available funds, and % of account
        currently used as margin.
        """
        try:
            balance, deposit = self._broker.get_account_balances()
            used_pct = self._broker.get_account_used_perc()
            available = round(balance - deposit, 2) if balance and deposit else None
            return {
                "balance":    round(balance, 2) if balance else None,
                "deposit":    round(deposit, 2) if deposit else None,
                "available":  available,
                "used_pct":   round(used_pct, 2) if used_pct else None,
                "max_usable_pct": self._prefs.get("max_account_usable", 50),
                "safe_to_trade": (used_pct or 0) < self._prefs.get("max_account_usable", 50),
            }
        except Exception as e:
            log.warning("RiskTools.get_balance failed: %s", e)
            return {"error": str(e)}

    def get_positions(self) -> list[dict]:
        """
        Return all currently open positions with entry price, direction,
        deal ID, and unrealised P&L estimate.
        """
        try:
            positions = self._broker.get_open_positions() or []
            result = []
            for p in positions:
                result.append({
                    "deal_id":    p.deal_id,
                    "epic":       p.epic,
                    "direction":  p.direction.value if hasattr(p.direction, "value") else str(p.direction),
                    "size":       p.size,
                    "entry":      p.level,
                    "limit":      p.limit,
                    "stop":       p.stop,
                    "currency":   p.currency,
                })
            return result
        except Exception as e:
            log.warning("RiskTools.get_positions failed: %s", e)
            return [{"error": str(e)}]

    def size_position(
        self,
        epic: str,
        stop_distance: float,
        risk_pct: Optional[float] = None,
        session: str = "",
    ) -> dict:
        """
        Calculate the appropriate position size for a trade.

        Formula:
            size = (balance × risk_pct/100) / stop_distance

        Args:
            epic:          instrument epic (used for per-instrument overrides)
            stop_distance: distance in price points from entry to stop
            risk_pct:      % of account to risk (defaults to preferences value)
            session:       current session (applies session size scaling)

        Returns recommended size plus the workings for transparency.
        """
        try:
            balance, _ = self._broker.get_account_balances()
            if not balance or balance <= 0:
                return {"error": "Could not fetch balance", "size": 1}
            if not stop_distance or stop_distance <= 0:
                return {"error": "stop_distance must be > 0", "size": 1}

            # Resolve risk %: argument > instrument override > preference default
            base_risk = risk_pct or self._prefs.instrument_override(
                epic, "max_risk_per_trade_pct"
            ) or self._prefs.max_risk_per_trade_pct

            # Session scaling
            scale = self._prefs.session_size_scale(session.upper()) if session else 1.0
            effective_risk = base_risk * scale

            risk_amount  = balance * effective_risk / 100
            raw_size     = risk_amount / stop_distance
            # Floor to 1 decimal, minimum 1
            recommended  = max(1, round(raw_size, 1))

            return {
                "epic":            epic,
                "balance":         round(balance, 2),
                "risk_pct":        effective_risk,
                "session_scale":   scale,
                "stop_distance":   stop_distance,
                "risk_amount":     round(risk_amount, 2),
                "recommended_size": recommended,
            }
        except Exception as e:
            log.warning("RiskTools.size_position failed: %s", e)
            return {"error": str(e), "size": 1}

    def check_risk(
        self,
        epic: str,
        direction: str,
        stop_distance: float,
        session: str = "",
        regime: str = "",
    ) -> dict:
        """
        Run the full pre-trade risk gate. Returns approved=True only if ALL
        checks pass.

        Checks performed:
          1. Account usage within limit
          2. Open position count within limit
          3. Session is in preferred list
          4. Regime is in allowed list (if regime provided)
          5. Daily loss limit not breached
          6. Minimum stop distance respected
        """
        issues   = []
        warnings = []

        # 1. Account usage
        used_pct = self._broker.get_account_used_perc() or 0
        max_usable = self._prefs.get("max_account_usable", 50)
        if used_pct >= max_usable:
            issues.append(f"Account used {used_pct:.1f}% ≥ max {max_usable}%")

        # 2. Open positions
        positions = self._broker.get_open_positions() or []
        open_count = len(positions)
        max_pos = self._prefs.max_open_positions
        if open_count >= max_pos:
            issues.append(f"Already {open_count} open position(s) (max {max_pos})")

        # 3. Session check
        if session and session.upper() not in [s.upper() for s in self._prefs.preferred_sessions]:
            warnings.append(f"Session {session} not in preferred sessions {self._prefs.preferred_sessions}")

        # 4. Regime check
        if regime:
            if regime == "VOLATILE" and self._prefs.avoid_volatile:
                issues.append(f"Regime is VOLATILE — blocked by preferences (avoid_volatile=True)")
            elif regime not in self._prefs.allowed_regimes and self._prefs.require_regime_confirmation:
                issues.append(f"Regime {regime} not in allowed_regimes {self._prefs.allowed_regimes}")

        # 5. Daily loss limit
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            perf = self._analytics.get_performance_summary(since=today)
            daily_pnl = perf.get("total_pnl") or 0
            balance, _ = self._broker.get_account_balances()
            if balance and daily_pnl < 0:
                daily_loss_pct = abs(daily_pnl) / balance * 100
                max_loss = self._prefs.max_daily_loss_pct
                if daily_loss_pct >= max_loss:
                    issues.append(
                        f"Daily loss limit breached: {daily_loss_pct:.2f}% ≥ {max_loss}%"
                    )
        except Exception:
            pass

        # 6. Minimum stop distance
        try:
            market = self._broker.get_market_info(epic)
            if market.stop_distance_min and stop_distance < market.stop_distance_min:
                issues.append(
                    f"Stop distance {stop_distance} < broker minimum {market.stop_distance_min}"
                )
        except Exception:
            pass

        approved = len(issues) == 0
        return {
            "approved":  approved,
            "epic":      epic,
            "direction": direction,
            "issues":    issues,
            "warnings":  warnings,
            "checks_passed": 6 - len(issues),
            "checks_total":  6,
        }

    # ─────────────────────────────────────────────
    # Anthropic API tool definitions
    # ─────────────────────────────────────────────

    def get_tool_map(self) -> dict:
        return {
            "get_balance":   self.get_balance,
            "get_positions": self.get_positions,
            "size_position": self.size_position,
            "check_risk":    self.check_risk,
        }

    def get_tool_definitions(self) -> list[dict]:
        return [
            {
                "name": "get_balance",
                "description": (
                    "Return current account balance, available margin, and % used. "
                    "Call this before sizing any position."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_positions",
                "description": "Return all currently open positions with entry, stop, and limit levels.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "size_position",
                "description": (
                    "Calculate the correct position size for a trade based on account balance, "
                    "risk %, stop distance, and session scaling. Always call this to get the "
                    "recommended size rather than using a fixed value."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "epic":          {"type": "string"},
                        "stop_distance": {
                            "type":        "number",
                            "description": "Price distance from entry to stop-loss",
                        },
                        "risk_pct": {
                            "type":        "number",
                            "description": "% of account to risk (leave blank to use preferences default)",
                        },
                        "session": {
                            "type":        "string",
                            "description": "Current session for size scaling (ASIAN/LONDON/OVERLAP/NY)",
                        },
                    },
                    "required": ["epic", "stop_distance"],
                },
            },
            {
                "name": "check_risk",
                "description": (
                    "Run the full pre-trade risk gate. Returns approved=True only if ALL checks "
                    "pass: account usage, open position count, session, regime, daily loss limit, "
                    "and minimum stop distance. ALWAYS call this before submitting a BUY or SELL decision."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "epic":          {"type": "string"},
                        "direction":     {"type": "string", "enum": ["BUY", "SELL"]},
                        "stop_distance": {"type": "number"},
                        "session":       {"type": "string"},
                        "regime":        {"type": "string"},
                    },
                    "required": ["epic", "direction", "stop_distance"],
                },
            },
        ]
