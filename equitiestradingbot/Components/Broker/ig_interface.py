import json
import logging
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas
import requests

from ...interfaces import Market, MarketHistory, MarketMACD, Position
from ..utils import Interval, TradeDirection, Utils
from .abstract_interfaces import AccountBalances, AccountInterface, StocksInterface


class IG_API_URL(Enum):
    """
    IG REST API urls
    """

    BASE_URI = "https://api.ig.com/gateway/deal"
    DEMO_PREFIX = "demo-"
    SESSION = "session"
    ACCOUNTS = "accounts"
    POSITIONS = "positions"
    POSITIONS_OTC = "positions/otc"
    MARKET = "markets"
    PRICES = "prices"
    CONFIRMS = "confirms"
    MARKET_NAV = "marketnavigation"
    WATCHLISTS = "watchlists"


class IGInterface(AccountInterface, StocksInterface):
    """
    IG interface class provides function to use the IG REST API
    """
    
    api_base_url: str
    authenticated_headers: Dict[str, str]

    def initialise(self) -> None:
        logging.info("Initialising IGInterface...")
        demoPrefix = (
            IG_API_URL.DEMO_PREFIX.value
            if self._config.get_ig_use_demo_account()
            else ""
        )
        self.api_base_url = IG_API_URL.BASE_URI.value.replace("api", f"{demoPrefix}api")
        self.authenticated_headers = {}
        if self._config.is_paper_trading_enabled():
            logging.info("Paper Trading is active")
        if not self.authenticate():
            logging.error("Authentication failed")
            raise RuntimeError("Unable to authenticate to IG index. Check credentials")
        
    def authenticate(self) -> bool: 
        """
        Authenticate the IGInterface instance with the configured credentials
        """
        data = {
            "identifier": self._config.get_credentials()["username"],
            "password": self._config.get_credentials()["password"],
        }
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json; charset=utf-8",
            "X-IG-API-KEY": self._config.get_credentials()["api_key"],
            "Version": "2",
        }

        url = "{}/{}".format(self.api_base_url, IG_API_URL.SESSION.value)
        response = requests.post(url, data=json.dumps(data), headers=headers)

        if response.status_code != 200:
            logging.debug(
                "Authentication returned code: {}".format(response.status_code)
            )
            return False
        
        headers_json = dict(response.headers)
        try:
            CST_token = headers_json["CST"]
            x_sec_token = headers_json["X-SECURITY-TOKEN"]
        except Exception as e:
            logging.error(f"Failed to get authentication tokens: {str(e)}")
            return False
        
        self.authenticated_headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json; charset=utf-8",
            "X-IG-API-KEY": self._config.get_credentials()["api_key"],
            "CST": CST_token,
            "X-SECURITY-TOKEN": x_sec_token,
        }

        return self.set_default_account(self._config.get_credentials()["account_id"])
    
    def set_default_account(self, account_id: str) -> bool:
        """
        Set the IG account to use
            -**account_id**: String representing the account id in use
            - returns **false** if an error occurs otherwise True
        """
        url = "{}/{}".format(self.api_base_url, IG_API_URL.SESSION.value)
        data = {"accountId": account_id, "defaultAccount": True}  # Changed "True" string to True boolean
        response = requests.put(
            url, data=json.dumps(data), headers=self.authenticated_headers
        )

        if response.status_code == 412:
            # 412 means account is already default, which is fine
            logging.info("Account is already set as default")
            return True
        elif response.status_code != 200:
            logging.error(f"Failed to set default account: {response.status_code}")
            return False
        
        logging.info("Default IG account set")
        return True
    
    def get_account_balances(self) -> AccountBalances:
        """
        Retuns a tuple (balance, deposit) for the account in use
            - Returns **(None, None)** if an error occurs. Otherwise
            (balance, deposit)
            """
        
        url = "{}/{}".format(self.api_base_url, IG_API_URL.SESSION.value)
        d = self._http_get(url)
        if d is not None: 
            try:
                for i in d["accounts"]:
                    if str(i["accountType"]) == "SPREADBET":
                        balance = i["balance"]["balance"]
                        deposit = i["balance"]["deposit"]
                        return balance, deposit
            except Exception:
                return None, None
        return None, None
    
    def get_open_positions(self) -> List[Position]:
        """Returns the account open positions
        
            - Returns a list of Position objects
        """
        url = "{}/{}".format(self.api_base_url, IG_API_URL.POSITIONS.value)
        data = self._http_get(url)
        if not data or "positions" not in data:
            logging.error("Failed to get positions data")
            return []
            
        positions = []
        for d in data["positions"]:
            try:
                position_data = d.get("position", {})
                positions.append(
                    Position(
                        deal_id=position_data.get("dealId"),
                        size=position_data.get("size"),
                        create_date=position_data.get("createDateUTC"),
                        direction=TradeDirection[position_data.get("direction", "NONE")],
                        level=position_data.get("level"),
                        limit=position_data.get("limitLevel"),
                        stop=position_data.get("stopLevel"),
                        currency=position_data.get("currency"),
                        epic=position_data.get("epic"),
                        market_id=None,
                    )
                )
            except (KeyError, ValueError) as e:
                logging.error(f"Error processing position data: {str(e)}")
                continue
        return positions
    
    def get_position_map(self) -> Dict[str, int]:
        """
        Returns a dict containing the account open positions
        in the form {string: int} where the string is defined as 
        'marketId-tradeDirection' and the int is the trade size
            - Returns empty dict if an error occurs otherwise a dict(string:int)
        """
        position_map: Dict[str, int] = {}
        for item in self.get_open_positions():
            if not item.epic or not item.direction:
                continue
            key = f"{item.epic}-{item.direction.name}"
            position_map[key] = item.size + position_map.get(key, 0)
        return position_map
    
    def get_market_info(self, epic_id: str) -> Market:
        """
        Returns info for the given market including a price snapshot
        
            -**epic_id**: market epic as string
            - Return **None** if an error otherwise the json returned by IG API
            """
        url = "{}/{}/{}".format(self.api_base_url, IG_API_URL.MARKET.value, epic_id)
        info = self._http_get(url)

        if "markets" in info:
            raise RuntimeError("Multiple matches found for the epic: {}".format(epic_id))
        if self._config.get_ig_controlled_risk():
            info["minNormalStopOrLimitDistance"] = info["MinControlledStopRiskStopDistance"]
        market = Market()
        market.epic = info["instrument"]["epic"]
        market.id = info["instrument"]["marketId"]
        market.name = info["instrument"]["name"]
        market.bid = info["snapshot"]["bid"]
        market.offer = info["snapshot"]["offer"]
        market.high = info["snapshot"]["high"]
        market.low = info["snapshot"]["low"]
        market.stop_distance_min = info["dealinRules"]["minNoramlStopOrLimitDistance"]["value"]

        market.expiry = info["instrument"]["expiry"]
        return market
    
    def search_market(self, search: str) -> List[Market]:
        """
        Returns a list of markets that matched the search string
        """
        url = "{}/{}?searchTerm={}".format(
        self.api_base_url, IG_API_URL.MARKET.value, search
        )
        data = self._http_get(url)
        markets = []
        if data is not None and "markets" in data:
            markets = [self.get_market_info(m["epic"]) for m in data["markets"]]
        return markets

    def get_prices(
        self, market: Market, interval: Interval, data_range: int
    ) -> MarketHistory: 
        url = "{}/{}/{}/{}/{}".format(
            self.api_base_url,
            IG_API_URL.PRICES.value,
            market.epic,
            interval,
            data_range,
        )
        data = self._http_get(url)
        if "allowance" in data: 
            remaining_allowance = data["allowance"]["remainingAllowance"]
            reset_time = Utils.humanize_time(int(data["allowance"]["allowanceExpiry"]))
            if remaining_allowance < 100:
                logging.warning(
                    "Remaining API calls left: {}".format(str(remaining_allowance))
                )
                logging.warning("The to API Key reset: {}".format(str(reset_time)))
        dates = []
        highs = []
        lows = []
        closes = []
        volumes = []
        for price in data["prices"]:
            dates.append(price["snapshotTimeUTC"])
            highs.append(price["highPrice"]["bid"])
            closes.append(price["lowPrice"]["bid"])
            volumes.append(float(price["lastTradedVolume"]))
        history = MarketHistory(market, dates, highs, lows, closes, volumes)
        return history
    
    def trade(
            self, epic_id: str, trade_direction: TradeDirection, limit: float, stop: float
    ) -> bool: 
        """
        Try to open a new trade for the epic_id (instrument)
            
            - **epic_id**: market epic as a string
            - **trade_direction**: Buy or Sell 
            - **limit**: limit level
            - **stop**: stop level
            - Returns **false** if an error occurs otherwise True
        """
        if self._config.is_paper_paper_trading_enabled():
            logging.info(
                "Paper Trade: {} {} with limit={} and stop={}".format(
                    trade_direction.value, epic_id, limit, stop
                )
            )
            return True
        
        url = "{}/{}".format(self.api_base_url, IG_API_URL.POSITIONS_OTC.value)
        data = {
            "direction": trade_direction.value,
            "epic": epic_id,
            "limitLevel": limit,
            "orderType": self._config.get_in_order_type(),
            "size": self._config.get_ig_order_size(),
            "expiry": self._config.get_ig_order_expiry(),
            "guaranteedStop": self._config.get_ig_use_g_stop(),
            "currencyCode": self._config.get_ig_order_currency(),
            "forceOpen": self._config.get_ig_order_force_open(),
            "stopLevel": stop,
        }

        r = requests.post(
            url, data=json.dumps(data), headers=self.authenicated_headers
        )

        if r.status_code != 200:
            return False
        
        d = json.loads(r.text)
        deal_ref = d["dealReference"]
        if self.confirm_order(deal_ref):
            logging.info(
                "Order {} for {} confirmed with limit={} and stop={}".format(
                    trade_direction.value, epic_id, limit, stop
                )
            )
            return True
        else: 
            logging.warning(
                "Trade {} or {} has failed".format(trade_direction.value, epic_id)
            )
            return False
        
    def confirm_order(self, dealRef: str) -> bool:
        """
        Confirm an order from a dealing reference
            
            -**dealRef**: dealing reference to confirm
            - Return **False** if an error occurs otherwise True
         """
        url = "{}/{}/{}".format(self.api_base_url, IG_API_URL.CONFIRMS.value, dealRef)
        d = self._http_get(url)

        if d is not None:
            if d["reason"] !="SUCCESS":
                return False
            else:
                return True
        return False

    def close_position(self, position: Position) -> bool:
        """
        Close the given market position
            
            - **position**: Position object to close
            - Returns **false** if an error occurs otherwise True
        """
        if not position or not position.deal_id:
            logging.error("Invalid position provided")
            return False
            
        if self._config.is_paper_trading_enabled():
            logging.info("Paper trade: close {} position".format(position.epic))
            return True
            
        # To close we need the opposite direction
        direction = TradeDirection.NONE
        if position.direction is TradeDirection.BUY:
            direction = TradeDirection.SELL
        elif position.direction is TradeDirection.SELL:
            direction = TradeDirection.BUY
        else:
            logging.error("Wrong position direction!")
            return False
    
        url = "{}/{}".format(self.api_base_url, IG_API_URL.POSITIONS_OTC.value)
        data = {
            "dealId": position.deal_id,
            "epic": None,
            "expiry": None,
            "direction": direction.name,
            "size": str(position.size),  # Use actual position size
            "level": None,
            "orderType": "MARKET",
            "timeInForce": None,
            "quoteId": None,
        }
        
        del_headers = dict(self.authenticated_headers)
        del_headers["_method"] = "DELETE"
        
        try:
            r = requests.post(url, data=json.dumps(data), headers=del_headers)
            if r.status_code != 200:
                logging.error(f"Failed to close position: {r.status_code}")
                return False
                
            d = json.loads(r.text)
            deal_ref = d.get("dealReference")
            if not deal_ref:
                logging.error("No deal reference received")
                return False
                
            if self.confirm_order(deal_ref):
                logging.info("Position for {} closed".format(position.epic))
                return True
            else:
                logging.error("Could not confirm order closure")
                return False
        except Exception as e:
            logging.error(f"Error closing position: {str(e)}")
            return False
    

    def close_all_positions(self) -> bool:
        """
        Try to close all the account open position.
            
            - Returns **False** if an error occurs otherwise True 
        """
        result = True
        try:
            positions = self.get_open_positions()
            if positions is not None:
                for p in positions:
                    try:
                        if not self.close_position(p):
                            result = False
                    except Exception:
                        logging.error(
                            "Error closing position for {}".format(p.market_id)
                        )
                        result = False
        
            else:
                logging.error("Unable to retrieve open positons!")
                result = False
        except Exception:
            logging.error("Error during close all positions")
            result = False
        return result

    def get_account_used_perc(self) -> Optional[float]:
        """
        Fetch the percentage of available balance is currently used
            - Returns the percentage of account used over total available amount
        """
        balance, deposit = self.get_account_balances()
        if balance is None or deposit is None:
            return None
        return Utils.percentage(deposit, balance)

    def navigate_market_node(self, node_id: str) -> Dict[str, Any]:
        """
        Navigate the market node id
            
            - Returns the json representing the market node
        """
        url = "{}/{}/{}".format(self.api_base_url, IG_API_URL.MARKET_NAV.value, node_id)
        return self._http_get(url)

    def _get_watchlist(self, id: str) -> Dict[str, Any]:
        """
        Get watchlist info
            
            - **id**: id of the watchlist. If empty id is provided, the 
            function returns the lisst of all the watchlist in the account
            """
        url = "{}/{}/{}".format(self.api_base_url, IG_API_URL.WATCHLISTS.value, id)
        return self._http_get(url)

    def get_markets_from_watchlist(self, name: str) -> List[Market]:
        """
        Get the list of markets included in the watchlist
            
            - **name**: name of the watchlist 
        """
        markets = []
        #Request with empty name returns list all the watchlists
        all_watchlists = self._get_watchlist("")
        for w in all_watchlists["watchlists"]:
            if "name" in w and w["name"] == name:
                data = self._get_watchlist(w["id"])
                if "markets" in data:
                    for m in data["markets"]:
                        markets.append(self.get_market_info(m["epic"]))
                break
            return markets
    
    def _http_get(self, url: str) -> Dict[str, Any]:
        """
        Perform an HTTP GET request to the url.
        Return the json object returned from the API if 200 is recieved
        Return None if an error is received from the API
        """
        self._wait_before_call(self._config.get_ig_api_timeout())

        # Add version header for market navigation
        headers = self.authenticated_headers.copy()
        if IG_API_URL.MARKET_NAV.value in url:
            headers["Version"] = "1"

        response = requests.get(url, headers= headers)
        if response.status_code != 200:
            logging.error("HTTP request returned {}".format(response.status_code))
            raise RuntimeError("HTTP request returned {}".format(response.status_code))
        data = json.loads(response.text)
        if "errorCode" in data:
            logging.error(data["errorCode"])
            raise RuntimeError(data["errorCode"])
        return data

    def get_macd(
        self, market: Market, interval: Interval, data_range: int
    ) -> MarketMACD:
        """
        Calculate MACD data from price history
        """
        try:
            # Get price data
            price_data = self.get_prices(market, interval, data_range)
            if price_data is None or price_data.dataframe is None or price_data.dataframe.empty:
                logging.error(f"No price data available for {market.id}")
                return MarketMACD(market, [], [], [], [])

            # Calculate MACD
            df = price_data.dataframe.copy()
            
            # Calculate 12-day EMA
            exp1 = df['4. close'].ewm(span=12, adjust=False).mean()
            
            # Calculate 26-day EMA
            exp2 = df['4. close'].ewm(span=26, adjust=False).mean()
            
            # Calculate MACD line
            macd = exp1 - exp2
            
            # Calculate signal line (9-day EMA of MACD)
            signal = macd.ewm(span=9, adjust=False).mean()
            
            # Calculate histogram
            hist = macd - signal
            
            # Create MACD dataframe
            macd_df = pandas.DataFrame({
                MarketMACD.MACD_COLUMN: macd,
                MarketMACD.SIGNAL_COLUMN: signal,
                MarketMACD.HIST_COLUMN: hist
            }, index=df.index)
            
            return MarketMACD(market, macd_df[MarketMACD.MACD_COLUMN].tolist(),
                            macd_df[MarketMACD.SIGNAL_COLUMN].tolist(),
                            macd_df[MarketMACD.HIST_COLUMN].tolist(),
                            macd_df)
                            
        except Exception as e:
            logging.error(f"Error calculating MACD for {market.id}: {str(e)}")
            return MarketMACD(market, [], [], [], [])




    
