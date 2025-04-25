import datetime
import logging
from typing import Tuple
import traceback

import numpy as np
import pandas

from ..components.configuration import Configuration
from ..components.utils import Interval, TradeDirection, Utils
from ..components.broker.broker import Broker
from ..interfaces import Market, MarketMACD, MarketHistory
from .base import BacktestResult, Strategy, TradeSignal


class SimpleMACD(Strategy):
    """
    Strategy that uses the MACD Technical inidicator of a market
    to buy, sell or hold
    Buy when the MACD cross over the MACD signal.
    Sell when the MACD cross below the MACD signal 
    """

    def __init__(self, config: Configuration, broker:Broker) -> None:
        super().__init__(config, broker)
        logging.info("Simple MACD strategy initialised.")

    def read_configuration(self, config: Configuration) -> None:
        """
        Read the json configuration 
        """
        raw = config.get_raw_config()
        self.max_spread_perc = raw["strategies"]["simple_macd"]["max_spread_perc"]
        self.limit_p = raw["strategies"]["simple_macd"]["limit_perc"]
        self.stop_p = raw["strategies"]["simple_macd"]["stop_perc"]

    def initialise(self) -> None:
        """
        Initialise SimpleMACD Strategy
        """
        pass

    def fetch_datapoints(self, market: Market) -> MarketMACD:
        """
        Fetch historic MACD data
        """
        logging.info(f"Fetching MACD data for {market.id}")
        try:
            macd_data = self.broker.get_macd(market, Interval.DAY, 30)
            if macd_data is None or macd_data.dataframe is None or macd_data.dataframe.empty:
                logging.warning(f"Retrieved empty MACD data for {market.id}")
            else:
                logging.info(f"Successfully retrieved MACD data for {market.id} with {len(macd_data.dataframe)} datapoints")
            return macd_data
        except Exception as e:
            logging.error(f"Error fetching MACD data for {market.id}: {str(e)}")
            logging.error(traceback.format_exc())
            return MarketMACD(market, [], [], [], [])
    
    def find_trade_signal(self, market: Market, datapoints: MarketMACD) -> TradeSignal:
        """
        Calculate that MACD of the previous days and find a 
        cross between MACD and MACD signal
            - **market**: Market Object
            - **datapoints**: datapoints used to analyse the market
            - Returns TradeDirection, limit_level, stop_level or TradeDirection.NONE, None, None
        """
        limit_perc = self.limit_p
        stop_perc = max(market.stop_distance_min, self.stop_p)
        
        logging.info(f"Analyzing market: {market.id} ({market.name})")
        logging.info(f"Current bid: {market.bid}, offer: {market.offer}, spread: {market.offer - market.bid}")
        logging.info(f"Stop distance min: {market.stop_distance_min}, using stop_perc: {stop_perc}")

        # Spread constraint
        if market.bid - market.offer > self.max_spread_perc:
            logging.info(f"Market {market.id} exceeded max spread: {market.bid - market.offer} > {self.max_spread_perc}")
            return TradeDirection.NONE, None, None
        
        #Find where macd and signal cross each other
        macd = datapoints
        
        # Check if we have MACD data
        if macd.dataframe is None or macd.dataframe.empty:
            logging.warning(f"No MACD data available for {market.id}")
            return TradeDirection.NONE, None, None
            
        # Log MACD data points
        if not macd.dataframe.empty:
            last_row = macd.dataframe.iloc[-1]
            prev_row = macd.dataframe.iloc[-2] if len(macd.dataframe) > 1 else None
            
            logging.info(f"Latest MACD value: {last_row.get(MarketMACD.MACD_COLUMN)}, Signal: {last_row.get(MarketMACD.SIGNAL_COLUMN)}")
            logging.info(f"Latest MACD histogram: {last_row.get(MarketMACD.HIST_COLUMN)}")
            
            if prev_row is not None:
                logging.info(f"Previous MACD value: {prev_row.get(MarketMACD.MACD_COLUMN)}, Signal: {prev_row.get(MarketMACD.SIGNAL_COLUMN)}")
                logging.info(f"Previous MACD histogram: {prev_row.get(MarketMACD.HIST_COLUMN)}")
        
        px = self.generate_signals_from_dataframe(macd.dataframe)
        
        # Log the generated signals
        if not px.empty:
            logging.info(f"Generated positions: {px['positions'].tail(3).values}")
            logging.info(f"Generated signals: {px['signals'].tail(3).values}")

        # Identify the trade direction looking at the last signal
        tradeDirection = self.get_trade_direction_from_signals(px)
        
        # Log the trade decision
        if tradeDirection is TradeDirection.NONE:
            logging.info(f"No trade signal found for {market.id}")
            return TradeDirection.NONE, None, None
        
        # Log only tradable epics
        logging.info(
            "SimpleMACD says: {} {}".format(tradeDirection.name, market.id)
        )
        
        # calclulate stop and limit distances
        limit, stop = self.calculate_stop_limit(
            tradeDirection, market.offer, market.bid, limit_perc, stop_perc
        )
        
        logging.info(f"Trade signal for {market.id}: Direction={tradeDirection.name}, Limit={limit}, Stop={stop}")
        
        return tradeDirection, limit, stop
    
    def calculate_stop_limit(
        self,
        tradeDirection: TradeDirection,
        current_offer: float,
        current_bid: float,
        limit_perc: float,
        stop_perc: float,
    ) -> Tuple[float, float]:
        """
        Calculate the stop and the limit levels from the given percentages
        """
        limit = None
        stop = None
        if tradeDirection == TradeDirection.BUY:
            limit = current_offer + Utils.percentage_of(limit_perc, current_offer)
            stop = current_bid - Utils.percentage_of(stop_perc, current_bid)
        elif tradeDirection == TradeDirection.SELL:
            limit = current_bid - Utils.percentage_of(limit_perc, current_bid)
            stop = current_offer + Utils.percentage_of(stop_perc, current_offer)
        else:
            raise ValueError("Trade direction cannot be NONE")
        return limit, stop
    
    def generate_signals_from_dataframe(
            self, dataframe: pandas.DataFrame
    ) -> pandas.DataFrame:
        dataframe.loc[:, "positions"] = 0
        dataframe.loc[:, "positions"] = np.where(
            dataframe[MarketMACD.HIST_COLUMN] >= 0, 1, 0
        )
        dataframe.loc[:, "signals"] = dataframe["positions"].diff()
        return dataframe
    
    def get_trade_direction_from_signals(
        self, dataframe: pandas.DataFrame
    ) -> TradeDirection:
        tradeDirection = TradeDirection.NONE
        if len(dataframe["signals"]) > 1:  # Make sure we have at least 2 signals
            last_signal = dataframe["signals"].iloc[-1]  # Get the most recent signal
            if last_signal < 0:
                tradeDirection = TradeDirection.BUY
            elif last_signal > 0:
                tradeDirection = TradeDirection.SELL
        return tradeDirection
    
    def backtest(
        self, market: Market, start_date: datetime.datetime, end_date: datetime.datetime
    ) -> BacktestResult:
        """Backtest the strategy"""
        logging.info(f"Starting backtest for {market.id} from {start_date} to {end_date}")
        # TODO
        # Generic initialisations
        trades = []
        # - Get price data for market
        prices = self.broker.get_prices(market, Interval.DAY, None)
        # - Get macd data from broker
        data = self.fetch_datapoints(market)
        # Simulate time passing by starting with N rows (from the bottom)
        # and adding the next row (on the top) one by one, calling the strategy with
        # the intermediate data and recording its output
        datapoint_used = 26
        while len(data.dataframe) > datapoint_used:
            current_data = data.dataframe.tail(datapoint_used).copy()
            datapoint_used += 1
            # Get trade date
            trade_dt = current_data.index.values[0].astype("M8[ms]").astype("0")
            if start_date <= trade_dt <= end_date:
                trade, limit, stop = self.find_trade_signal(market, current_data)
                if trade is not TradeDirection.NONE:
                    try:
                        price = prices.loc[trade_dt.strftime("%Y-%m-%d"), "4. close"]
                        trades.append(
                            (trade_dt.strftime("%Y-%m-%d"), trade, float(price))
                        )
                    except Exception as e:
                        logging.debug(e)
                        continue
        if len(trades) < 2:
            raise Exception("Not enough trades for the given data range")
        # Iterate through trades and assess profit loss
        balance = 1000
        previous = trades[0]
        for trade in trades[1:]:
            if previous[1] is trade[1]:
                raise Exception("Error: sequencial trades with same direction")
            diff = trade[2] - previous[2]
            pl = 0
            if previous[1] is TradeDirection.BUY and trade[1] is TradeDirection.SELL:
                # Check if the trade ht the stop or the limit level
                if trade[2] <= previous[3]: #Hit the stop loss
                    pl = previous[3] - previous[2]
                elif trade[2] >= previous[4]: #Hit the limit level
                    pl = previous[4] - previous[2]
                else:
                    pl = diff
            elif previous[1] is TradeDirection.SELL and TradeDirection.BUY:
                # Check if the trade hit the stop or limit level
                if trade[2] >= previous[3]: # Hit the stop loss
                    pl = previous[2] - previous[3]
                elif trade[2] <= previous[4]: # Hit the limit level
                    pl = previous[2] - previous[4]
                else:
                    pl = diff
            balance += pl
            previous = trade
        return {"balance": balance, "trades": trades}
       
        

        
    
