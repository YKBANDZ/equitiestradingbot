import logging
from datetime import datetime, timezone
from typing import Optional

from ..components.configuration import Configuration
from ..components.broker.broker import Broker
from .advanced_momentum_strategy import AdvancedMomentumStrategy
from .simplicity import SimplicityStrategy
from .base import Strategy, TradeSignal
from ..interfaces import Market, MarketHistory


class StrategyManager(Strategy):
    """
    Manages switching between Advanced Momentum and Simplicity strategies
    based on time of day.
    """
    
    def __init__(self, config: Configuration, broker: Broker) -> None:
        super().__init__(config, broker)
        
        # Create both strategies
        self.momentum_strategy = AdvancedMomentumStrategy(config, broker)
        self.simplicity_strategy = SimplicityStrategy(config, broker)
        
        # Current active strategy
        self.current_strategy: Strategy = self.momentum_strategy
        self.current_strategy_name = "Advanced Momentum"
        
        # Strategy switching times (UK time)
        self.switch_to_simplicity_time = "14:30"  # 2:30 PM UK time
        self.switch_back_time = "16:00"  # 4:00 PM UK time
        
        logging.info("Strategy Manager initialized")

    def read_configuration(self, config: Configuration) -> None:
        """Read configuration for strategy switching"""
        raw = config.get_raw_config()
        
        if "strategy_manager" in raw:
            manager_config = raw["strategy_manager"]
            self.switch_to_simplicity_time = manager_config.get("switch_to_simplicity_time", "14:30")
            self.switch_back_time = manager_config.get("switch_back_time", "16:00")

    def initialise(self) -> None:
        """Initialize the strategy manager"""
        logging.info("Strategy Manager initialised")

    def fetch_datapoints(self, market: Market) -> MarketHistory:
        """Fetch datapoints using current strategy"""
        return self.current_strategy.fetch_datapoints(market)

    def find_trade_signal(self, market: Market, datapoints: MarketHistory) -> TradeSignal:
        """Find trade signal using current strategy and handle switching"""
        try:
            current_time = datetime.now(timezone.utc)
            
            # Check if we need to switch strategies
            self._check_strategy_switch(current_time)
            
            # Run the current strategy
            signal = self.current_strategy.find_trade_signal(market, datapoints)
            
            # If Simplicity strategy is complete, switch back to Momentum
            if (self.current_strategy_name == "Simplicity" and 
                hasattr(self.current_strategy, 'is_strategy_complete') and 
                self.current_strategy.is_strategy_complete()):
                
                logging.info("Simplicity Strategy completed, switching back to Advanced Momentum")
                self._switch_to_momentum()
            
            return signal
            
        except Exception as e:
            logging.error(f"Error in Strategy Manager: {e}")
            return None, None, None

    def _check_strategy_switch(self, current_time: datetime) -> None:
        """Check if we need to switch strategies based on time"""
        current_time_str = current_time.strftime("%H:%M")
        
        # Switch to Simplicity at 14:30 UK time
        if (current_time_str == self.switch_to_simplicity_time and 
            self.current_strategy_name == "Advanced Momentum"):
            
            logging.info(f"Switching to Simplicity Strategy at {current_time_str}")
            self._switch_to_simplicity()
        
        # Switch back to Momentum at 16:00 UK time
        elif (current_time_str == self.switch_back_time and 
              self.current_strategy_name == "Simplicity"):
            
            logging.info(f"Switching back to Advanced Momentum Strategy at {current_time_str}")
            self._switch_to_momentum()

    def _switch_to_simplicity(self) -> None:
        """Switch to Simplicity Strategy"""
        self.current_strategy = self.simplicity_strategy
        self.current_strategy_name = "Simplicity"
        logging.info("Strategy switched to: Simplicity")

    def _switch_to_momentum(self) -> None:
        """Switch to Advanced Momentum Strategy"""
        self.current_strategy = self.momentum_strategy
        self.current_strategy_name = "Advanced Momentum"
        logging.info("Strategy switched to: Advanced Momentum")

    def set_open_positions(self, positions) -> None:
        """Set open positions for current strategy"""
        if hasattr(self.current_strategy, 'set_open_positions'):
            self.current_strategy.set_open_positions(positions)

    def backtest(self, market: Market, start_date: datetime, end_time: datetime):
        """Backtest using current strategy"""
        return self.current_strategy.backtest(market, start_date, end_time) 