from .base import (
    BacktestResult,
    DataPoints,
    Strategy,
    TradeSignal,
)
from .simple_macd import SimpleMACD
from .weighted_avg_peak import WeightedAvgPeak
from .simple_bollinger_bands import SimpleBollingerBands
from .factories import(
    StrategyFactory,
    StrategyNames,
    StrategyImp1,
)