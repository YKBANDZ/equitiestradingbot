from enum import Enum
from typing import Union

from ..components.configuration import Configuration
from ..components.broker.broker import Broker
from .simple_bollinger_bands import SimpleBollingerBands
from .simple_macd import SimpleMACD
from .weighted_avg_peak import WeightedAvgPeak
from .smart_sentiment_strategy import SmartSentimentStrategy
from .advanced_momentum_strategy import AdvancedMomentumStrategy
from .simplicity import SimplicityStrategy
from .intraday_gold_strategy import IntradayGoldStrategy
from .strategy_manager import StrategyManager

StrategyImp1 = Union[SimpleMACD, WeightedAvgPeak, SimpleBollingerBands, SmartSentimentStrategy, AdvancedMomentumStrategy, SimplicityStrategy, IntradayGoldStrategy, StrategyManager]


class StrategyNames(Enum):
    SIMPLE_MACD = "simple_macd"
    WEIGHTED_AVG_PEAK = "weighted_avg_peak"
    SIMPLE_BOLL_BANDS = "simple_boll_bands"
    SMART_SENTIMENT = "smart_sentiment"
    ADVANCED_MOMENTUM = "advanced_momentum"
    SIMPLICITY = "simplicity"
    INTRADAY_GOLD = "intraday_gold"
    STRATEGY_MANAGER = "strategy_manager"


class StrategyFactory:
    """
    Factory class to create instances of Strategies. The class provide an
    interface to instantiate new objects of a given Strategy name
    """

    config: Configuration
    broker: Broker

    def __init__(self, config: Configuration, broker: Broker) -> None:
        """
        Constructor of the StrategyFactory
        
            - **config**: config json used to initialise Strategies
            - **broker**: instance of Broker class strategies
            - Return rthe instance of the StrategyFactory
            """
        self.config = config
        self.broker = broker

    def make_strategy(self, strategy_name: str) -> StrategyImp1:
        """
        Create and return instance of the Strategy class specified 
        by the strategy_name
                - **strategy_name**: name of the strategy as defined in the
                config file
                - Returns an instance of the requested Strategy or None if an error occurs
        """
        if strategy_name == StrategyNames.SIMPLE_MACD.value:
            return SimpleMACD(self.config, self.broker)
        elif strategy_name == StrategyNames.WEIGHTED_AVG_PEAK.value:
            return WeightedAvgPeak(self.config, self.broker)
        elif strategy_name == StrategyNames.SIMPLE_BOLL_BANDS.value:
            return SimpleBollingerBands(self.config, self.broker)
        elif strategy_name == StrategyNames.SMART_SENTIMENT.value:
            return SmartSentimentStrategy(self.config, self.broker)
        elif strategy_name == StrategyNames.ADVANCED_MOMENTUM.value:
            return AdvancedMomentumStrategy(self.config, self.broker)
        elif strategy_name == StrategyNames.SIMPLICITY.value:
            return SimplicityStrategy(self.config, self.broker)
        elif strategy_name == StrategyNames.INTRADAY_GOLD.value:
            return IntradayGoldStrategy(self.config, self.broker)
        elif strategy_name == StrategyNames.STRATEGY_MANAGER.value:
            return StrategyManager(self.config, self.broker)
        else: 
            raise ValueError("Strategy {} does not exist".format(strategy_name))
    

    def make_from_configuration(self) -> StrategyImp1:
        """
        Create and return an instance of the Strategy class as configured in the 
        configuration file
        """
        return self.make_strategy(self.config.get_active_strategy())
        
