from enum import Enum
from typing import TypeVar, Union

from ..configuration import Configuration
from ..broker.abstract_interfaces import AccountInterface
from ..broker.abstract_interfaces import StocksInterface
from ..broker.abstract_interfaces import IGInterface
from ..broker.abstract_interfaces import AVInterface
from ..broker.abstract_interfaces import YFinanceInterce
    
    


AccountInterfaceImp1 = TypeVar("AccountInterfaceImp1", bound= AccountInterface)
StocksInterfaceImp1 = TypeVar("StocksInterfaceImp1", bound= StocksInterface )
BrokerInterfaces = Union[AccountInterfaceImp1, StocksInterfaceImp1]

class InterfaceNames(Enum):
    IG_INDEX = "ig_interface"
    ALPHA_INDEX = "alpha_vantage"
    YAHOO_FINANCE = "yfinance"


class BrokerFactory:
    config = Configuration

    def __init__(self, config: Configuration) -> None:
        self.config = config
    
    def make(self, name: str) -> BrokerInterfaces:
        if name == InterfaceNames.IG_INDEX.value:
            return IGInterface(self.config)
        elif name == InterfaceNames.ALPHA_INDEX.value:
            return AVInterface(self.config)
        elif name == InterfaceNames.YAHOO_FINANCE.value:
            return YFinanceInterce(self.config)
        else: 
            raise ValueError("Interface {} not supported".format(name))
        
    def make_stock_interface_from_config(
            self,
    ) -> BrokerInterfaces:
        return self.make(self.config.get_active_account_interface())
    
    def make_account_interface_from_config(
        self,    
    ) -> BrokerInterfaces:
        return self.make(self.config.get_active_account_interface())