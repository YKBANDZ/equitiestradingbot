from typing import List

import pandas

from . import Market


class MarketMACD:
    DATE_COLUMN: str = "Date"
    MACD_COLUMN: str = "MACD"
    SIGNAL_COLUMN: str = "Signal"
    HIST_COLUMN: str = "Hist"

    market: Market
    dataframe: pandas.DataFrame

    def __init__(
            self,
            market: Market,
            date: List[str],
            macd: List[float],
            signal: List[float],
            hist: List[float],
    ) -> None:
        self.market = market
        self.dataframe = pandas.DataFrame(
            {
                self.DATE_COLUMN: date,
                self.MACD_COLUMN: macd,
                self.SIGNAL_COLUMN: signal,
                self.HIST_COLUMN: hist,
            }
        )
        self.dataframe.set_index(self.DATE_COLUMN, inplace=True)
