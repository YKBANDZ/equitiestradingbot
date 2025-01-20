import logging
from pathlib import Path
from datetime import datetime

#configuration 
log_filepath = Path("/Users/e1/Documents/equitiestradingbot/log")
timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
log_file = log_filepath / f"trading_bot_{timestamp}.log"

#Set up logging
logging.basicConfig(
    filename = log_file,
    filemode= "a", #Append mode
    level= logging.DEBUG, #if this needs adjusting... do so
    format= "%(asctime)s - %(levelname)s - %(message)s",
    datefmt= "%Y-%m-%d %H:%M:%S"
)

#Example Logging Entries
logging.info("Logging sys initialized.")
logging.debug("Debug mode is active.")
logging.warning("This is a warning")
logging.error("An error has occured.")

print(f"Logging initialized. Logs are being written to {log_file}")