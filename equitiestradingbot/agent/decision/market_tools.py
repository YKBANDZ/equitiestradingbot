"""
Market data tool wrappers for the Claude decision agent.

Exposes price history and technical indicators as tools Claude can call
during its decision loop, converting broker data structures into clean
JSON-serialisable dicts.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ...components.broker.broker import Broker

log = logging.getLogger(__name__)

# How many recent rows to return in serialised price data
# (full 250-bar DataFrame is too large for a tool response)
_MAX_PRICE_ROWS = 20
_MAX_MACD_ROWS  = 20


class MarketDataTools:
    def __init__(self, broker: "Broker") -> None:
        self._broker = broker

    # ─────────────────────────────────────────────
    # Tool callables
    # ─────────────────────────────────────────────

    def get_market_info(self, epic: str) -> dict:
        """
        Return a snapshot of the current market state: bid, offer, spread,
        daily high/low, and minimum stop distance.
        """
        try:
            market = self._broker.get_market_info(epic)
            spread = round(market.offer - market.bid, 5) if market.offer and market.bid else None
            spread_pct = (
                round(spread / market.bid * 100, 4)
                if spread and market.bid else None
            )
            return {
                "epic":              market.epic,
                "name":              market.name,
                "bid":               market.bid,
                "offer":             market.offer,
                "spread":            spread,
                "spread_pct":        spread_pct,
                "daily_high":        market.high,
                "daily_low":         market.low,
                "stop_distance_min": market.stop_distance_min,
            }
        except Exception as e:
            log.warning("MarketDataTools.get_market_info failed for %s: %s", epic, e)
            return {"error": str(e), "epic": epic}

    def get_prices(
        self,
        epic: str,
        interval: str = "HOUR",
        bars: int = 50,
    ) -> dict:
        """
        Return the most recent OHLCV price bars for an instrument.

        Args:
            epic:     IG epic ID
            interval: MINUTE_5 | MINUTE_15 | MINUTE_30 | HOUR | HOUR_4 | DAY
            bars:     number of bars to fetch (capped internally to avoid huge payloads)

        Returns the last _MAX_PRICE_ROWS candles plus summary statistics.
        """
        try:
            from ...agent.intelligence.intelligence_tools import IntelligenceTools
            iv     = IntelligenceTools._resolve_interval(interval)
            market = self._broker.get_market_info(epic)
            history = self._broker.get_prices(market, iv, bars)
            if history is None or history.dataframe is None:
                return {"error": "No data returned", "epic": epic}

            df   = history.dataframe
            tail = df.tail(_MAX_PRICE_ROWS)

            candles = []
            for ts, row in tail.iterrows():
                candles.append({
                    "time":   str(ts),
                    "open":   round(float(row["open"]),  5),
                    "high":   round(float(row["high"]),  5),
                    "low":    round(float(row["low"]),   5),
                    "close":  round(float(row["close"]), 5),
                    "volume": int(row["volume"]) if "volume" in row else None,
                })

            close = df["close"]
            return {
                "epic":         epic,
                "interval":     interval,
                "bars_fetched": len(df),
                "current_price": round(float(close.iloc[-1]), 5),
                "price_change_pct": round(
                    (float(close.iloc[-1]) - float(close.iloc[0])) / float(close.iloc[0]) * 100, 3
                ),
                "recent_candles": candles,
            }
        except Exception as e:
            log.warning("MarketDataTools.get_prices failed for %s: %s", epic, e)
            return {"error": str(e), "epic": epic}

    def get_macd(
        self,
        epic: str,
        interval: str = "HOUR",
        bars: int = 100,
    ) -> dict:
        """
        Return MACD, Signal, and Histogram values for the instrument.
        Includes the last _MAX_MACD_ROWS data points and a summary of
        the current signal (BULLISH_CROSS, BEARISH_CROSS, ABOVE, BELOW).
        """
        try:
            from ...agent.intelligence.intelligence_tools import IntelligenceTools
            iv     = IntelligenceTools._resolve_interval(interval)
            market = self._broker.get_market_info(epic)
            macd_data = self._broker.get_macd(market, iv, bars)
            if macd_data is None or macd_data.dataframe is None:
                return {"error": "No MACD data", "epic": epic}

            df   = macd_data.dataframe
            tail = df.tail(_MAX_MACD_ROWS)

            rows = []
            for ts, row in tail.iterrows():
                rows.append({
                    "time":   str(ts),
                    "macd":   round(float(row.get("MACD",   0)), 6),
                    "signal": round(float(row.get("Signal", 0)), 6),
                    "hist":   round(float(row.get("Hist",   0)), 6),
                })

            # Summarise current MACD state
            if len(rows) >= 2:
                curr = rows[-1]
                prev = rows[-2]
                if prev["macd"] < prev["signal"] and curr["macd"] >= curr["signal"]:
                    signal_label = "BULLISH_CROSS"
                elif prev["macd"] > prev["signal"] and curr["macd"] <= curr["signal"]:
                    signal_label = "BEARISH_CROSS"
                elif curr["macd"] > curr["signal"]:
                    signal_label = "ABOVE_SIGNAL"
                else:
                    signal_label = "BELOW_SIGNAL"
            else:
                signal_label = "INSUFFICIENT_DATA"

            return {
                "epic":         epic,
                "interval":     interval,
                "signal":       signal_label,
                "current_macd": rows[-1] if rows else None,
                "history":      rows,
            }
        except Exception as e:
            log.warning("MarketDataTools.get_macd failed for %s: %s", epic, e)
            return {"error": str(e), "epic": epic}

    # ─────────────────────────────────────────────
    # Anthropic API tool definitions
    # ─────────────────────────────────────────────

    def get_tool_map(self) -> dict:
        return {
            "get_market_info": self.get_market_info,
            "get_prices":      self.get_prices,
            "get_macd":        self.get_macd,
        }

    def get_tool_definitions(self) -> list[dict]:
        return [
            {
                "name": "get_market_info",
                "description": (
                    "Return current market snapshot: bid, offer, spread, daily high/low, "
                    "and minimum stop distance. Call this first when analysing a new instrument."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "epic": {"type": "string", "description": "IG epic ID"},
                    },
                    "required": ["epic"],
                },
            },
            {
                "name": "get_prices",
                "description": (
                    "Return recent OHLCV price candles for an instrument. "
                    "Use interval='HOUR' bars=50 for intraday decisions. "
                    "Use interval='DAY' bars=30 for swing context."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "epic":     {"type": "string"},
                        "interval": {
                            "type": "string",
                            "enum": ["MINUTE_5", "MINUTE_15", "MINUTE_30",
                                     "HOUR", "HOUR_4", "DAY"],
                            "default": "HOUR",
                        },
                        "bars": {
                            "type": "integer",
                            "default": 50,
                            "description": "Number of bars to fetch",
                        },
                    },
                    "required": ["epic"],
                },
            },
            {
                "name": "get_macd",
                "description": (
                    "Return MACD indicator values and a current signal label "
                    "(BULLISH_CROSS / BEARISH_CROSS / ABOVE_SIGNAL / BELOW_SIGNAL). "
                    "Use to confirm momentum direction before entry."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "epic":     {"type": "string"},
                        "interval": {"type": "string", "default": "HOUR"},
                        "bars":     {"type": "integer", "default": 100},
                    },
                    "required": ["epic"],
                },
            },
        ]
