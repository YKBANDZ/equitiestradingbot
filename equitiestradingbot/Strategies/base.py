import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from ..Components import Configuration
from ..Components.utils import TradeDirection
from ..Components.Broker import broker
from ..Interfaces import Market, Position

DataPoints = Any
BacktestResult = Dict[str, Union[float, List[Tuple[str, TradeDirection, float]]]]
TradeSignal = Tuple[TradeDirection, Optional[float], Optional[float]]

class Strategy(ABC):
    """
    Generic strategy template to use a parent class for custom strategies 
    """

    positions: Optional[List[Position]] = None
    broker: broker