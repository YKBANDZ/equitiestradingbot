import json
import time
import logging
from pathlib import Path
from typing import Dict
from ..configuration import Configuration


class PlatformSignalManager:
    """
    Manages signal frequency for platform sending to prevent spam
    Similar to TelegramSignalManager but for platform signals
    """
    
    def __init__(self, config: Configuration):
        self.config = config
        self.signal_window = float(self.config.get_platform_signal_window()) * 3600  # Convert to seconds
        self.signal_timestamps: Dict[str, float] = {}
        self._load_history()
    
    def _load_history(self):
        """Load platform signal history from file"""
        try:
            with Path(self.config.get_platform_signal_history_filepath()).open(mode='r') as f:
                self.signal_timestamps = json.load(f)
                # Convert string timestamps back to float
                self.signal_timestamps = {k: float(v) for k, v in self.signal_timestamps.items()}
        except FileNotFoundError:
            logging.info("No existing platform signal history found, starting fresh")
            self.signal_timestamps = {}
        except Exception as e:
            logging.error(f"Error loading platform signal history: {str(e)}")
            self.signal_timestamps = {}
    
    def _save_history(self):
        """Save platform signal history to file"""
        try:
            # Ensure directory exists
            Path(self.config.get_platform_signal_history_filepath()).parent.mkdir(parents=True, exist_ok=True)
            
            with Path(self.config.get_platform_signal_history_filepath()).open(mode='w') as f:
                json.dump(self.signal_timestamps, f)
        except Exception as e:
            logging.error(f"Error saving platform signal history: {str(e)}")
    
    def can_send_signal(self, market_epic: str) -> bool:
        """Check if enough time has passed to send a new platform signal"""
        current_time = time.time()
        last_signal_time = self.signal_timestamps.get(market_epic, 0)
        
        if current_time - last_signal_time >= self.signal_window:
            return True
        logging.debug(f"Platform signal for {market_epic} skipped - last signal was {current_time - last_signal_time:.0f} seconds ago")
        return False
    
    def record_signal(self, market_epic: str):
        """Record that a platform signal was sent"""
        self.signal_timestamps[market_epic] = time.time()
        self._save_history()
        logging.info(f"Recorded new platform signal for {market_epic}")
