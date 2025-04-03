from enum import Enum
from typing import TypeVar, Union

from ..configuration import Configuration
from .abstract_interfaces import AccountInterface, StocksInterface
from .ig_interface import IGInterface
from .av_interface import AVInterface
from .yf_interface import YFinanceInterface
    
AccountInterfaceImp1 = TypeVar("AccountInterfaceImp1", bound=AccountInterface)
StocksInterfaceImp1 = TypeVar("StocksInterfaceImp1", bound=StocksInterface)
BrokerInterfaces = Union[AccountInterfaceImp1, StocksInterfaceImp1]

class InterfaceNames(Enum):
    IG_INDEX = "ig_interface"
    ALPHA_VANTAGE = "alpha_vantage"
    YAHOO_FINANCE = "yfinance"


class BrokerFactory:
    config: Configuration

    def __init__(self, config: Configuration) -> None:
        self.config = config
    
    def make(self, name: str) -> Union[IGInterface, AVInterface, YFinanceInterface]:
        if name == InterfaceNames.IG_INDEX.value:
            return IGInterface(self.config)
        elif name == InterfaceNames.ALPHA_VANTAGE.value:
            return AVInterface(self.config)
        elif name == InterfaceNames.YAHOO_FINANCE.value:
            return YFinanceInterface(self.config)
        else: 
            raise ValueError("Interface {} not supported".format(name))
        
    def make_stock_interface_from_config(self) -> StocksInterface:
        return self.make(self.config.get_active_stocks_interface())
    
    def make_account_interface_from_config(self) -> AccountInterface:
        return self.make(self.config.get_active_account_interface())