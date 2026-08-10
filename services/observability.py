import os
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

def setup_logger(name: str) -> logging.Logger:
    """
    Creates and returns a standard Python logger that writes to a rotating file.
    Max file size: 5MB. Keeps up to 3 backups.
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=3
        )
        
        formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')
        file_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        
    return logger
