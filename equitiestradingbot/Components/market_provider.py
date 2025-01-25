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

    def reset(self) -> None:
        """
        Reset internal market pointer to the beginning
        """
        logging.info("Resetting MarketProvider")
        self._initialise()

    def get_market_from_epic(self, epic: str) -> Market:
        """
        The given epic Id returns a selected market snapshop
        """
        return self._create_market(epic)
    
    def search_market(self, search: str) -> Market: 
        """
        Finds the market which the id matches the string inputted
        If it is successfull, market snapshot returns.
        Raises a exception if there are multiple markets which match the seach string
        """
        markets = self.broker.search_market(search)
        if markets is None or len(markets) < 1: 
            raise RuntimeError(
                "ERROR : Unable to find a market matching: {}".format(search)
            )
        else:
            #Iterate through the list and use a set to verify that results are all same market
            epic_set = set()
            for m in markets:
                #epic are in formate: KC.D.PRSMLN.DAILY.IP. Extract third element
                market_id = m.epic.split(".")[2]
                # Store in DFB epic
                if "DFB" in m.expiry and "DAILY" in m.epic:
                    epic_set.add(market_id)
            if not len(epic_set) == 1:
                raise RuntimeError(
                    "ERROR: Multiple markets match the seach string: {}".format(search)
                )
            return markets[0]
        
        def _initialise(self) -> None:
            #Initialise epic list
            self.epic_list = []
            self.epic_list_iter = iter([])
            self.market_list_iter = iter([])
            #Initialise API members 
            self.node_stack = deque()
            source = self.config.get_active_market_source()
            if source == MarketSource
