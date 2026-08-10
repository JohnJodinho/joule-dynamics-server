import os
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

# Ensure logs directory exists
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# General Project Logger Setup
# -----------------------------------------------------------------------------
def setup_logger(name: str) -> logging.Logger:
    """Creates a rotating file logger for general project traceability."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Rotating File Handler (Max 5MB, keep 3 backups)
        log_file = os.path.join(LOGS_DIR, "app.log")
        file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
        
        # Console Handler
        console_handler = logging.StreamHandler()

        formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
    return logger

# -----------------------------------------------------------------------------
# JSONL Conversation Telemetry Setup
# -----------------------------------------------------------------------------
class RoutingDecision(BaseModel):
    classification: str
    reason: Optional[str] = None

class EmbeddingsInfo(BaseModel):
    matched_section_titles: List[str] = Field(default_factory=list)
    similarity_scores: List[float] = Field(default_factory=list)

class ToolExecution(BaseModel):
    tool_name: str
    args: Dict[str, Any]
    db_response: Any

class ConversationTurn(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str
    user_message: str
    model_response: str
    time_taken_ms: int
    routing_decision: RoutingDecision
    embeddings_info: EmbeddingsInfo
    docs_retrieved: List[str] = Field(default_factory=list)
    tools_executed: List[ToolExecution] = Field(default_factory=list)
    history_context: List[Dict[str, str]] = Field(default_factory=list)
    suggested_actions: List[str] = Field(default_factory=list)

def log_conversation_turn(turn: ConversationTurn):
    """
    Appends a structured JSON string to the conversations.jsonl file.
    Designed to be run via FastAPI BackgroundTasks.
    """
    jsonl_file = os.path.join(LOGS_DIR, "conversations.jsonl")
    try:
        with open(jsonl_file, "a", encoding="utf-8") as f:
            f.write(turn.model_dump_json() + "\n")
    except Exception as e:
        logger = setup_logger(__name__)
        logger.error(f"Failed to write conversation telemetry to JSONL: {e}")
