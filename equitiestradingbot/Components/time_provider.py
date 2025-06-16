import logging
import time 
from datetime import datetime, timedelta
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
        # Forex markets are always open
        if 'USD' in epic or 'EUR' in epic:
            return True
            
        tz = pytz.timezone('Europe/London') if 'USCGC' in epic else pytz.timezone(timezone)
        now = datetime.now(tz)

        # Choose calender based on instrument
        calender = self.gold if 'USCGC' in epic else self.nyse
        
        # Check if today is a trading day
        schedule = calender.schedule(start_date=now.date(), end_date=now.date())
        if schedule.empty:
            return False
        
        #Get market hours for today
        market_open = schedule.iloc[0]['market_open'].tz_convert(tz)
        market_close = schedule.iloc[0]['market_close'].tz_convert(tz)
            
        return market_open <= now <= market_close
    
    def get_seconds_to_market_opening(self, current_time: datetime) -> int:
        """
        Calculate the number of seconds until the next market opening for Gold futures
        Trading hours:
        - Sunday 23:00 - Friday 22:00 UK time
        - Daily break: 22:00-23:00 UK time (Mon-Thu)
        - Weekend break: Friday 22:00 - Sunday 23:00 UK time
        """
        # Convert current time to UK time
        uk_tz = pytz.timezone('Europe/London')
        current_uk = current_time.astimezone(uk_tz)
        
        # Get current day of week (0 = Monday, 6 = Sunday)
        current_weekday = current_uk.weekday()
        
        # Initialize next market open time
        next_market_open = current_uk.replace(
            hour=23,
            minute=0,
            second=0,
            microsecond=0
        )
        
        # Handle different scenarios
        if current_weekday == 4:  # Friday
            if current_uk.hour >= 22:  # After market close on Friday
                # Next opening is Sunday 23:00
                days_until_sunday = 2
                next_market_open = next_market_open + timedelta(days=days_until_sunday)
        elif current_weekday == 5:  # Saturday
            # Next opening is Sunday 23:00
            days_until_sunday = 1
            next_market_open = next_market_open + timedelta(days=days_until_sunday)
        elif current_weekday == 6:  # Sunday
            if current_uk.hour < 23:  # Before market open on Sunday
                # Next opening is today at 23:00
                pass
            else:  # After market open on Sunday
                # Next opening is tomorrow at 23:00
                next_market_open = next_market_open + timedelta(days=1)
        else:  # Monday-Thursday
            if current_uk.hour >= 22:  # After daily break
                # Next opening is tomorrow at 23:00
                next_market_open = next_market_open + timedelta(days=1)
            elif current_uk.hour < 23:  # Before market open
                # Next opening is today at 23:00
                pass
        
        # Calculate seconds until next market open
        seconds_until_open = (next_market_open - current_uk).total_seconds()
        
        logging.info(f"Current UK time: {current_uk}")
        logging.info(f"Next market open: {next_market_open}")
        logging.info(f"Seconds until market open: {seconds_until_open}")
        
        return int(seconds_until_open)
    
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