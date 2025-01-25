import logging
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Deque, Iterator, List

from ..Interfaces import Market
from . import Configuration
from .Broker import Broker


class MarketSource(Enum):
    """Available Market sources: First you'll find out market sources for robinhood.But for
    IG market sources are: locol file list, watchlist, market navigation through API
    """

    LIST = "list"
    WATCHLIST = "watchlist"
    API = "api"


class MarketProvider: 
    """Provide markets from different sources based on config.
    Supports market lists, dynamic market exploration or watchlists
    """

    config: Configuration
    broker: Broker
    epic_list: List[str] = []
    epic_list_iter: Iterator[str]
    market_list_iter: Iterator[Market]
    node_stack: Deque[str]

    def __init__(self, config: Configuration, broker: Broker) -> None:
        self.config = config
        self.broker = broker
        self._initialise()

    def next(self) -> Market:
        """
        Return the next market from the configured source
        """
        source = self.config.get_active_market_source()
        if source == MarketSource.LIST.value:
            return self._next_from_epic_list()
        elif source == MarketSource.WATCHLIST.value:
            return self._next_from_market_list()
        elif source == MarketSource.API.value:
            return self._next_from_api()
        else:
            raise RuntimeError("ERROR: invalid market_source configuration")

    