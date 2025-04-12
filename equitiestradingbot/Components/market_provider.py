import logging
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Deque, Iterator, List

from ..interfaces import Market
from .configuration import Configuration
from .broker.broker import Broker
from .broker.av_interface import AVInterface


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
        Tries to find the market which id matches the given search string.
        If successful return the market snapshot
        Raise an exception when the multiple markets match the search string
        """
        markets = self.broker.search_market(search)
        if markets is None or len(markets) < 1: 
            raise RuntimeError(
                "ERROR : Unable to find a market matching: {}".format(search)
            )
        else:
            # Iterate through the list and use a set to verify that the results are all the same market
            epic_set = set()
            for m in markets:
                # Epic are format: KC.D.PRSMLN.DAILY.IP. Extract the third element
                market_id = m.epic.split(".")[2]
                #Strore the DFB epic
                if "DFB" in m.expiry and "DAILY" in m.epic:
                    epic_set.add(market_id)
            if not len(epic_set) == 1:
                raise RuntimeError(
                    "Error: Multiple markets match the same search string: {}".format(search)
                )
            # Results in the same market
            return markets[0]
        
    def _initialise(self) -> None:
        #Initialise epic list
        self.epic_list = []
        self.epic_list_iter = iter([])
        self.market_list_iter = iter([])
        #Initialise API members 
        self.node_stack = deque()
        source = self.config.get_active_market_source()
        if source == MarketSource.LIST.value:
            self.epic_list = self._load_epic_ids_from_local_file(
                Path(self.config.get_epic_ids_filepath())
            )
        elif source == MarketSource.WATCHLIST.value:
            market_list = self._load_markets_from_watchlist(
                self.config.get_watchlist_name()
            )
            self.market_list_iter = iter(market_list)
        elif source == MarketSource.API.value:
            if isinstance(self.broker.stocks_ifc, AVInterface):
                # For Alpha Vantage, we don't use the node navigation system
                self.epic_list = []
                self.node_stack = deque()  # Empty stack since we don't use nodes
            else:
                # For other APIs like IG that use node navigation
                #start with empty node ID to get root nodes
                logging.info("Starting API navigation from root node")
                self.epic_list = self._load_epic_ids_from_api_node("")
        else:
            raise RuntimeError("Error: invalid market_source configuration")
        self.epic_list_iter = iter(self.epic_list)

    def _load_epic_ids_from_local_file(self, filepath: Path) -> List[str]:
        """
        Read a file from filesystem containing a list of epic ids.
        The filepath is defined in the configuration file
        Returns a 'list' of strings where each string is a market epic 
        """
        # define empty list
        epic_ids = []
        try:
            # open file and read the content in a list
            with filepath.open(mode="r") as f:
                filecontents = f.readlines()
                for line in filecontents:
                    # remove linebreak which is in the last character of the string
                    current_epic_id = line[: -1]
                    epic_ids.append(current_epic_id)
        except IOError:
            # Create the file empty
            logging.error("{} does not exist!".format(filepath))
        if len(epic_ids) < 1:
            logging.error("Error list is empty!")
        return epic_ids
    
    def _next_from_epic_list(self) -> Market:
        try:
            epic = next(self.epic_list_iter)
            return self._create_market(epic)
        except Exception:
            raise StopIteration
        
    def _next_from_market_list(self) -> Market:
        try:
            return next(self.market_list_iter)
        except Exception:
            raise StopIteration
        
    def _load_markets_from_watchlist(self, watchlist_name: str) -> List[Market]:
        markets = self.broker.get_markets_from_watchlist(
            self.config.get_watchlist_name()
        )
        if markets is None:
            message = "Watchlist {} not found!".format(watchlist_name)
            logging.error(message)
            raise RuntimeError(message)
        return markets
    
    def _load_epic_ids_from_api_node(self, node_id: str) -> List[str]:
        logging.info(f"Navigating market node: {node_id}")
        node = self.broker.navigate_market_node(node_id)
        
        # Log response structure 
        if "nodes" in node and isinstance(node["nodes"], list):
            logging.info(f"Found {len(node['nodes'])} sub-nodes")
            for sub_node in node["nodes"]:
                logging.info(f"Adding node {sub_node['id']} ({sub_node.get('name', 'unnamed')}) to stack")
                self.node_stack.append(sub_node["id"])
            return self._load_epic_ids_from_api_node(self.node_stack.pop())    
        
        if "markets" in node and isinstance(node["markets"], list):
            epics = [
                market["epic"]
                for market in node["markets"]
                if any(
                    [
                        "DFB" in str(market["epic"]),
                        "TODAY" in str(market["epic"]),
                        "DAILY" in str(market["epic"]),
                    ]
                )
            ]
            logging.info(f"Found {len(epics)} matching markets")
            return epics
    
        logging.warning(f"Node {node_id} contains no nodes or markets")
        return []
            
    def _next_from_api(self) -> Market:
        # For Alpha Vantage, we don't support iterating through markets
        if isinstance(self.broker.stocks_ifc, AVInterface):
            raise StopIteration("Market iteration not supported for Alpha Vantage")
            
        # For other APIs that use node navigation
        try:
            return self._next_from_epic_list()
        except Exception:
            if not self.node_stack:  # Check if node_stack is empty
                raise StopIteration("No more nodes to process")
            self.epic_list = self._load_epic_ids_from_api_node(self.node_stack.pop())
            self.epic_list_iter = iter(self.epic_list)
            return self._next_from_epic_list()
        
    def _create_market(self, epic_id: str) -> Market:
        market = self.broker.get_market_info(epic_id)
        if market is None:
            raise RuntimeWarning("Unable to fetch data for {}".format(epic_id))
        return market

                                                                        
        
        
