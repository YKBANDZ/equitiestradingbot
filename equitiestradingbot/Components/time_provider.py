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

    def is_market_open(self, timezone: str) -> bool:
        """
        Return True if the US market is open, false otherwise
        - **timezone**: string representing the timezone
        """
        tz = pytz.timezone(timezone)
        now = datetime.now(tz=tz)
        
        # Check if today is a trading day
        schedule = self.nyse.schedule(start_date=now.date(), end_date=now.date())
        if schedule.empty:
            return False
            
        # Get market hours for today
        market_open = schedule.iloc[0]['market_open'].tz_convert(tz)
        market_close = schedule.iloc[0]['market_close'].tz_convert(tz)
        
        # Check if current time is within market hours
        return market_open <= now <= market_close
    
    def get_seconds_to_market_opening(self, from_time: datetime) -> float:
        """Return the amount of seconds from now to the next market opening,
        taking into account US market holidays"""
        
        # Get the calendar for next 5 days
        schedule = self.nyse.schedule(
            start_date=from_time.date(),
            end_date=(from_time + pd.Timedelta(days=5)).date()
        )
        
        if schedule.empty:
            # If no trading days in next 5 days, use a default of next Monday
            return (from_time + pd.Timedelta(days=7)).replace(
                hour=9, minute=30, second=0, microsecond=0
            ).timestamp() - from_time.timestamp()
            
        # Get next market opening time
        next_open = schedule.iloc[0]['market_open']
        if from_time.timestamp() > next_open.timestamp():
            # If we're past today's opening, get next day's opening
            if len(schedule) > 1:
                next_open = schedule.iloc[1]['market_open']
            else:
                # Get schedule for further dates if needed
                future_schedule = self.nyse.schedule(
                    start_date=(from_time + pd.Timedelta(days=5)).date(),
                    end_date=(from_time + pd.Timedelta(days=10)).date()
                )
                if not future_schedule.empty:
                    next_open = future_schedule.iloc[0]['market_open']
                
        return next_open.timestamp() - from_time.timestamp()
    
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