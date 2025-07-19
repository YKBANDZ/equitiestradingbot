import logging
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import pandas as pd
import numpy as np

from .base import BacktestResult, Strategy, TradeSignal
from ..components.broker.broker import Broker
from ..components.configuration import Configuration
from ..components.utils import Interval, TradeDirection, Utils
from ..interfaces import Market, MarketHistory
from ..components.telegram.telegram_bot import TelegramBot
from ..components.time_provider import TimeProvider


class TradeState(Enum):
    WAITING_FOR_FIRST_CANDLE = "waiting_for_first_candle"
    MONITORING_BREAKOUT = "monitoring_breakout"
    WAITING_FOR_FVG = "waiting_for_fvg"
    IN_TRADE = "in_trade"


@dataclass
class FirstCandleData:
    """Data structure for first candle information"""
    high: float
    low: float
    open: float
    close: float
    volume: int
    timestamp: datetime


class SimplicityStrategy(Strategy):
    """
    Simplicity Strategy (First Candle Rule)
    
    Core Concept: Use the first 30-minute candle (9:30-10:00 AM EST / 14:30-15:00 UK) 
    to establish high/low levels, then trade breakouts on 5-minute chart with 
    fair value gap confirmation.
    """
    
    def __init__(self, config: Configuration, broker: Broker) -> None:
        super().__init__(config, broker)
        self.telegram_bot = TelegramBot(config)
        
        # Strategy state
        self.first_candle_data: Optional[FirstCandleData] = None
        self.trade_state = TradeState.WAITING_FOR_FIRST_CANDLE
        self.strategy_complete = False
        
        logging.info("Simplicity Strategy created")

    def read_configuration(self, config: Configuration) -> None:
        """Read strategy-specific configuration"""
        raw = config.get_raw_config()
        
        if "strategies" in raw and "simplicity" in raw["strategies"]:
            strategy_config = raw["strategies"]["simplicity"]
            
            # Time settings
            self.first_candle_start_time = strategy_config.get("first_candle_start_time", "14:30")
            self.first_candle_end_time = strategy_config.get("first_candle_end_time", "15:00")
            
            # Risk management
            self.risk_reward_ratio = strategy_config.get("risk_reward_ratio", 2.0)
            self.min_volume_threshold = strategy_config.get("min_volume_threshold", 1000)
            self.fvg_confirmation_candles = strategy_config.get("fvg_confirmation_candles", 3)
            
            # Stop loss settings
            self.stop_loss_buffer = strategy_config.get("stop_loss_buffer", 0.5)
            self.lookback_candles = strategy_config.get("lookback_candles", 10)
        else:
            # Default values if no config found
            self.first_candle_start_time = "14:30"
            self.first_candle_end_time = "15:00"
            self.risk_reward_ratio = 2.0
            self.min_volume_threshold = 1000
            self.fvg_confirmation_candles = 3
            self.stop_loss_buffer = 0.5
            self.lookback_candles = 10

    def initialise(self) -> None:
        """Initialize the strategy"""
        logging.info("Simplicity Strategy initialised")

    def fetch_datapoints(self, market: Market) -> MarketHistory:
        """Fetch both 30-minute and 5-minute data"""
        try:
            # Get 30-minute data for first candle analysis
            thirty_min_data = self.broker.get_prices(market, Interval.MINUTE_30, 10)
            
            # Get 5-minute data for breakout monitoring
            five_min_data = self.broker.get_prices(market, Interval.MINUTE_5, 20)
            
            # Store both datasets
            self.thirty_min_data = thirty_min_data
            self.five_min_data = five_min_data
            
            # Return the 5-minute data as primary (for signal generation)
            return five_min_data
            
        except Exception as e:
            logging.error(f"Error fetching datapoints: {e}")
            # Return None if we can't fetch data, but don't crash the strategy
            return None

    def find_trade_signal(self, market: Market, datapoints: MarketHistory) -> TradeSignal:
        """Main strategy logic to find trade signals"""
        try:
            current_time = datetime.now()
            
            # Check if strategy should be active
            if not self._should_process_market(current_time):
                return TradeDirection.NONE, None, None
            
            # Check if strategy should complete
            if self._should_complete_strategy(current_time):
                self.strategy_complete = True
                logging.info("Simplicity Strategy completed for the day")
                return TradeDirection.NONE, None, None
            
            # Fetch both timeframes if we don't have them
            if not self.thirty_min_data or not self.five_min_data:
                fetched_data = self.fetch_datapoints(market)
                if fetched_data is None:
                    logging.warning("Could not fetch market data, skipping this cycle")
                    return TradeDirection.NONE, None, None
            
            # Check if it's time to capture first candle
            if self._is_first_candle_time(current_time) and self.trade_state == TradeState.WAITING_FOR_FIRST_CANDLE:
                logging.info(f"First candle time window active: {current_time.strftime('%H:%M')}")
                self._capture_first_candle(market)
                return TradeDirection.NONE, None, None
            
            # If we have first candle data, monitor for breakouts
            if self.first_candle_data and self.trade_state == TradeState.MONITORING_BREAKOUT:
                logging.debug(f"Monitoring for breakouts. First candle: High={self.first_candle_data.high}, Low={self.first_candle_data.low}")
                
                if self._detect_breakout(market, datapoints):
                    logging.info("Breakout detected! Waiting for Fair Value Gap confirmation...")
                    self.trade_state = TradeState.WAITING_FOR_FVG
                    return TradeDirection.NONE, None, None
            
            # If waiting for FVG confirmation
            if self.trade_state == TradeState.WAITING_FOR_FVG:
                logging.debug("Checking for Fair Value Gap confirmation...")
                
                if self._detect_fair_value_gap(market, datapoints):
                    logging.info("Fair Value Gap confirmed! Generating trade signal...")
                    return self._generate_trade_signal(market, datapoints)
            
            return TradeDirection.NONE, None, None
            
        except Exception as e:
            logging.error(f"Error in find_trade_signal: {e}")
            return TradeDirection.NONE, None, None

    def _should_process_market(self, current_time: datetime) -> bool:
        """Determine if we should process this market"""
        # Only trade during market hours (simplified check)
        return True

    def _should_complete_strategy(self, current_time: datetime) -> bool:
        """Check if strategy should complete and switch back to momentum"""
        # Complete strategy after 16:00 UK time (end of optimal trading window)
        # or if we've been running for more than 2 hours
        hour = current_time.hour
        return hour >= 16

    def _is_first_candle_time(self, current_time: datetime) -> bool:
        """Check if current time is during first candle period"""
        start_time = datetime.strptime(self.first_candle_start_time, "%H:%M").time()
        end_time = datetime.strptime(self.first_candle_end_time, "%H:%M").time()
        
        return start_time <= current_time.time() <= end_time

    def _capture_first_candle(self, market: Market) -> None:
        """Capture the first 30-minute candle data"""
        try:
            if self.thirty_min_data and len(self.thirty_min_data.dataframe) > 0:
                df = self.thirty_min_data.dataframe
                first_candle = df.iloc[0]  # First 30-minute candle
                
                # IG API doesn't provide 'open' price, so we'll use close price
                # or estimate it from the data structure
                open_price = first_candle.get('open', first_candle['close'])
                
                self.first_candle_data = FirstCandleData(
                    high=first_candle['high'],
                    low=first_candle['low'],
                    open=open_price,
                    close=first_candle['close'],
                    volume=first_candle['volume'],
                    timestamp=first_candle.name
                )
                
                self.trade_state = TradeState.MONITORING_BREAKOUT
                
                logging.info(f"First candle captured for {market.epic}: High={self.first_candle_data.high}, Low={self.first_candle_data.low}")
            
        except Exception as e:
            logging.error(f"Error capturing first candle: {e}")
            # Log the actual DataFrame columns for debugging
            if self.thirty_min_data and len(self.thirty_min_data.dataframe) > 0:
                df = self.thirty_min_data.dataframe
                logging.error(f"Available columns: {list(df.columns)}")
                logging.error(f"First candle data: {df.iloc[0].to_dict()}")

    def _detect_breakout(self, market: Market, datapoints: MarketHistory) -> bool:
        """Detect if price has broken above first candle high or below first candle low"""
        if not self.first_candle_data:
            return False
        
        try:
            df = datapoints.dataframe
            if len(df) < 2:
                return False
            
            current_price = df.iloc[-1]['close']
            first_candle_high = self.first_candle_data.high
            first_candle_low = self.first_candle_data.low
            
            # Check for breakout above high
            if current_price > first_candle_high:
                logging.info(f"Breakout above first candle high detected: {current_price} > {first_candle_high}")
                return True
            
            # Check for breakout below low
            if current_price < first_candle_low:
                logging.info(f"Breakout below first candle low detected: {current_price} < {first_candle_low}")
                return True
            
            return False
            
        except Exception as e:
            logging.error(f"Error detecting breakout: {e}")
            return False

    def _detect_fair_value_gap(self, market: Market, datapoints: MarketHistory) -> bool:
        """Detect Fair Value Gap after breakout"""
        if not self.first_candle_data:
            return False
        
        try:
            df = datapoints.dataframe
            
            if len(df) < self.fvg_confirmation_candles:
                return False
            
            # Get recent candles for FVG analysis
            recent_candles = df.tail(self.fvg_confirmation_candles)
            
            # Determine breakout direction
            breakout_direction = self._determine_breakout_direction(df)
            
            if breakout_direction == "high":
                return self._detect_fvg_high_breakout(recent_candles)
            elif breakout_direction == "low":
                return self._detect_fvg_low_breakout(recent_candles)
            
            return False
            
        except Exception as e:
            logging.error(f"Error detecting Fair Value Gap: {e}")
            return False

    def _determine_breakout_direction(self, df: pd.DataFrame) -> str:
        """Determine if breakout was above high or below low"""
        if not self.first_candle_data or len(df) < 2:
            return "none"
        
        current_price = df.iloc[-1]['close']
        first_candle_high = self.first_candle_data.high
        first_candle_low = self.first_candle_data.low
        
        if current_price > first_candle_high:
            return "high"
        elif current_price < first_candle_low:
            return "low"
        
        return "none"

    def _detect_fvg_high_breakout(self, recent_candles: pd.DataFrame) -> bool:
        """Detect FVG for high breakout"""
        first_candle_high = self.first_candle_data.high
        
        if len(recent_candles) >= 3:
            candle_1 = recent_candles.iloc[-3]  # First candle in sequence
            candle_2 = recent_candles.iloc[-2]  # Second candle
            candle_3 = recent_candles.iloc[-1]  # Current candle
            
            # Pattern: Break above high, then retrace back into range
            if (candle_1['high'] > first_candle_high and 
                candle_2['low'] < first_candle_high and
                candle_3['close'] > first_candle_high):
                
                logging.info("Fair Value Gap detected for high breakout")
                return True
        
        return False

    def _detect_fvg_low_breakout(self, recent_candles: pd.DataFrame) -> bool:
        """Detect FVG for low breakout"""
        first_candle_low = self.first_candle_data.low
        
        if len(recent_candles) >= 3:
            candle_1 = recent_candles.iloc[-3]  # First candle in sequence
            candle_2 = recent_candles.iloc[-2]  # Second candle
            candle_3 = recent_candles.iloc[-1]  # Current candle
            
            # Pattern: Break below low, then retrace back into range
            if (candle_1['low'] < first_candle_low and 
                candle_2['high'] > first_candle_low and
                candle_3['close'] < first_candle_low):
                
                logging.info("Fair Value Gap detected for low breakout")
                return True
        
        return False

    def _generate_trade_signal(self, market: Market, datapoints: MarketHistory) -> TradeSignal:
        """Generate trade signal after FVG confirmation"""
        if not self.first_candle_data:
            return TradeDirection.NONE, None, None
        
        try:
            df = datapoints.dataframe
            
            if len(df) < 2:
                return TradeDirection.NONE, None, None
            
            current_price = df.iloc[-1]['close']
            breakout_direction = self._determine_breakout_direction(df)
            
            if breakout_direction == "high":
                return self._generate_long_signal(market, current_price, df)
            elif breakout_direction == "low":
                return self._generate_short_signal(market, current_price, df)
            
            return TradeDirection.NONE, None, None
            
        except Exception as e:
            logging.error(f"Error generating trade signal: {e}")
            return TradeDirection.NONE, None, None

    def _generate_long_signal(self, market: Market, current_price: float, df: pd.DataFrame) -> TradeSignal:
        """Generate long trade signal"""
        try:
            # Entry price: Current market price
            entry_price = current_price
            
            # Stop loss: Lowest candle body on 5-minute chart
            stop_loss = self._find_lowest_candle_body(df, "long")
            
            # Take profit: 2:1 risk-reward ratio
            risk = entry_price - stop_loss
            take_profit = entry_price + (risk * self.risk_reward_ratio)
            
            # Send Telegram notification
            self.telegram_bot.send_trade_signal(
                market=market.epic,
                direction="LONG",
                entry_price=entry_price,
                take_profit=take_profit,
                stop_loss=stop_loss,
                conditions=["Simplicity Strategy - FVG Confirmed"]
            )
            
            logging.info(f"Long signal generated for {market.epic}: Entry={entry_price}, SL={stop_loss}, TP={take_profit}")
            
            return TradeDirection.BUY, take_profit, stop_loss
            
        except Exception as e:
            logging.error(f"Error generating long signal: {e}")
            return TradeDirection.NONE, None, None

    def _generate_short_signal(self, market: Market, current_price: float, df: pd.DataFrame) -> TradeSignal:
        """Generate short trade signal"""
        try:
            # Entry price: Current market price
            entry_price = current_price
            
            # Stop loss: Highest candle body on 5-minute chart
            stop_loss = self._find_highest_candle_body(df, "short")
            
            # Take profit: 2:1 risk-reward ratio
            risk = stop_loss - entry_price
            take_profit = entry_price - (risk * self.risk_reward_ratio)
            
            # Send Telegram notification
            self.telegram_bot.send_trade_signal(
                market=market.epic,
                direction="SHORT",
                entry_price=entry_price,
                take_profit=take_profit,
                stop_loss=stop_loss,
                conditions=["Simplicity Strategy - FVG Confirmed"]
            )
            
            logging.info(f"Short signal generated for {market.epic}: Entry={entry_price}, SL={stop_loss}, TP={take_profit}")
            
            return TradeDirection.SELL, take_profit, stop_loss
            
        except Exception as e:
            logging.error(f"Error generating short signal: {e}")
            return TradeDirection.NONE, None, None

    def _find_lowest_candle_body(self, df: pd.DataFrame, direction: str) -> float:
        """Find the lowest candle body for stop loss placement"""
        if len(df) == 0:
            return 0.0
        
        # Look at last N candles for lowest body
        recent_candles = df.tail(self.lookback_candles) if len(df) >= self.lookback_candles else df
        
        lowest_body = float('inf')
        
        for _, candle in recent_candles.iterrows():
            # IG API doesn't provide 'open' price, so use close price
            body_low = candle['close']  # Use close price as body low
            if body_low < lowest_body:
                lowest_body = body_low
        
        # Add small buffer below lowest body
        return lowest_body - self.stop_loss_buffer

    def _find_highest_candle_body(self, df: pd.DataFrame, direction: str) -> float:
        """Find the highest candle body for stop loss placement"""
        if len(df) == 0:
            return 0.0
        
        # Look at last N candles for highest body
        recent_candles = df.tail(self.lookback_candles) if len(df) >= self.lookback_candles else df
        
        highest_body = float('-inf')
        
        for _, candle in recent_candles.iterrows():
            # IG API doesn't provide 'open' price, so use close price
            body_high = candle['close']  # Use close price as body high
            if body_high > highest_body:
                highest_body = body_high
        
        # Add small buffer above highest body
        return highest_body + self.stop_loss_buffer

    def backtest(self, market: Market, start_date: datetime, end_time: datetime) -> BacktestResult:
        """Backtest the strategy"""
        # Placeholder for backtesting functionality
        logging.info(f"Backtesting Simplicity Strategy for {market.epic} from {start_date} to {end_time}")
        
        return {
            "total_return": 0.0,
            "trades": []
        }

    def is_strategy_complete(self) -> bool:
        """Check if this strategy is complete and should switch back to momentum"""
        return self.strategy_complete

