"""
Market regime detection — classifies price data into one of four regimes:

  TRENDING_UP    : Price above 200 EMA, 50 EMA > 200 EMA, ADX > 25
  TRENDING_DOWN  : Price below 200 EMA, 50 EMA < 200 EMA, ADX > 25
  RANGING        : EMAs close together, ADX < 20, price oscillating
  VOLATILE       : ATR significantly above its own rolling average

All calculations use the OHLCV DataFrame returned by broker.get_prices()
(columns: open, high, low, close, volume — indexed by date).
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Tuning constants
EMA_FAST   = 50
EMA_SLOW   = 200
ADX_PERIOD = 14
ATR_PERIOD = 14
VOLATILITY_LOOKBACK = 20   # bars used for rolling ATR mean
VOLATILE_MULTIPLIER = 1.5  # ATR > 1.5× rolling mean → VOLATILE
ADX_TREND_THRESHOLD = 25
ADX_RANGE_THRESHOLD = 20


class RegimeDetector:
    """Stateless — pass the DataFrame returned by broker.get_prices()."""

    # ─────────────────────────────────────────────
    # Public
    # ─────────────────────────────────────────────

    @staticmethod
    def detect(df: pd.DataFrame) -> str:
        """
        Return one of: TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE

        Requires at least 200 bars for reliable EMA computation.
        Falls back to RANGING if insufficient data.
        """
        if df is None or len(df) < ADX_PERIOD + 5:
            return "RANGING"

        close = df["close"]

        # ── Volatility check (highest priority) ──────────────────────────────
        atr   = RegimeDetector._atr(df, ATR_PERIOD)
        if len(atr.dropna()) >= VOLATILITY_LOOKBACK:
            rolling_mean = atr.rolling(VOLATILITY_LOOKBACK).mean()
            if atr.iloc[-1] > rolling_mean.iloc[-1] * VOLATILE_MULTIPLIER:
                return "VOLATILE"

        # ── Trend check ──────────────────────────────────────────────────────
        if len(close) >= EMA_SLOW:
            ema_fast = close.ewm(span=EMA_FAST, adjust=False).mean()
            ema_slow = close.ewm(span=EMA_SLOW, adjust=False).mean()
            adx      = RegimeDetector._adx(df, ADX_PERIOD)

            current_close    = close.iloc[-1]
            current_ema_fast = ema_fast.iloc[-1]
            current_ema_slow = ema_slow.iloc[-1]
            current_adx      = adx.iloc[-1] if len(adx.dropna()) > 0 else 0

            if current_adx > ADX_TREND_THRESHOLD:
                if current_ema_fast > current_ema_slow and current_close > current_ema_slow:
                    return "TRENDING_UP"
                if current_ema_fast < current_ema_slow and current_close < current_ema_slow:
                    return "TRENDING_DOWN"

        return "RANGING"

    @staticmethod
    def get_full_analysis(df: pd.DataFrame) -> dict:
        """
        Return a rich dict with all indicators for the agent's context.
        Useful when the agent wants to understand the detailed market state.
        """
        if df is None or len(df) < 5:
            return {"regime": "UNKNOWN", "error": "Insufficient data"}

        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        result: dict = {}

        # Regime
        result["regime"] = RegimeDetector.detect(df)

        # EMAs
        if len(close) >= EMA_SLOW:
            ema_fast = close.ewm(span=EMA_FAST, adjust=False).mean()
            ema_slow = close.ewm(span=EMA_SLOW, adjust=False).mean()
            result["ema_50"]           = round(float(ema_fast.iloc[-1]), 4)
            result["ema_200"]          = round(float(ema_slow.iloc[-1]), 4)
            result["ema_50_above_200"] = bool(ema_fast.iloc[-1] > ema_slow.iloc[-1])
            # EMA slope over last 5 bars (positive = rising)
            result["ema_50_slope"]  = round(float(ema_fast.iloc[-1] - ema_fast.iloc[-5]), 4)
            result["ema_200_slope"] = round(float(ema_slow.iloc[-1] - ema_slow.iloc[-5]), 4)
        else:
            result["ema_50"]  = None
            result["ema_200"] = None

        # ATR + volatility
        atr = RegimeDetector._atr(df, ATR_PERIOD)
        if len(atr.dropna()) > 0:
            current_atr = float(atr.iloc[-1])
            result["atr_14"] = round(current_atr, 4)
            if len(atr.dropna()) >= VOLATILITY_LOOKBACK:
                rolling_mean = float(atr.rolling(VOLATILITY_LOOKBACK).mean().iloc[-1])
                result["atr_vs_avg"] = round(current_atr / rolling_mean, 3) if rolling_mean else None
                result["volatility_elevated"] = current_atr > rolling_mean * VOLATILE_MULTIPLIER
            else:
                result["atr_vs_avg"] = None
                result["volatility_elevated"] = False

        # ADX
        adx = RegimeDetector._adx(df, ADX_PERIOD)
        if len(adx.dropna()) > 0:
            result["adx_14"] = round(float(adx.iloc[-1]), 2)
            result["trending"] = float(adx.iloc[-1]) > ADX_TREND_THRESHOLD

        # RSI (14)
        rsi = RegimeDetector._rsi(close, 14)
        if len(rsi.dropna()) > 0:
            result["rsi_14"] = round(float(rsi.iloc[-1]), 2)

        # Recent price context
        result["current_close"] = round(float(close.iloc[-1]), 4)
        result["daily_high"]    = round(float(high.iloc[-1]), 4)
        result["daily_low"]     = round(float(low.iloc[-1]), 4)
        result["bars_available"] = len(df)

        # Distance from 200 EMA as % (useful for entry decisions)
        if result.get("ema_200"):
            dist = (result["current_close"] - result["ema_200"]) / result["ema_200"] * 100
            result["pct_from_ema_200"] = round(dist, 3)

        return result

    @staticmethod
    def get_volatility_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
        """Return the current ATR value as a float, or 0.0 if insufficient data."""
        atr = RegimeDetector._atr(df, period)
        if len(atr.dropna()) == 0:
            return 0.0
        return round(float(atr.iloc[-1]), 4)

    # ─────────────────────────────────────────────
    # Indicator helpers
    # ─────────────────────────────────────────────

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _adx(df: pd.DataFrame, period: int) -> pd.Series:
        high  = df["high"]
        low   = df["low"]
        close = df["close"]

        up_move   = high.diff()
        down_move = -low.diff()

        plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move,   0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        atr = RegimeDetector._atr(df, period)

        plus_di  = 100 * pd.Series(plus_dm,  index=df.index).ewm(span=period, adjust=False).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(span=period, adjust=False).mean() / atr

        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.ewm(span=period, adjust=False).mean()
        return adx

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
        rs    = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))
