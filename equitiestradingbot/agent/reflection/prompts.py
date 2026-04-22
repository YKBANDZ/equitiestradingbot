"""
Prompt templates for the reflection engine.

Two modes:
  POST_TRADE  — runs after every closed position; fast, single-trade focused
  DAILY       — runs once per day (after market close); full pattern analysis
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────────────────
# Shared system prompt
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an autonomous trading journal analyst for a live CFD trading bot \
operating on IG Markets. Your role is to review trade outcomes, extract actionable learnings, \
and continuously refine the bot's trading strategy preferences so it improves over time.

You have access to a set of journal tools that let you read trade history, retrieve existing \
learnings, write new learnings, record reflections, and update strategy preferences.

## Your core responsibilities

1. **Be evidence-based** — every learning must be grounded in specific trade data. Quote \
trade IDs, dates, and statistics. Do not invent patterns that aren't in the data.

2. **Be actionable** — learnings must translate directly into tradeable rules. Prefer \
"Avoid trading Gold during NFP release ±30 min (3/3 losses in sample)" over \
"Be careful around news events".

3. **Update preferences conservatively** — only change strategy_preferences when you have \
≥3 data points supporting a pattern. Use confidence scores honestly (0.5 = uncertain, \
0.9 = strong evidence).

4. **Supersede stale learnings** — if a new learning contradicts an old one with stronger \
evidence, use `add_learning` with a note, then call the appropriate update to mark the \
old one superseded.

5. **Track your own accuracy** — when you write a learning, note whether previous similar \
learnings were borne out. Adjust confidence accordingly.

## Output format
Think step by step. Use the tools to gather data before writing conclusions. Always finish \
by calling `write_reflection` to record your session summary.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Post-trade prompt (fast path — runs after every close)
# ─────────────────────────────────────────────────────────────────────────────

def build_post_trade_prompt(trade: dict) -> str:
    direction = trade.get("direction", "?")
    epic      = trade.get("epic", "?")
    pnl       = trade.get("pnl", 0)
    entry     = trade.get("entry_price", 0)
    exit_p    = trade.get("exit_price", 0)
    session   = trade.get("session", "UNKNOWN")
    regime    = trade.get("market_regime", "UNKNOWN")
    reasoning = trade.get("reasoning", "(none recorded)")
    reflection= trade.get("outcome_reflection", "")
    outcome   = "WIN" if pnl and pnl > 0 else "LOSS" if pnl and pnl < 0 else "BREAKEVEN"

    return f"""A position just closed. Review it against the existing journal and decide \
whether it contains a learning worth recording.

## Trade just closed
- Epic      : {epic}
- Direction : {direction}
- Entry     : {entry}
- Exit      : {exit_p}
- PnL       : {pnl}  ({outcome})
- Session   : {session}
- Regime    : {regime}
- Entry reasoning: {reasoning}
- Initial reflection: {reflection}

## Your task
1. Call `get_learnings` to see what you already know.
2. Call `get_past_trades` (epic="{epic}", limit=10) to see recent history on this instrument.
3. If this trade reveals a pattern not yet captured, call `add_learning`.
4. If it contradicts an existing learning, update confidence accordingly.
5. Finish with `write_reflection` covering today's date range with a 1–3 sentence summary.

Be concise — this is a quick post-trade check, not a full review.
Today (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Daily reflection prompt (deep review)
# ─────────────────────────────────────────────────────────────────────────────

def build_daily_prompt(date_str: str) -> str:
    return f"""Perform the daily trading journal review for {date_str}.

## Your task — work through these steps in order

### Step 1 — Load context
Call `get_context_summary` to load your current performance stats, active learnings, \
strategy preferences, and recent trades.

### Step 2 — Deep performance analysis
Call `get_performance_breakdown` to get per-session, per-instrument, per-strategy, \
and per-regime stats. Call `get_performance` with no filters for the all-time view.

### Step 3 — Pattern identification
Review the data and identify:
- Which sessions are consistently profitable / unprofitable?
- Which instruments are working / not working?
- Are there common conditions in losing trades (news, regime, time of day)?
- Are there common conditions in winning trades worth doubling down on?
- Are any existing learnings now confirmed or invalidated by new data?

### Step 4 — Write learnings
For each new pattern found, call `add_learning` with:
- The correct category (timing / risk / entry / exit / instrument / regime)
- Specific, actionable text
- An honest confidence score
- The relevant trade_ids

### Step 5 — Update strategy preferences (if warranted)
If the evidence supports changing the bot's operating rules, call \
`get_strategy_preferences` first, then call `save_strategy_preferences` with an \
updated copy. Only change what the data justifies.

### Step 6 — Write reflection
Call `write_reflection` with:
- period_start / period_end covering today
- A concise summary (3–5 sentences)
- action_items: a concrete list of changes to implement next session

Today (UTC): {date_str}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Weekly reflection prompt (deeper retrospective)
# ─────────────────────────────────────────────────────────────────────────────

def build_weekly_prompt(week_start: str, week_end: str) -> str:
    return f"""Perform the weekly trading journal review for the week of {week_start} \
to {week_end}.

## Your task

### Step 1 — Load full context
Call `get_context_summary`. Call `get_performance` with since="{week_start}".
Call `get_performance_breakdown`. Call `get_recent_reflections` (limit=7) to review \
this week's daily reflections.

### Step 2 — Weekly pattern analysis
Identify:
- Net PnL for the week and whether it improved vs the previous week.
- Which day of the week performed best / worst.
- Whether this week's trades validated or contradicted last week's learnings.
- Any emerging multi-day patterns (e.g. Monday gaps, Friday reversals).

### Step 3 — Preference update
Call `get_strategy_preferences`. Decide if any weekly-level rules should be added \
(e.g. "Reduce position size on Mondays"). Update preferences if justified by ≥2 weeks \
of data.

### Step 4 — Write weekly reflection
Call `write_reflection` with:
- period_start="{week_start}", period_end="{week_end}"
- A summary covering overall week performance, key lessons, and what to focus on next week.
- action_items as a prioritised list.

Week: {week_start} → {week_end}
"""
