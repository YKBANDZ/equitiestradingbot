import logging
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from typing import Optional, Tuple, Dict, Any

from ..components.configuration import Configuration
from ..components.utils import Interval, TradeDirection, Utils
from ..components.broker.broker import Broker
from ..interfaces import Market, MarketHistory 
from .base import BacktestResult, Strategy, TradeSignal
from ..components.telegram.telegram_bot import TelegramBot
from .signal_confidence import SignalConfidenceScorer

class AdvancedMomentumStrategy(Strategy):
    def __init__(self, config: Configuration, broker: Broker) -> None:
        super().__init__(config, broker)
        self.telegram_bot = TelegramBot(config)
        self.confidence_scorer = SignalConfidenceScorer()
        logging.info("Advanced Momentum Strategy created")

    def read_configuration(self, config: Configuration) -> None:
        raw = config.get_raw_config()
        strategy_config = raw["strategies"]["advanced_momentum"]
        
        # Entry Signal Parameters
        self.fast_length = strategy_config["fast_length"]
        self.slow_length = strategy_config["slow_length"]
        self.signal_smoothing = strategy_config["signal_smoothing"]
        
        # Exit Strategy Parameters
        self.atr_length = strategy_config["atr_length"]
        self.atr_multiplier = strategy_config["base_risk_multiplier"]
        self.risk_reward_ratio = strategy_config["risk_reward_ratio"]
        self.use_squeeze_exit = strategy_config["force_exit_squeeze"]
        
        # Trend Filter Parameters
        self.use_long_trend = strategy_config["use_long_trend"]
        self.use_short_trend = strategy_config["use_short_trend"]
        self.ma_length = strategy_config["ma_length"]
        
        # RSI Parameters
        self.rsi_length = strategy_config["rsi_length"]
        self.rsi_long_boundary = strategy_config["rsi_long_boundary"]
        self.rsi_short_boundary = strategy_config["rsi_short_boundary"]
        
        # ADX Parameters
        self.use_adx = strategy_config["use_adx_limiter"]
        self.adx_length = strategy_config["adx_length"]
        self.di_length = strategy_config["di_length"]
        self.adx_high_boundary = strategy_config["adx_high_boundary"]
        self.adx_low_boundary = strategy_config["adx_low_boundary"]
        
        # Volume Filter Parameters
        self.use_volume_filter = strategy_config["use_volume_filter"]
        self.volume_ma_length = strategy_config["volume_ma_length"]
        
        # Squeeze Parameters
        self.bb_length = strategy_config["bb_length"]
        self.bb_mult = strategy_config["bb_mult_factor"]
        self.kc_length = strategy_config["kc_length"]
        self.kc_mult = strategy_config["kc_mult_factor"]
        self.use_true_range = strategy_config["use_true_range"]

    def initialise(self) -> None:
        logging.info("Advanced Momentum Strategy initialised")

    def fetch_datapoints(self, market: Market) -> MarketHistory:
        return self.broker.get_prices(market, Interval.MINUTE_15, self.slow_length * 2)

    def find_trade_signal(self, market: Market, datapoints: MarketHistory) -> TradeSignal:
        try:
            df = datapoints.dataframe.copy()
            df.columns = df.columns.str.lower()
            
            # Calculate all indicators
            df = self._calculate_indicators(df)
            
            # Get latest values
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Log analysis
            logging.info(f"Trade Analysis for {market.epic}:")
            logging.info(f"Latest Price: {latest['close']:.5f}")
            logging.info(f"50 EMA: {latest['ema_50']:.5f}")
            logging.info(f"200 EMA: {latest['ema_200']:.5f}")
            logging.info(f"MACD: {latest['macd']:.5f} (Signal: {latest['signal']:.5f})")
            logging.info(f"RSI: {latest['rsi']:.2f}")
            logging.info(f"ADX: {latest['adx']:.2f}")
            logging.info(f"Volume: {latest['volume']:.0f} (MA: {latest['volume_ma']:.0f})")
            
            # Check for long entry
            logging.info("Checking for LONG signal...")
            if self._check_long_conditions(df):
                # Calculate confidence score for long signal
                confidence_data = self._calculate_confidence_score(df, TradeDirection.BUY)
                
                conditions = [
                    "Price > 50 EMA & 200 EMA",
                    "MACD above signal line and above zero",
                    f"Confidence Score: {confidence_data['overall_score']}/100 ({confidence_data['confidence_level']})"
                ]
                
                # Add individual indicator scores to conditions
                for indicator, score in confidence_data['individual_scores'].items():
                    conditions.append(f"{indicator.upper()}: {score}/5")
                
                if latest['rsi'] > self.rsi_long_boundary:
                    conditions.append("RSI above boundary")
                if self.use_volume_filter and latest['volume'] > latest['volume_ma']:
                    conditions.append("Volume above MA")
                if self.use_adx and latest['adx'] >= 20:
                    conditions.append("ADX above 20")
                if self.use_squeeze_exit and latest['squeeze_momentum'] > 0:
                    conditions.append("Squeeze momentum positive")
                
                signal = self._buy_signal(market, latest)
                if signal[0] != TradeDirection.NONE:
                    self.telegram_bot.send_trade_signal(
                        market=market.epic,
                        direction="LONG",
                        entry_price=latest['close'],
                        take_profit=signal[1],
                        stop_loss=signal[2],
                        conditions=conditions
                    )
                return signal
            
            # Check for short entry
            logging.info("Checking for SHORT signal...")
            if self._check_short_conditions(df):
                # Calculate confidence score for short signal
                confidence_data = self._calculate_confidence_score(df, TradeDirection.SELL)
                
                conditions = [
                    "MACD below signal line and below zero",
                    f"Confidence Score: {confidence_data['overall_score']}/100 ({confidence_data['confidence_level']})"
                ]
                
                # Add individual indicator scores to conditions
                for indicator, score in confidence_data['individual_scores'].items():
                    conditions.append(f"{indicator.upper()}: {score}/5")
                
                if latest['rsi'] < 40:
                    conditions.append("RSI below 40")
                if self.use_volume_filter and latest['volume'] > latest['volume_ma'] * 1.2:
                    conditions.append("Volume above MA * 1.2")
                if self.use_adx and latest['adx'] >= 25:
                    conditions.append("ADX above 25")
                if self.use_squeeze_exit and latest['squeeze_momentum'] < 0:
                    conditions.append("Squeeze momentum negative")
                
                signal = self._sell_signal(market, latest)
                if signal[0] != TradeDirection.NONE:
                    self.telegram_bot.send_trade_signal(
                        market=market.epic,
                        direction="SHORT",
                        entry_price=latest['close'],
                        take_profit=signal[1],
                        stop_loss=signal[2],
                        conditions=conditions
                    )
                return signal
            
            # Log why no trade signal was generated
            logging.info("No trade signal generated. Conditions not met:")
            if not (latest['close'] > latest['ema_50'] and latest['close'] > latest['ema_200']):
                logging.info("  - EMA trend structure not met (required for long)")
            if not (latest['close'] < latest['ema_50'] and latest['close'] < latest['ema_200']):
                logging.info("  - EMA trend structure not met (required for short)")
            if not self._check_rsi_conditions(df):
                logging.info("  - RSI conditions not met")
            if not self._check_volume_conditions(df):
                logging.info("  - Volume conditions not met")
            if not self._check_adx_conditions(df):
                logging.info("  - ADX conditions not met")
            
            return TradeDirection.NONE, None, None
            
        except Exception as e:
            logging.error(f"Error finding trade signal for {market.epic}: {str(e)}")
            logging.error(f"DataFrame columns: {df.columns.tolist()}")
            logging.error(f"DataFrame head:\n{df.head()}")
            return TradeDirection.NONE, None, None

    def _buy_signal(self, market: Market, latest: pd.Series) -> TradeSignal:
        """Generate a buy signal with take profit and stop loss levels"""
        atr = latest['atr']
        stop_loss = latest['close'] - (atr * self.atr_multiplier)
        take_profit = latest['close'] + (atr * self.atr_multiplier * self.risk_reward_ratio)
        return TradeDirection.BUY, take_profit, stop_loss

    def _sell_signal(self, market: Market, latest: pd.Series) -> TradeSignal:
        """Generate a sell signal with take profit and stop loss levels"""
        atr = latest['atr']
        stop_loss = latest['close'] + (atr * self.atr_multiplier)
        take_profit = latest['close'] - (atr * self.atr_multiplier * self.risk_reward_ratio)
        return TradeDirection.SELL, take_profit, stop_loss

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # Calculate MACD
        df['ema_fast'] = df['close'].ewm(span=self.fast_length, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.slow_length, adjust=False).mean()
        df['macd'] = df['ema_fast'] - df['ema_slow']
        df['signal'] = df['macd'].ewm(span=self.signal_smoothing, adjust=False).mean()
        df['histogram'] = df['macd'] - df['signal']
        
        # Calculate 50 and 200 EMAs
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        
        # Log MACD details for verification
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        logging.info("MACD Calculation Details:")
        logging.info(f"  Fast EMA ({self.fast_length}): {latest['ema_fast']:.5f}")
        logging.info(f"  Slow EMA ({self.slow_length}): {latest['ema_slow']:.5f}")
        logging.info(f"  MACD Line: {latest['macd']:.5f}")
        logging.info(f"  Signal Line: {latest['signal']:.5f}")
        logging.info(f"  Histogram: {latest['histogram']:.5f}")
        logging.info(f"  Previous MACD: {prev['macd']:.5f}")
        logging.info(f"  Previous Signal: {prev['signal']:.5f}")
        logging.info(f"  MACD Cross Status: {'Crossed Up' if latest['macd'] > latest['signal'] and prev['macd'] <= prev['signal'] else 'Crossed Down' if latest['macd'] < latest['signal'] and prev['macd'] >= prev['signal'] else 'No Cross'}")
        logging.info(f"  MACD Zero Line Status: {'Above Zero' if latest['macd'] > 0 else 'Below Zero'}")
        
        # Calculate RSI
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=self.rsi_length).mean()
        avg_loss = loss.rolling(window=self.rsi_length).mean()
        rs = avg_gain / avg_loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # Calculate SMA
        df['sma'] = df['close'].rolling(window=self.ma_length).mean()
        
        # Calculate Volume EMA
        df['volume_ma'] = df['volume'].ewm(span=self.volume_ma_length, adjust=False).mean()
        
        # Calculate ADX
        df['tr'] = self._true_range(df)
        df['atr'] = df['tr'].rolling(window=self.atr_length).mean()
        
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        
        df['plus_dm'] = (df['high'] - df['high'].shift()).clip(lower=0)
        df['minus_dm'] = (df['low'].shift() - df['low']).clip(lower=0)
        
        df['plus_di'] = 100 * (df['plus_dm'].rolling(window=self.di_length).mean() / true_range.rolling(window=self.di_length).mean())
        df['minus_di'] = 100 * (df['minus_dm'].rolling(window=self.di_length).mean() / true_range.rolling(window=self.di_length).mean())
        
        df['dx'] = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'])
        df['adx'] = df['dx'].rolling(window=self.adx_length).mean()
        
        # Calculate Squeeze Momentum
        df = self._calculate_squeeze_momentum(df)
        
        return df

    def _true_range(self, df: pd.DataFrame) -> pd.Series:
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        return ranges.max(axis=1)

    def _calculate_squeeze_momentum(self, df: pd.DataFrame) -> pd.DataFrame:
        # Calculate Bollinger Bands
        df['bb_middle'] = df['close'].rolling(window=self.bb_length).mean()
        df['bb_std'] = df['close'].rolling(window=self.bb_length).std()
        df['bb_upper'] = df['bb_middle'] + (df['bb_std'] * self.bb_mult)
        df['bb_lower'] = df['bb_middle'] - (df['bb_std'] * self.bb_mult)
        
        # Calculate Keltner Channels
        if self.use_true_range:
            df['kc_tr'] = self._true_range(df)
        else:
            df['kc_tr'] = df['high'] - df['low']
        
        df['kc_middle'] = df['close'].rolling(window=self.kc_length).mean()
        df['kc_upper'] = df['kc_middle'] + (df['kc_tr'].rolling(window=self.kc_length).mean() * self.kc_mult)
        df['kc_lower'] = df['kc_middle'] - (df['kc_tr'].rolling(window=self.kc_length).mean() * self.kc_mult)
        
        # Calculate Squeeze Momentum
        df['squeeze_on'] = (df['bb_upper'] <= df['kc_upper']) & (df['bb_lower'] >= df['kc_lower'])
        df['squeeze_momentum'] = np.where(
            df['squeeze_on'],
            np.where(df['close'] > df['bb_middle'], 1, -1),
            0
        )
        
        return df

    def _check_long_conditions(self, df: pd.DataFrame) -> bool:
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        conditions_met = 0
        total_conditions = 0
        
        # EMA trend structure check (now optional like other conditions)
        if latest['close'] > latest['ema_50'] and latest['close'] > latest['ema_200']:
            conditions_met += 1
            logging.info("  - EMA trend structure met")
        else:
            logging.info("  - EMA trend structure not met")
        
        # Mandatory MACD check with 60% minimum difference
        macd_diff_percent = ((latest['macd'] - latest['signal']) / abs(latest['signal'])) * 100
        if not (latest['macd'] > latest['signal'] and latest['macd'] > 0 and macd_diff_percent >= 60):
            logging.info(f"  - MACD conditions not met (required: above signal line by 60%, current diff: {macd_diff_percent:.2f}%)")
            return False
        logging.info(f"  - MACD conditions met (above signal line by {macd_diff_percent:.2f}% and above zero)")
        
        # Additional conditions (need 2 more to meet the 4/5 requirement)
        total_conditions = 4  # EMA, RSI, Volume, ADX
        
        # Check RSI
        if latest['rsi'] > self.rsi_long_boundary:
            conditions_met += 1
            logging.info("  - RSI condition met")

        # Check volume conditions
        if self.use_volume_filter:
            if latest['volume'] > latest['volume_ma']:
                conditions_met += 1
                logging.info("  - Volume condition met")
        
        # Check ADX (only check if below 20)
        if self.use_adx:
            if latest['adx'] >= 20:
                conditions_met += 1
                logging.info("  - ADX condition met (above 20)")
        
        # Check Squeeze Momentum (optional)
        if self.use_squeeze_exit:
            if latest['squeeze_momentum'] > 0:
                conditions_met += 1
                logging.info("  - Squeeze momentum condition met")
        
        logging.info(f"  - Additional conditions met: {conditions_met}/{total_conditions}")
        # Need at least 2 more conditions (total of 4 including mandatory MACD)
        return conditions_met >= 2

    def _check_short_conditions(self, df: pd.DataFrame) -> bool:
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        conditions_met = 0
        total_conditions = 0
        
        # EMA trend structure check (now optional like other conditions)
        if latest['close'] < latest['ema_50'] and latest['close'] < latest['ema_200']:
            conditions_met += 1
            logging.info("  - EMA trend structure met (price < 50 EMA < 200 EMA)")
        else:
            logging.info("  - EMA trend structure not met")
        
        # Mandatory MACD check with 60% minimum difference
        macd_diff_percent = ((latest['signal'] - latest['macd']) / abs(latest['signal'])) * 100
        if not (latest['macd'] < latest['signal'] and latest['macd'] < 0 and macd_diff_percent >= 60):
            logging.info(f"  - MACD conditions not met (required: below signal line by 60%, current diff: {macd_diff_percent:.2f}%)")
            return False
        logging.info(f"  - MACD conditions met (below signal line by {macd_diff_percent:.2f}% and below zero)")
        
        # Additional conditions (need 2 more to meet the 4/5 requirement)
        total_conditions = 4  # EMA, RSI, Volume, ADX
        
        # Check RSI
        if latest['rsi'] < 40:
            conditions_met += 1
            logging.info("  - RSI condition met (below 40)")

        # Check volume conditions
        if self.use_volume_filter:
            if latest['volume'] > latest['volume_ma'] * 1.2:
                conditions_met += 1
                logging.info("  - Volume condition met")
        
        # Check ADX
        if self.use_adx:
            if latest['adx'] >= 25:
                conditions_met += 1
                logging.info("  - ADX condition met (above 25)")
        
        # Check Squeeze Momentum (optional)
        if self.use_squeeze_exit:
            if latest['squeeze_momentum'] < 0:
                conditions_met += 1
                logging.info("  - Squeeze momentum condition met")
        
        logging.info(f"  - Additional conditions met: {conditions_met}/{total_conditions}")
        # Need at least 2 more conditions (total of 4 including mandatory MACD)
        return conditions_met >= 2

    def _check_trend_conditions(self, df: pd.DataFrame) -> bool:
        latest = df.iloc[-1]
        if self.use_long_trend and latest['close'] <= latest['sma']:
            return False
        if self.use_short_trend and latest['close'] >= latest['sma']:
            return False
        return True

    def _check_macd_conditions(self, df: pd.DataFrame) -> bool:
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        return (latest['macd'] > latest['signal'] and prev['macd'] <= prev['signal']) or \
               (latest['macd'] < latest['signal'] and prev['macd'] >= prev['signal'])

    def _check_rsi_conditions(self, df: pd.DataFrame) -> bool:
        latest = df.iloc[-1]
        return latest['rsi'] > self.rsi_long_boundary or latest['rsi'] < self.rsi_short_boundary

    def _check_volume_conditions(self, df: pd.DataFrame) -> bool:
        if not self.use_volume_filter:
            return True
        latest = df.iloc[-1]
        return latest['volume'] > latest['volume_ma']

    def _check_adx_conditions(self, df: pd.DataFrame) -> bool:
        latest = df.iloc[-1]
        return latest['adx'] >= 20  # Changed to only check if ADX is below 20

    def _calculate_confidence_score(self, df: pd.DataFrame, trend_direction: TradeDirection) -> Dict[str, Any]:
        """Calculate confidence score for the current market conditions"""
        latest = df.iloc[-1]
        
        indicators = {
            'adx': latest['adx'],
            'macd': {
                'macd': latest['macd'],
                'signal': latest['signal']
            },
            'ema': {
                'price': latest['close'],
                'ema_50': latest['ema_50'],
                'ema_200': latest['ema_200']
            },
            'volume': {
                'current': latest['volume'],
                'average': latest['volume_ma']
            },
            'rsi': latest['rsi'],
            'trend_direction': trend_direction
        }
        
        return self.confidence_scorer.calculate_overall_score(indicators)

    def backtest(self, market: Market, start_date: datetime, end_time: datetime) -> BacktestResult:
        return BacktestResult() 