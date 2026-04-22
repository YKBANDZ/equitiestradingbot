"""
AgentDecisionEngine — the core Claude agent that replaces rule-based strategies.

For each market the bot presents, Claude:
  1. Reads its context (preferences, learnings, performance)
  2. Checks session, news, and market regime via intelligence tools
  3. Analyses price action via market data tools
  4. Validates the trade through the risk gate
  5. Returns a structured AgentDecision via a dedicated submit_decision tool

The decision is returned as an AgentDecision dataclass, which is then
consumed by TradingBot.process_trade() exactly like the old strategy output.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

import anthropic

from ..journal.trade_journal import TradeJournal
from ..journal.analytics import JournalAnalytics
from ..tools.journal_tools import JournalTools
from ..intelligence.intelligence_tools import IntelligenceTools
from ..intelligence.session import SessionTracker
from .market_tools import MarketDataTools
from .risk_tools import RiskTools
from .preferences import PreferencesManager

if TYPE_CHECKING:
    from ...components.broker.broker import Broker

log = logging.getLogger(__name__)

DECISION_MODEL  = "claude-sonnet-4-6"
MAX_TOOL_ROUNDS = 15


# ─────────────────────────────────────────────────────────────────────────────
# Decision output
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentDecision:
    action:      str            # BUY | SELL | HOLD
    limit:       Optional[float] = None   # absolute price level for take-profit
    stop:        Optional[float] = None   # absolute price level for stop-loss
    size:        Optional[float] = None   # recommended position size
    confidence:  float           = 0.0
    reasoning:   str             = ""
    key_factors: list[str]       = field(default_factory=list)

    @property
    def is_trade(self) -> bool:
        return self.action in ("BUY", "SELL")


# ─────────────────────────────────────────────────────────────────────────────
# System prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt(preferences: PreferencesManager) -> str:
    prefs_block = preferences.render_for_prompt()
    return f"""You are an autonomous AI trading agent operating a live CFD trading account \
via IG Markets. Your job is to analyse each market presented to you and decide whether \
to BUY, SELL, or HOLD.

You have access to tools covering four domains:
  • Market data   — get_market_info, get_prices, get_macd
  • Intelligence  — get_session, get_news_status, get_market_context, get_market_regime
  • Journal       — get_context_summary, get_past_trades, get_learnings
  • Risk          — get_balance, get_positions, check_risk, size_position

## Decision process — follow these steps in order

### 1. Context load (do this once per session, not per market)
Call `get_context_summary` to load your performance history, active learnings, \
and recent trades. This is your experience.

### 2. Situational awareness
Call `get_market_context(epic)` — this returns session, news status, and regime \
in a single call. If safe_to_trade is False, submit HOLD immediately.

### 3. Market analysis
Call `get_market_info(epic)` for the current spread and levels.
Call `get_prices(epic, interval='HOUR', bars=50)` to read recent price action.
Call `get_macd(epic)` to confirm momentum direction.

### 4. Risk gate
Call `check_risk(epic, direction, stop_distance)` — if approved=False, submit HOLD.
Call `size_position(epic, stop_distance)` to get the recommended size.

### 5. Decision
Submit your decision via the `submit_decision` tool. Be honest about confidence. \
HOLD is always the correct answer when evidence is weak or conditions are uncertain.

## Core principles
- **Evidence first**: every BUY/SELL must be backed by regime + momentum agreement
- **News discipline**: never trade within the news buffer window (check your preferences)
- **Risk discipline**: never submit BUY/SELL if check_risk returns approved=False
- **Honest confidence**: 0.5 = uncertain, 0.7 = reasonable, 0.9 = strong conviction
- **HOLD freely**: missing a trade costs nothing; a bad trade costs real money

