import logging
from datetime import datetime
import pandas
import yfinance as yf

from ..components.configuration import Configuration
from ..components.utils import Interval, TradeDirection, Utils
from ..components.broker.broker import Broker
from ..interfaces import Market, MarketHistory
from .base import BacktestResult, Strategy, TradeSignal

class SmartSentimentStrategy(Strategy):
    def __init__(self, config: Configuration, broker: Broker) -> None:
        super().__init__(config, broker)
        logging.info("Smart Sentiment Strategy created")

    def read_configuration(self, config: Configuration) -> None:
        raw = config.get_raw_config()
        self.window = raw["strategies"]["smart_sentiment"]["window"]
        self.limit_p = raw["strategies"]["smart_sentiment"]["limit_perc"]
        self.stop_p = raw["strategies"]["smart_sentiment"]["stop_perc"]
        self.lower_tf = Interval.MINUTE_15   # Entry Timeframe
        self.upper_tf = Interval.HOUR        # Trend Timeframe
        self.ema_window = 200                # For 200 EMA

    def initialise(self) -> None:
        logging.info("Smart Sentiment Strategy initialised")

    def fetch_datapoints(self, market: Market) -> tuple[MarketHistory, MarketHistory]:
        entry_data = self.broker.get_prices(market, self.lower_tf, self.window * 2)
        trend_data = self.broker.get_prices(market, self.upper_tf, self.window * 2)
        return entry_data, trend_data
    
    def find_trade_signal(self, market: Market, datapoints: tuple[MarketHistory, MarketHistory]) -> TradeSignal:
        entry_data, trend_data = datapoints
        df = entry_data.dataframe[: self.window * 2].copy()
        trend_df = trend_data.dataframe[: self.ema_window].copy()

        # === Calculate MACD & RSI ===
        df = self._calculate_macd_rsi(df)

        # === Calculate EMA200 on higher timeframe ===
        trend_df["ema_200"] = trend_df[MarketHistory.CLOSE_COLUMN].ewm(span=self.ema_window, adjust=False).mean()
        is_bullish = trend_df[MarketHistory.CLOSE_COLUMN].iloc[0] > trend_df["ema_200"].iloc[0]

        # === Entry Signal: MACD Cross + RSI Confirmation ===
        macd_trending_up = df["macd"].iloc[0] > df["macd"].iloc[1]
        macd_trending_down = df["macd"].iloc[0] < df["macd"].iloc[1]
        rsi = df["rsi"].iloc[0]

        if is_bullish and macd_trending_up and rsi < 70:
            return self._buy_signal(market)
        elif not is_bullish and macd_trending_down and rsi > 30:
            return self._sell_signal(market)
        else:
            logging.info(f"No signal found for {market.epic}")
            return TradeDirection.NONE, None, None
        

    def _calculate_macd_rsi(self, df: pandas.DataFrame) -> pandas.DataFrame:
        fast = 12
        slow = 26
        signal = 9
        rsi_period = 14

        df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
        df["macd"] = df["ema_fast"] - df["ema_slow"]
        df["signal"] = df["macd"].ewm(span=signal, adjust=False).mean()

        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=rsi_period).mean()
        avg_loss = loss.rolling(window=rsi_period).mean()
        rs = avg_gain / avg_loss
        df["rsi"] = 100 - (100 / (1 + rs))

        return df

    def _buy_signal(self, market: Market) -> TradeSignal:
        direction = TradeDirection.BUY
        limit = market.offer + Utils.percentage_of(self.limit_p, market.offer)
        stop = market.bid - Utils.percentage_of(self.stop_p, market.bid)
        return direction, limit, stop

    def _sell_signal(self, market: Market) -> TradeSignal:
        direction = TradeDirection.SELL
        limit = market.bid - Utils.percentage_of(self.limit_p, market.bid)
        stop = market.offer + Utils.percentage_of(self.stop_p, market.offer)
        return direction, limit, stop

    def backtest(self, market: Market, start_date: datetime, end_time: datetime) -> BacktestResult:
        return BacktestResult()

    