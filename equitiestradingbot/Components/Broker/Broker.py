from typing import Any, Dict, List, Optional

from ...Interfaces import Market, MarketHistory, MarketMACD, Position
from ..utils import Interval, TradeDirection
from . import AccountInterface, BrokerFactory, StocksInterface

class Broker:
    """This class provides a template interface for all broker related 
    actions/takes wrapping the actua; implementation class internally
    """
    factory: BrokerFactory
    stocks_ifc: StocksInterface
    account_ifc: AccountInterface

    