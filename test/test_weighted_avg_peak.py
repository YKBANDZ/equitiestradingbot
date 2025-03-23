from pathlib import Path

import pytest
from common.MockRequests import (
    ig_request_confirm_trade,
    ig_request_trade,
    ig_request_market_info,
    ig_request_prices,
    ig_request_set_account,
    ig_request_trade,
)

from equitiestradingbot.components import Configuration, TradeDirection
from equitiestradingbot.components.broker.broker import Broker, BrokerFactory
from equitiestradingbot.strategies import WeightedAvgPeak


@pytest.fixture
def config():
    config = Configuration.from_filepath(Path("test/test_data/demo_trading_bot.toml"))
    config.config["strategies"]["active"] = "weighted_avg_peak"
    return config

@pytest.fixture
def broker(config, requests_mock):
    """
    Initialise the strategy with mock services
    """
    ig_request_login 