from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request, BackgroundTasks
from pydantic import BaseModel

import logging

logger = logging.getLogger(__name__)

from services.groq_service import process_chat_message
from services.rate_limiter import limiter

router = APIRouter(
    prefix="/api/v1/real-estate", tags=["Real Estate Intelligence Layer"]
)


class ChatRequest(BaseModel):
    message: str
    session_id: str
    context: Optional[Dict[str, Any]] = {}


class ChatResponse(BaseModel):
    reply: str
    path_used: str
    tools_called: List[Dict[str, Any]]
    suggested_actions: Optional[List[str]] = []


@router.post("/chat", response_model=ChatResponse)
async def handle_real_estate_chat(payload: ChatRequest, request: Request):
    # Enforce token bucket rate limiting by session ID or client IP
    client_key = payload.session_id or request.client.host
    limiter.check_rate_limit(client_key)

    try:
        result = await process_chat_message(
            user_query=payload.message,
            session_id=payload.session_id,
            session_context=payload.context or {},
        )
    except Exception as e:
        logger.error(f"Error in handle_real_estate_chat: {e}")
        return ChatResponse(
            reply="Unable to reach the intelligence layer. Please try again shortly.",
            path_used="ERROR",
            tools_called=[],
        )

    return ChatResponse(
        reply=result["reply"],
        path_used=result["path_used"],
        tools_called=result["tools_called"],
        suggested_actions=result.get("suggested_actions", []),
    )


@router.get("/chat/starters")
async def get_starter_prompts():
    return {
        "starters": [
            "What's today's biggest rate spike?",
            "What does the 7-day average mean?",
            "Which Miami properties are unavailable right now?",
            "How often is listing data refreshed?",
        ]
    }
