import logging
import requests
from typing import Optional
from ..configuration import Configuration
from .signal_manager import SignalManager

class TelegramBot:
    def __init__(self, config: Configuration):
        self.config = config
        self.token = self._get_telegram_token()
        self.chat_id = self._get_chat_id()
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.signal_manager = SignalManager(config)
        
    def _get_telegram_token(self) -> str:
        """Get Telegram bot token from configuration"""
        try:
            return self.config.get_telegram_credentials()["bot_token"]
        except KeyError:
            logging.error("Telegram bot token not found in configuration")
            return ""
            
    def _get_chat_id(self) -> str:
        """Get Telegram chat ID from configuration"""
        try:
            return self.config.get_telegram_credentials()["chat_id"]
        except KeyError:
            logging.error("Telegram chat ID not found in configuration")
            return ""
    
    def send_message(self, message: str) -> bool:
        """Send a message to the configured Telegram chat"""
        if not self.token or not self.chat_id:
            logging.error("Cannot send Telegram message: Missing token or chat_id")
            return False
            
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, data=data)
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to send Telegram message: {str(e)}")
            return False
            
    def send_trade_signal(self, market: str, direction: str, entry_price: float, 
                         take_profit: float, stop_loss: float, conditions: list) -> bool:
        """Send a formatted trade signal message if enough time has passed"""
        # Check if we can send a signal for this market
        if not self.signal_manager.can_send_signal(market):
            logging.info(f"Skipping signal for {market} - within signal window")
            return True  # Return True as this is not an error
            
        # Extract confidence score from conditions
        confidence_score = next((c for c in conditions if c.startswith("Confidence Score:")), None)
        if confidence_score:
            # Extract just the score part (e.g., "75.5/100 (High)")
            score_part = confidence_score.split("Confidence Score:")[1].strip()
            
            message = (
                f"🚨 <b>Trade Signal Alert</b> 🚨\n\n"
                f"Market: <b>{market}</b>\n"
                f"Direction: <b>{direction}</b>\n"
                f"Entry Price: <b>{entry_price:.5f}</b>\n"
                f"Take Profit: <b>{take_profit:.5f}</b>\n"
                f"Stop Loss: <b>{stop_loss:.5f}</b>\n\n"
                f"Confidence Score:\n"
                f"<b>{score_part}</b>\n"
            )
        else:
            # Fallback if no confidence score found
            message = (
                f"🚨 <b>Trade Signal Alert</b> 🚨\n\n"
                f"Market: <b>{market}</b>\n"
                f"Direction: <b>{direction}</b>\n"
                f"Entry Price: <b>{entry_price:.5f}</b>\n"
                f"Take Profit: <b>{take_profit:.5f}</b>\n"
                f"Stop Loss: <b>{stop_loss:.5f}</b>\n"
            )
            
        # Log message length for debugging
        logging.info(f"Preparing to send Telegram message of length {len(message)} characters")
        logging.debug(f"Message content: {message}")
            
        # Send message and record the signal if successful
        if self.send_message(message):
            self.signal_manager.record_signal(market)
            return True
        return False 