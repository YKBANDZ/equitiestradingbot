import logging
import requests
import json
from typing import Optional, List
from ..configuration import Configuration
from .platform_signal_manager import PlatformSignalManager


class PlatformSender:
    """
    Sends trading signals to the social trading platform via REST API
    """
    
    def __init__(self, config: Configuration):
        self.config = config
        self.api_base_url = self._get_platform_url()
        self.api_key = self._get_api_key()
        self.signal_manager = PlatformSignalManager(config)
        
    def _get_platform_url(self) -> str:
        """Get platform API base URL from configuration"""
        try:
            return self.config.get_platform_config()["api_base_url"]
        except KeyError:
            logging.error("Platform API base URL not found in configuration")
            return ""
            
    def _get_api_key(self) -> str:
        """Get API key for platform authentication"""
        try:
            return self.config.get_platform_config()["api_key"]
        except KeyError:
            logging.error("Platform API key not found in configuration")
            return ""
    
    def send_signal(self, signal_data: dict) -> bool:
        """Send a trading signal to the platform"""
        if not self.api_base_url or not self.api_key:
            logging.error("Cannot send platform signal: Missing URL or API key")
            return False
            
        # Check if we can send a signal for this market
        if not self.signal_manager.can_send_signal(signal_data["market"]):
            logging.info(f"Skipping platform signal for {signal_data['market']} - within signal window")
            return True  # Return True as this is not an error
            
        try:
            url = f"{self.api_base_url}/signals"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key
            }
            
            response = requests.post(url, json=signal_data, headers=headers, timeout=10)
            response.raise_for_status()
            
            # Record the signal if successful
            self.signal_manager.record_signal(signal_data["market"])
            logging.info(f"Successfully sent signal to platform for {signal_data['market']}")
            return True
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send signal to platform: {str(e)}")
            return False
        except Exception as e:
            logging.error(f"Unexpected error sending signal to platform: {str(e)}")
            return False
    
    def send_trade_signal(self, market: str, direction: str, entry_price: float, 
                         take_profit: float, stop_loss: float, conditions: List[str], 
                         strategy: str = "unknown") -> bool:
        """Send a formatted trade signal to the platform"""
        
        # Extract confidence score from conditions
        confidence_score = next((c for c in conditions if c.startswith("Confidence Score:")), None)
        if confidence_score:
            # Extract just the score part (e.g., "75.5/100 (High)")
            score_part = confidence_score.split("Confidence Score:")[1].strip()
        else:
            score_part = None
        
        # Convert direction from LONG/SHORT to BUY/SELL for platform API
        platform_direction = "BUY" if direction.upper() == "LONG" else "SELL" if direction.upper() == "SHORT" else direction

        # Convert market from CS.D.USCGC.TODAY.IP to Gold
        platform_market = "GOLD" if market.upper() == "CS.D.USCGC.TODAY.IP" else market
        
        # Prepare signal data in platform format
        signal_data = {
            "market": platform_market,
            "direction": platform_direction,
            "entry_price": entry_price,
            "take_profit": take_profit,
            "stop_loss": stop_loss
        }
        
        logging.info(f"Preparing to send platform signal for {market}")
        logging.debug(f"Signal data: {signal_data}")
        
        return self.send_signal(signal_data)
