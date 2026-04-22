"""
ReflectionEngine — drives the Claude agentic loop for journal reflection sessions.

Two public entry points:
  reflect_on_trade(trade)   — quick post-close review of a single trade
  run_daily_reflection()    — full daily review after market close
  run_weekly_reflection()   — deep weekly retrospective

The engine runs a standard tool_use loop:
  1. Send prompt → Claude
  2. Execute any tool_use blocks via JournalTools
  3. Feed results back → Claude
  4. Repeat until stop_reason == "end_turn"
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import anthropic

from ..journal.trade_journal import TradeJournal
from ..journal.analytics import JournalAnalytics
from ..tools.journal_tools import JournalTools
from ..intelligence.intelligence_tools import IntelligenceTools
from .prompts import (
    SYSTEM_PROMPT,
    build_post_trade_prompt,
    build_daily_prompt,
    build_weekly_prompt,
)

log = logging.getLogger(__name__)

# Model used for reflection — sonnet balances quality and cost well here
REFLECTION_MODEL = "claude-sonnet-4-6"

# Hard cap on tool-call rounds per session to prevent runaway loops
MAX_TOOL_ROUNDS = 12


class ReflectionEngine:
    """
    Manages Claude-powered reflection sessions over the trade journal.

    Args:
        journal:   TradeJournal instance (shared with TradingBot)
        analytics: JournalAnalytics instance
        api_key:   Anthropic API key.  Falls back to ANTHROPIC_API_KEY env var.
    """

    def __init__(
        self,
        journal: TradeJournal,
        analytics: JournalAnalytics,
        api_key: Optional[str] = None,
        intelligence_tools: Optional[IntelligenceTools] = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "Anthropic API key not provided. "
                "Set ANTHROPIC_API_KEY or pass api_key= to ReflectionEngine."
            )
        self.client = anthropic.Anthropic(api_key=resolved_key)
        journal_tools = JournalTools(journal, analytics)

        # Merge journal + intelligence tools into a single tool map/defs list
        self._tool_map  = {**journal_tools.get_tool_map()}
        self._tool_defs = journal_tools.get_tool_definitions()

        if intelligence_tools is not None:
            self._tool_map.update(intelligence_tools.get_tool_map())
            self._tool_defs.extend(intelligence_tools.get_tool_definitions())

    # ─────────────────────────────────────────────
    # Public entry points
    # ─────────────────────────────────────────────

    def reflect_on_trade(self, trade: dict) -> str:
        """
        Quick post-close reflection on a single trade.
        Runs the agentic loop and returns Claude's final text response.
        """
        epic = trade.get("epic", "?")
        pnl  = trade.get("pnl", 0)
        log.info("Reflection: post-trade review — %s  pnl=%.2f", epic, pnl or 0)
        prompt = build_post_trade_prompt(trade)
        return self._run_loop(prompt, session_label=f"post-trade/{epic}")

    def run_daily_reflection(self, date_str: Optional[str] = None) -> str:
        """
        Full daily review.  Call this once per day, ideally after market close.
        """
        date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log.info("Reflection: daily review for %s", date_str)
        prompt = build_daily_prompt(date_str)
        return self._run_loop(prompt, session_label=f"daily/{date_str}")

    def run_weekly_reflection(
        self,
        week_start: Optional[str] = None,
        week_end: Optional[str] = None,
    ) -> str:
        """
        Deep weekly retrospective.  Call on Sunday evening or Monday morning.
        """
        today = datetime.now(timezone.utc).date()
        if not week_end:
            week_end = str(today)
        if not week_start:
            week_start = str(today - timedelta(days=7))
        log.info("Reflection: weekly review %s → %s", week_start, week_end)
        prompt = build_weekly_prompt(week_start, week_end)
        return self._run_loop(prompt, session_label=f"weekly/{week_start}")

    # ─────────────────────────────────────────────
    # Core agentic loop
    # ─────────────────────────────────────────────

    def _run_loop(self, user_prompt: str, session_label: str = "") -> str:
        """
        Standard Anthropic tool_use loop.

        Sends the initial message, then alternates between:
          - executing tool_use blocks and feeding results back
          - receiving the final end_turn text response

        Returns the last text block from Claude.
        """
        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        final_text = ""

        for round_num in range(1, MAX_TOOL_ROUNDS + 1):
            log.debug("Reflection [%s] round %d — sending to Claude", session_label, round_num)

            response = self.client.messages.create(
                model=REFLECTION_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=self._tool_defs,
                messages=messages,
            )

            # Collect text and tool_use blocks from the response
            text_blocks = []
            tool_use_blocks = []
            for block in response.content:
                if block.type == "text":
                    text_blocks.append(block.text)
                elif block.type == "tool_use":
                    tool_use_blocks.append(block)

            if text_blocks:
                final_text = "\n".join(text_blocks)

            # Append Claude's response to message history
            messages.append({"role": "assistant", "content": response.content})

            # If no tool calls or stop reason is end_turn, we're done
            if response.stop_reason == "end_turn" or not tool_use_blocks:
                log.info(
                    "Reflection [%s] complete in %d round(s)", session_label, round_num
                )
                break

            # Execute each tool and collect results
            tool_results = self._execute_tools(tool_use_blocks, session_label)
            messages.append({"role": "user", "content": tool_results})

        else:
            log.warning(
                "Reflection [%s] hit MAX_TOOL_ROUNDS (%d) — forcing stop",
                session_label, MAX_TOOL_ROUNDS,
            )

        return final_text

    def _execute_tools(
        self, tool_use_blocks: list, session_label: str
    ) -> list[dict]:
        """
        Execute a batch of tool_use blocks and return a list of
        tool_result content blocks for the next message.
        """
        results = []
        for block in tool_use_blocks:
            tool_name = block.name
            tool_input = block.input or {}
            log.debug(
                "Reflection [%s] tool_use: %s(%s)",
                session_label, tool_name, json.dumps(tool_input)[:120],
            )

            try:
                callable_fn = self._tool_map.get(tool_name)
                if callable_fn is None:
                    raise KeyError(f"Unknown tool: {tool_name}")
                result = callable_fn(**tool_input)
                content = json.dumps(result, default=str)
                is_error = False
            except Exception as e:
                log.error(
                    "Reflection [%s] tool %s failed: %s", session_label, tool_name, e
                )
                content = json.dumps({"error": str(e)})
                is_error = True

            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
        return results
