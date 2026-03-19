#!/usr/bin/env python3
"""
Test script for platform integration
This script tests the platform signal sending functionality
"""

import sys
import logging
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from equitiestradingbot.components.configuration import Configuration
from equitiestradingbot.components.platform.platform_sender import PlatformSender

def test_platform_sender():
    """Test the platform sender functionality"""
    
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    try:
        # Load configuration
        config_path = project_root / "config" / "live_trading_bot.toml"
        config = Configuration.from_filepath(config_path)
        
        # Create platform sender
        platform_sender = PlatformSender(config)
        
        # Test signal data
        test_signal = {
            "market": "CS.D.USCGC.TODAY.IP",
            "direction": "LONG",
            "entry_price": 2650.50,
            "take_profit": 2660.00,
            "stop_loss": 2640.00,
            "confidence_score": "75.5/100 (High)",
            "conditions": ["RSI: 4/5", "MACD: 3/5", "Volume above MA"],
            "strategy": "intraday_gold"
        }
        
        print("Testing platform signal sending...")
        print(f"API URL: {platform_sender.api_base_url}")
        print(f"Signal data: {test_signal}")
        
        # Send test signal
        success = platform_sender.send_signal(test_signal)
        
        if success:
            print("✅ Platform signal sent successfully!")
        else:
            print("❌ Failed to send platform signal")
            
    except Exception as e:
        print(f"❌ Error testing platform sender: {e}")
        logging.error(f"Test error: {e}")

if __name__ == "__main__":
    test_platform_sender()
