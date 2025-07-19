import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple, List

from .base import Strategy, BacktestResult, TradeSignal
from ..components.utils import Interval, TradeDirection, Utils
from ..components.configuration import Configuration
from ..components.broker.broker import Broker
from ..interfaces import Market, MarketHistory
from ..components.telegram.telegram_bot import TelegramBot
from .signal_confidence import SignalConfidenceScorer


class IntradayGoldStrategy(Strategy):
    def __init__(self, config: Configuration, broker: Broker) -> None:
        super().__init__(config, broker)
        self.telegram_bot = TelegramBot(config)
        self.confidence_scorer = SignalConfidenceScorer()
        
        # Strategy state
        self.daily_levels = {}
        self.pivot_levels = {}
        self.swing_levels = []
        self.last_pattern_check = None
        self.current_trade = None
        
        # Initialize configuration
        self.read_configuration(config)
        
    def read_configuration(self, config: Configuration) -> None:
        raw = config.get_raw_config()
        strategy_config = raw["strategies"]["intraday_gold"]
        
        # Pattern detection parameters
        self.engulfing_min_body_ratio = strategy_config["engulfing_min_body_ratio"]
        self.pin_bar_min_tail_ratio = strategy_config["pin_bar_min_tail_ratio"]
        self.pin_bar_max_body_ratio = strategy_config["pin_bar_max_body_ratio"]
        self.breakout_min_distance = strategy_config["breakout_min_distance"]  # % of price
        self.false_breakout_max_distance = strategy_config["false_breakout_max_distance"]  # % of price
            
        # Support & Resistance parameters
        self.ema_period = strategy_config["ema_period"]
        self.ema_period2 = strategy_config["ema_period2"]
        self.swing_lookback = strategy_config["swing_lookback"]
        self.pivot_lookback = strategy_config["pivot_lookback"]  # days
            
        # Risk Management
        self.risk_reward_ratio = strategy_config["risk_reward_ratio"]
        self.atr_period = strategy_config["atr_period"]
        self.trailing_stop_atr_multiplier = strategy_config["trailing_stop_atr_multiplier"]
            
        # Volume confirmation
        self.use_volume_filter = strategy_config["use_volume_filter"]
        self.volume_multiplier = strategy_config["volume_multiplier"]
            
        # Time filters
        self.trading_start_hour = strategy_config["trading_start_hour"]  # London open
        self.trading_end_hour = strategy_config["trading_end_hour"]  # NY close
            
        logging.info("Intraday Gold Strategy configuration loaded successfully")
            
        
    def initialise(self) -> None:
        """Initialize the strategy"""
        logging.info("Intraday Gold Strategy initialised")
        
    def fetch_datapoints(self, market: Market) -> MarketHistory:
        """Fetch market data for analysis"""
        return self.broker.get_prices(market, Interval.MINUTE_15, 100)  # Get enough data for pattern detection
        
    def find_trade_signal(self, market: Market, datapoints: MarketHistory) -> TradeSignal:
        """Main signal detection method"""
        try:
            # Convert to DataFrame
            df = datapoints.dataframe  # Use the dataframe attribute directly
            if len(df) < 50:  # Need minimum data
                return TradeDirection.NONE, None, None
                
            # Calculate indicators
            df = self._calculate_indicators(df)
            
            # Update daily levels if needed
            self._update_daily_levels(df)
            
            # Get latest candle
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Pattern detection
            signal_direction = self._detect_patterns(df, latest, prev)
            
            if signal_direction != TradeDirection.NONE:
                # Calculate confidence score
                confidence_data = self._calculate_confidence_score(df, signal_direction)
                
                # Generate proper signal with stop loss and take profit
                if signal_direction == TradeDirection.BUY:
                    signal = self._buy_signal(market, latest)
                else:
                    signal = self._sell_signal(market, latest)
                
                # Prepare conditions list for Telegram
                conditions = [
                    f"Confidence Score: {confidence_data['overall_score']}/100 ({confidence_data['confidence_level']})"
                ]
                
                # Add individual indicator scores to conditions
                for indicator, score in confidence_data['individual_scores'].items():
                    conditions.append(f"{indicator.upper()}: {score}/5")
                
                # Add volume condition if enabled
                if self.use_volume_filter and latest['volume'] > latest.get('volume_ma', latest['volume']):
                    conditions.append("Volume above MA")
                
                # Add EMA condition
                if signal_direction == TradeDirection.BUY and latest['close'] > latest['ema_50']:
                    conditions.append("Price above 50 EMA")
                elif signal_direction == TradeDirection.SELL and latest['close'] < latest['ema_50']:
                    conditions.append("Price below 50 EMA")
                
                # Send Telegram signal
                if signal[0] != TradeDirection.NONE:
                    direction_text = "LONG" if signal_direction == TradeDirection.BUY else "SHORT"
                    self.telegram_bot.send_trade_signal(
                        market=market.epic,
                        direction=direction_text,
                        entry_price=latest['close'],
                        take_profit=signal[1],
                        stop_loss=signal[2],
                        conditions=conditions
                    )
                
                # Log signal details
                logging.info(f"Intraday Gold Signal Generated: {signal_direction}")
                logging.info(f"Pattern: {self._get_pattern_name()}")
                logging.info(f"Confidence Score: {confidence_data['overall_score']}/100 ({confidence_data['confidence_level']})")
                
                # Add individual indicator scores to logging
                for indicator, score in confidence_data['individual_scores'].items():
                    logging.info(f"  {indicator.upper()}: {score}/5")
                
                return signal
            
            return TradeDirection.NONE, None, None
            
        except Exception as e:
            logging.error(f"Error in find_trade_signal: {e}")
            return TradeDirection.NONE, None, None
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators"""
        # EMA
        df['ema_50'] = df['close'].ewm(span=self.ema_period).mean()
        df['ema_200'] = df['close'].ewm(span=self.ema_period2).mean()  # Add 200 EMA for confidence scoring
        
        # ATR for stop loss calculation
        df['tr'] = self._true_range(df)
        df['atr'] = df['tr'].rolling(window=self.atr_period).mean()
        
        # Volume moving average
        if self.use_volume_filter:
            df['volume_ma'] = df['volume'].rolling(window=20).mean()
        
        # Calculate pivot points
        df = self._calculate_pivot_points(df)
        
        # Calculate swing levels
        df = self._calculate_swing_levels(df)
        
        return df
    
    def _true_range(self, df: pd.DataFrame) -> pd.Series:
        """Calculate True Range"""
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        return ranges.max(axis=1)
    
    def _calculate_pivot_points(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate pivot points from daily data"""
        # Convert index to datetime if not already
        df.index = pd.to_datetime(df.index, format='%Y:%m:%d-%H:%M:%S')
        
        # Group by date using the index's date component
        daily_data = df.groupby(df.index.date).agg({
            'high': 'max',
            'low': 'min', 
            'open': 'first',
            'close': 'last'
        }).reset_index()
        
        if len(daily_data) >= 2:
            # Use previous day's data
            prev_day = daily_data.iloc[-2]
            pivot = (prev_day['high'] + prev_day['low'] + prev_day['close']) / 3
            r1 = 2 * pivot - prev_day['low']
            s1 = 2 * pivot - prev_day['high']
            
            # Store pivot levels
            self.pivot_levels = {
                'pivot': pivot,
                'r1': r1,
                's1': s1,
                'prev_high': prev_day['high'],
                'prev_low': prev_day['low'],
                'prev_close': prev_day['close']
            }
            
            # Add to DataFrame
            df['pivot'] = pivot
            df['r1'] = r1
            df['s1'] = s1
            df['prev_high'] = prev_day['high']
            df['prev_low'] = prev_day['low']
            df['prev_close'] = prev_day['close']
        
        return df
    
    def _calculate_swing_levels(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate recent swing highs and lows"""
        # Find swing highs and lows
        swing_highs = []
        swing_lows = []
        
        for i in range(2, len(df) - 2):
            # Swing high
            if (df.iloc[i]['high'] > df.iloc[i-1]['high'] and 
                df.iloc[i]['high'] > df.iloc[i-2]['high'] and
                df.iloc[i]['high'] > df.iloc[i+1]['high'] and
                df.iloc[i]['high'] > df.iloc[i+2]['high']):
                swing_highs.append(df.iloc[i]['high'])
            
            # Swing low
            if (df.iloc[i]['low'] < df.iloc[i-1]['low'] and 
                df.iloc[i]['low'] < df.iloc[i-2]['low'] and
                df.iloc[i]['low'] < df.iloc[i+1]['low'] and
                df.iloc[i]['low'] < df.iloc[i+2]['low']):
                swing_lows.append(df.iloc[i]['low'])
        
        # Store recent swing levels
        self.swing_levels = {
            'highs': swing_highs[-5:] if swing_highs else [],
            'lows': swing_lows[-5:] if swing_lows else []
        }
        
        return df
    
    def _update_daily_levels(self, df: pd.DataFrame) -> None:
        """Update daily support/resistance levels"""
        if 'prev_high' in df.columns:
            self.daily_levels = {
                'prev_high': df['prev_high'].iloc[-1],
                'prev_low': df['prev_low'].iloc[-1],
                'prev_close': df['prev_close'].iloc[-1]
            }
    
    def _detect_patterns(self, df: pd.DataFrame, latest: pd.Series, prev: pd.Series) -> TradeDirection:
        """Detect price-action patterns"""
        # Check for engulfing patterns
        engulfing_signal = self._check_engulfing_pattern(latest, prev)
        if engulfing_signal != TradeDirection.NONE:
            return engulfing_signal
        
        # Check for pin bar patterns
        pin_signal = self._check_pin_bar_pattern(latest, prev)
        if pin_signal != TradeDirection.NONE:
            return pin_signal
        
        # Check for breakout + retest patterns
        breakout_signal = self._check_breakout_retest_pattern(df, latest)
        if breakout_signal != TradeDirection.NONE:
            return breakout_signal
        
        # Check for false breakout patterns
        false_breakout_signal = self._check_false_breakout_pattern(df, latest)
        if false_breakout_signal != TradeDirection.NONE:
            return false_breakout_signal
        
        return TradeDirection.NONE
    
    def _check_engulfing_pattern(self, latest: pd.Series, prev: pd.Series) -> TradeDirection:
        """Check for bullish/bearish engulfing patterns"""
        # Calculate body sizes
        latest_body = abs(latest['close'] - latest['open'])
        prev_body = abs(prev['close'] - prev['open'])
        
        # Check if bodies are significant
        if latest_body < (latest['high'] - latest['low']) * self.engulfing_min_body_ratio:
            return TradeDirection.NONE
        
        # Bullish engulfing
        if (latest['close'] > latest['open'] and  # Current candle is bullish
            prev['close'] < prev['open'] and      # Previous candle is bearish
            latest['open'] < prev['close'] and    # Current open below prev close
            latest['close'] > prev['open']):      # Current close above prev open
            
            # Check if near support
            if self._is_near_support(latest['low']):
                logging.info("Bullish Engulfing pattern detected near support")
                return TradeDirection.BUY
        
        # Bearish engulfing
        if (latest['close'] < latest['open'] and  # Current candle is bearish
            prev['close'] > prev['open'] and      # Previous candle is bullish
            latest['open'] > prev['close'] and    # Current open above prev close
            latest['close'] < prev['open']):      # Current close below prev open
            
            # Check if near resistance
            if self._is_near_resistance(latest['high']):
                logging.info("Bearish Engulfing pattern detected near resistance")
                return TradeDirection.SELL
        
        return TradeDirection.NONE
    
    def _check_pin_bar_pattern(self, latest: pd.Series, prev: pd.Series) -> TradeDirection:
        """Check for pin bar (hammer/shooting star) patterns"""
        body_size = abs(latest['close'] - latest['open'])
        total_range = latest['high'] - latest['low']
        
        if total_range == 0:
            return TradeDirection.NONE
        
        body_ratio = body_size / total_range
        upper_tail = latest['high'] - max(latest['open'], latest['close'])
        lower_tail = min(latest['open'], latest['close']) - latest['low']
        
        # Bullish pin bar (hammer)
        if (body_ratio <= self.pin_bar_max_body_ratio and
            lower_tail >= total_range * self.pin_bar_min_tail_ratio and
            upper_tail <= total_range * 0.1):
            
            if self._is_near_support(latest['low']):
                logging.info("Bullish Pin Bar pattern detected near support")
                return TradeDirection.BUY
        
        # Bearish pin bar (shooting star)
        if (body_ratio <= self.pin_bar_max_body_ratio and
            upper_tail >= total_range * self.pin_bar_min_tail_ratio and
            lower_tail <= total_range * 0.1):
            
            if self._is_near_resistance(latest['high']):
                logging.info("Bearish Pin Bar pattern detected near resistance")
                return TradeDirection.SELL
        
        return TradeDirection.NONE
    
    def _check_breakout_retest_pattern(self, df: pd.DataFrame, latest: pd.Series) -> TradeDirection:
        """Check for breakout + retest patterns"""
        if len(df) < 10:
            return TradeDirection.NONE
        
        # Look for recent breakout
        for i in range(len(df) - 10, len(df) - 2):
            candle = df.iloc[i]
            prev_candle = df.iloc[i-1] if i > 0 else None
            
            if prev_candle is None:
                continue
            
            # Check if this candle broke above resistance
            if (candle['close'] > prev_candle['high'] and
                self._is_near_resistance(prev_candle['high'])):
                
                # Look for retest
                for j in range(i + 1, len(df) - 1):
                    retest_candle = df.iloc[j]
                    if (retest_candle['low'] <= prev_candle['high'] and
                        retest_candle['close'] > prev_candle['high']):
                        
                        # Check if latest candle confirms the retest
                        if latest['close'] > prev_candle['high']:
                            logging.info("Breakout + Retest pattern detected (Bullish)")
                            return TradeDirection.BUY
            
            # Check if this candle broke below support
            if (candle['close'] < prev_candle['low'] and
                self._is_near_support(prev_candle['low'])):
                
                # Look for retest
                for j in range(i + 1, len(df) - 1):
                    retest_candle = df.iloc[j]
                    if (retest_candle['high'] >= prev_candle['low'] and
                        retest_candle['close'] < prev_candle['low']):
                        
                        # Check if latest candle confirms the retest
                        if latest['close'] < prev_candle['low']:
                            logging.info("Breakout + Retest pattern detected (Bearish)")
                            return TradeDirection.SELL
        
        return TradeDirection.NONE
    
    def _check_false_breakout_pattern(self, df: pd.DataFrame, latest: pd.Series) -> TradeDirection:
        """Check for false breakout patterns"""
        if not self.daily_levels:
            return TradeDirection.NONE
        
        prev_high = self.daily_levels['prev_high']
        prev_low = self.daily_levels['prev_low']
        
        # Check if price broke above yesterday's high then reversed
        for i in range(len(df) - 5, len(df) - 1):
            candle = df.iloc[i]
            if (candle['high'] > prev_high and
                candle['close'] < prev_high and
                latest['close'] < prev_high):
                
                logging.info("False Breakout pattern detected (Bearish)")
                return TradeDirection.SELL
        
        # Check if price broke below yesterday's low then reversed
        for i in range(len(df) - 5, len(df) - 1):
            candle = df.iloc[i]
            if (candle['low'] < prev_low and
                candle['close'] > prev_low and
                latest['close'] > prev_low):
                
                logging.info("False Breakout pattern detected (Bullish)")
                return TradeDirection.BUY
        
        return TradeDirection.NONE
    
    def _is_near_support(self, price: float) -> bool:
        """Check if price is near support levels"""
        tolerance = price * 0.001  # 0.1% tolerance
        
        # Check pivot support
        if 's1' in self.pivot_levels:
            if abs(price - self.pivot_levels['s1']) <= tolerance:
                return True
        
        # Check previous day low
        if self.daily_levels and abs(price - self.daily_levels['prev_low']) <= tolerance:
            return True
        
        # Check swing lows
        for swing_low in self.swing_levels.get('lows', []):
            if abs(price - swing_low) <= tolerance:
                return True
        
        # Check 50 EMA
        return False  # Will be checked in main logic
    
    def _is_near_resistance(self, price: float) -> bool:
        """Check if price is near resistance levels"""
        tolerance = price * 0.001  # 0.1% tolerance
        
        # Check pivot resistance
        if 'r1' in self.pivot_levels:
            if abs(price - self.pivot_levels['r1']) <= tolerance:
                return True
        
        # Check previous day high
        if self.daily_levels and abs(price - self.daily_levels['prev_high']) <= tolerance:
            return True
        
        # Check swing highs
        for swing_high in self.swing_levels.get('highs', []):
            if abs(price - swing_high) <= tolerance:
                return True
        
        return False
    
    def _calculate_stop_loss_take_profit(self, entry_price: float, direction: TradeDirection, 
                                       atr: float, pattern_low: float, pattern_high: float) -> Tuple[float, float]:
        """Calculate stop loss and take profit levels"""
        if direction == TradeDirection.BUY:
            # Stop loss below pattern low or ATR-based
            stop_loss = min(pattern_low, entry_price - (atr * self.trailing_stop_atr_multiplier))
            # Take profit at 2:1 risk-reward
            risk = entry_price - stop_loss
            take_profit = entry_price + (risk * self.risk_reward_ratio)
        else:
            # Stop loss above pattern high or ATR-based
            stop_loss = max(pattern_high, entry_price + (atr * self.trailing_stop_atr_multiplier))
            # Take profit at 2:1 risk-reward
            risk = stop_loss - entry_price
            take_profit = entry_price - (risk * self.risk_reward_ratio)
        
        return stop_loss, take_profit
    
    def _calculate_confidence_score(self, df: pd.DataFrame, direction: TradeDirection) -> Dict[str, Any]:
        """Calculate confidence score for the signal"""
        latest = df.iloc[-1]
        
        indicators = {
            'ema': {
                'price': latest['close'],
                'ema_50': latest['ema_50'],
                'ema_200': latest['ema_200']
            },
            'volume': {
                'current': latest['volume'],
                'average': latest.get('volume_ma', latest['volume'])
            },
            'atr': latest['atr'],
            'trend_direction': direction
        }
        
        return self.confidence_scorer.calculate_overall_score(indicators)
    
    def _get_pattern_name(self) -> str:
        """Get the name of the detected pattern"""
        # This would be set during pattern detection
        return "Price Action Pattern"
    
    def _buy_signal(self, market: Market, latest: pd.Series) -> TradeSignal:
        """Generate a buy signal with take profit and stop loss levels"""
        atr = latest['atr']
        stop_loss = latest['close'] - (atr * self.trailing_stop_atr_multiplier)
        take_profit = latest['close'] + (atr * self.trailing_stop_atr_multiplier * self.risk_reward_ratio)
        return TradeDirection.BUY, take_profit, stop_loss
    
    def _sell_signal(self, market: Market, latest: pd.Series) -> TradeSignal:
        """Generate a sell signal with take profit and stop loss levels"""
        atr = latest['atr']
        stop_loss = latest['close'] + (atr * self.trailing_stop_atr_multiplier)
        take_profit = latest['close'] - (atr * self.trailing_stop_atr_multiplier * self.risk_reward_ratio)
        return TradeDirection.SELL, take_profit, stop_loss
    
    def backtest(self, market: Market, start_date: datetime, end_time: datetime) -> Any:
        """Backtest the strategy"""
        # Placeholder for backtesting
        return None 