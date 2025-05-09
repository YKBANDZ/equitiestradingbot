import logging
import time 
from datetime import datetime
from enum import Enum

import pytz
import pandas_market_calendars as mcal
import pandas as pd

from .utils import Utils


class TimeAmount(Enum):
    """Types of amount of time to wait for"""

    SECONDS = 0
    NEXT_MARKET_OPENING = 1


class TimeProvider:
    """Class that handles functions dependent on actual time
    such as wait, sleep or compute date/time operations
    """

    def __init__(self) -> None:
        logging.debug("TimeProvider __init__")
        # Initialize NYSE calendar
        self.nyse = mcal.get_calendar('NYSE')
        self.gold = mcal.get_calendar('CMEGlobex_Gold')

    def is_market_open(self, timezone: str, epic: str) -> bool:
        """
        Return True if the market is open, false otherwise
        - **timezone**: string representing the timezone
        - **epic**: string representing the epic
        """
        tz = pytz.timezone('America/Chicago') if 'GOLD' in epic else pytz.timezone(timezone)
        now = datetime.now(tz)

        # Choose calender based on instrument
        calender = self.gold if 'GOLD' in epic else self.nyse
        
        # Check if today is a trading day
        schedule = calender.schedule(start_date=now.date(), end_date=now.date())
        if schedule.empty:
            return False
        
        #Get market hours for today
        market_open = schedule.iloc[0]['market_open'].tz_convert(tz)
        market_close = schedule.iloc[0]['market_close'].tz_convert(tz)
            
        return market_open <= now <= market_close
    
    def wait_for(self, time_amount_type: TimeAmount, amount: float = -1.0) -> None:
        """Wait for the specified amount of time.
        An TimeAmount type can be specified
        """
        if time_amount_type is TimeAmount.NEXT_MARKET_OPENING:
            amount = self.get_seconds_to_market_opening(datetime.now())
        elif time_amount_type is TimeAmount.SECONDS:
            if amount < 0:
                raise ValueError("Invalid amount of time to wait for")
            logging.info("Wait for {0:.2f} hours...".format(amount/3600))
            time.sleep(amount)