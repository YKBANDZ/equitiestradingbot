import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from ..components import Configuration, TradeDirection
from ..components.broker.broker import Broker
from ..interfaces import Market, Position

DataPoints = Any
BacktestResult = Dict[str, Union[float, List[Tuple[str, TradeDirection, float]]]]
TradeSignal = Tuple[TradeDirection, Optional[float], Optional[float]]

class Strategy(ABC):
    """
    Generic strategy template to use a parent class for custom strategies 
    """

    positions: Optional[List[Position]] = None
    broker: Broker

    def __init__(self, config: Configuration, broker: Broker) -> None:
        self.positions = None
        self.broker = broker
        # Read configuration of derived Strategy
        self.read_configuration(config)
        # Initialise derived strategy 
        self.initialise()

    def set_open_positions(self, positions: List[Position]) -> None:
        """
        Set the account open positions
        """
        self.positions = positions

    def run(self, market: Market) -> TradeSignal:
        """
        Run the strategy against the specified market
        """
        logging.info(f"Strategy run starting for market: {market.id} ({market.name if hasattr(market, 'name') else 'unnamed'})")
        datapoints = self.fetch_datapoints(market)
        
        if datapoints is None:
            logging.info(f"Unable to fetch market datapoints for {market.id}")
            return TradeDirection.NONE, None, None
            
        logging.info(f"Strategy datapoints fetched for {market.id}, calling find_trade_signal")
        result = self.find_trade_signal(market, datapoints)
        logging.info(f"Strategy find_trade_signal completed for {market.id}. Result: {result}")
        
        return result
    
    ##############################################################
    # OVERRIDE THESE FUNCTIONS IN STRATEGY IMPLEMENTATION
    ##############################################################

    @abstractmethod
    def initialise(self) -> None:
        pass

    @abstractmethod
    def read_configuration(self, config: Configuration) -> None:
        pass

    @abstractmethod
    def fetch_datapoints(self, market: Market) -> DataPoints:
        pass

    @abstractmethod
    def find_trade_signal(self, market: Market, datapoints: DataPoints) -> TradeSignal:
        pass

    @abstractmethod
    def backtest(
        self, market: Market, start_date: datetime, end_time: datetime
    ) -> BacktestResult:
        pass