from typing import List

import pandas

from . import Market

class MarketHistory:
    DATE_COLUMN: str = "date"
    OPEN_COLUMN: str = "open"
    HIGH_COLUMN: str = "high"
    LOW_COLUMN: str = "low"
    CLOSE_COLUMN: str = "close"
    VOLUME_COLUMN: str = "volume"

    market: Market
    dataframe: pandas.DataFrame

    def __init__(
            self,
            market: Market,
            date: List[str],
            open: List[float],
            high: List[float],
            low: List[float],
            close: List[float],
            volume: List[float],
    ) -> None:
        self.market = market
        self.dataframe = pandas.DataFrame(
            {
                self.DATE_COLUMN: date,
                self.OPEN_COLUMN: open,
                self.HIGH_COLUMN: high,
                self.LOW_COLUMN: low,
                self.CLOSE_COLUMN: close,
                self.VOLUME_COLUMN: volume,
            }
        )
        self.dataframe.set_index(self.DATE_COLUMN, inplace=True)