{prefs_block}
"""


# ─────────────────────────────────────────────────────────────────────────────
# submit_decision tool definition (captures structured output from Claude)
# ─────────────────────────────────────────────────────────────────────────────

_SUBMIT_DECISION_TOOL = {
    "name": "submit_decision",
    "description": (
        "Submit your final trading decision for this market. "
        "Call this exactly once when you have completed your analysis. "
        "If any risk check failed or conditions are uncertain, set action='HOLD'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["BUY", "SELL", "HOLD"],
                "description": "Your trading decision",
            },
            "limit": {
                "type": "number",
                "description": "Absolute take-profit price level (required for BUY/SELL)",
            },
            "stop": {
                "type": "number",
                "description": "Absolute stop-loss price level (required for BUY/SELL)",
            },
            "size": {
                "type": "number",
                "description": "Position size (use the value from size_position tool)",
            },
            "confidence": {
                "type": "number",
                "description": "Your confidence 0.0–1.0 (0.5=uncertain, 0.9=strong)",
            },
            "reasoning": {
                "type": "string",
                "description": "Concise explanation of why you are taking this action",
            },
            "key_factors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 3–5 factors that drove this decision",
            },
        },
        "required": ["action", "reasoning"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Decision engine
# ─────────────────────────────────────────────────────────────────────────────

class AgentDecisionEngine:
    """
    Runs the Claude agentic loop for each market and returns an AgentDecision.

    Args:
        broker:       Broker instance
        journal:      TradeJournal instance
        analytics:    JournalAnalytics instance
        api_key:      Anthropic API key (falls back to ANTHROPIC_API_KEY env var)
    """

    def __init__(
        self,
        broker: "Broker",
        journal: TradeJournal,
        analytics: JournalAnalytics,
        api_key: Optional[str] = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "Anthropic API key required for AgentDecisionEngine. "
                "Set ANTHROPIC_API_KEY or pass api_key=."
            )
        self.client = anthropic.Anthropic(api_key=resolved_key)

        # Preferences (living rulebook)
        self.preferences = PreferencesManager(journal)

        # Tool groups
        self._journal_tools = JournalTools(journal, analytics)
        self._intel_tools   = IntelligenceTools(broker)
        self._market_tools  = MarketDataTools(broker)
        self._risk_tools    = RiskTools(broker, self.preferences, analytics)

        # Combined tool map and definitions (submit_decision handled separately)
        self._tool_map = {
            **self._journal_tools.get_tool_map(),
            **self._intel_tools.get_tool_map(),
            **self._market_tools.get_tool_map(),
            **self._risk_tools.get_tool_map(),
        }
        self._tool_defs = (
            self._journal_tools.get_tool_definitions()
            + self._intel_tools.get_tool_definitions()
            + self._market_tools.get_tool_definitions()
            + self._risk_tools.get_tool_definitions()
            + [_SUBMIT_DECISION_TOOL]
        )

    # ─────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────

    def decide(
        self,
        epic: str,
        market_name: str = "",
    ) -> AgentDecision:
        """
        Run the full Claude decision loop for a single market.

        Returns an AgentDecision.  Always returns HOLD on error so the bot
        never crashes due to an API failure.
        """
        label = f"decision/{epic}"
        log.info("AgentDecision: starting analysis for %s (%s)", epic, market_name)

        try:
            session = SessionTracker.get_current_session()
            system  = _build_system_prompt(self.preferences)
            user    = self._build_user_prompt(epic, market_name, session)
            decision = self._run_loop(system, user, label)
            log.info(
                "AgentDecision: %s → %s  conf=%.2f  '%s'",
                epic, decision.action, decision.confidence,
                decision.reasoning[:80],
            )
            return decision
        except Exception as e:
            log.error("AgentDecision: loop failed for %s — %s", epic, e)
            return AgentDecision(
                action="HOLD",
                reasoning=f"Decision engine error: {e}",
                confidence=0.0,
            )

    # ─────────────────────────────────────────────
    # Prompt builder
    # ─────────────────────────────────────────────

    @staticmethod
    def _build_user_prompt(epic: str, market_name: str, session: str) -> str:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")
        return (
            f"Analyse **{market_name or epic}** (`{epic}`) and decide whether to trade it.\n\n"
            f"Current time : {now}\n"
            f"Current session: {session}\n\n"
            "Work through the decision process in your system prompt, then call "
            "`submit_decision` with your final answer."
        )

    # ─────────────────────────────────────────────
    # Agentic tool loop
    # ─────────────────────────────────────────────

    def _run_loop(
        self, system: str, user_prompt: str, label: str
    ) -> AgentDecision:
        messages = [{"role": "user", "content": user_prompt}]
        decision: Optional[AgentDecision] = None

        for round_num in range(1, MAX_TOOL_ROUNDS + 1):
            log.debug("AgentDecision [%s] round %d", label, round_num)

            response = self.client.messages.create(
                model=DECISION_MODEL,
                max_tokens=4096,
                system=system,
                tools=self._tool_defs,
                messages=messages,
            )

            tool_use_blocks = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_use_blocks.append(block)

            messages.append({"role": "assistant", "content": response.content})

            if not tool_use_blocks or response.stop_reason == "end_turn":
                break

            # Execute tools; intercept submit_decision
            tool_results = []
            for block in tool_use_blocks:
                if block.name == "submit_decision":
                    decision = self._parse_decision(block.input)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     json.dumps({"status": "decision_received"}),
                    })
                else:
                    result, is_error = self._execute_tool(block, label)
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result,
                        "is_error":    is_error,
                    })

            messages.append({"role": "user", "content": tool_results})

            # Stop as soon as we have a decision
            if decision is not None:
                log.debug("AgentDecision [%s] decision captured in round %d", label, round_num)
                break

        if decision is None:
            log.warning("AgentDecision [%s] — no submit_decision called; defaulting to HOLD", label)
            decision = AgentDecision(
                action="HOLD",
                reasoning="Analysis completed without a clear trade signal.",
                confidence=0.0,
            )

        return decision

    def _execute_tool(self, block, label: str) -> tuple[str, bool]:
        tool_name  = block.name
        tool_input = block.input or {}
        log.debug(
            "AgentDecision [%s] tool: %s(%s)",
            label, tool_name, json.dumps(tool_input)[:100],
        )
        try:
            fn = self._tool_map.get(tool_name)
            if fn is None:
                raise KeyError(f"Unknown tool: {tool_name}")
            result = fn(**tool_input)
            return json.dumps(result, default=str), False
        except Exception as e:
            log.error("AgentDecision tool %s failed: %s", tool_name, e)
            return json.dumps({"error": str(e)}), True

    @staticmethod
    def _parse_decision(inp: dict) -> AgentDecision:
        return AgentDecision(
            action      = inp.get("action", "HOLD").upper(),
            limit       = inp.get("limit"),
            stop        = inp.get("stop"),
            size        = inp.get("size"),
            confidence  = float(inp.get("confidence", 0.5)),
            reasoning   = inp.get("reasoning", ""),
            key_factors = inp.get("key_factors", []),
        )
