import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from ..components import Configuration
from ..components.utils import TradeDirection
from ..components.Broker import broker
from ..interfaces import Market, Position

DataPoints = Any
BacktestResult = Dict[str, Union[float, List[Tuple[str, TradeDirection, float]]]]
TradeSignal = Tuple[TradeDirection, Optional[float], Optional[float]]

class Strategy(ABC):
    """
    Generic strategy template to use a parent class for custom strategies 
    """

    positions: Optional[List[Position]] = None
    broker: broker