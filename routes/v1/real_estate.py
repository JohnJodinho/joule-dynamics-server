"""
Real Estate Intelligence Layer — v1 API routes.

Endpoints:
  POST /api/v1/real-estate/chat          — main chat handler
  GET  /api/v1/real-estate/chat/starters — suggested prompt starters
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from groq import BadRequestError

from services.groq_service import process_chat_message
from services.rate_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/real-estate",
    tags=["Real Estate Intelligence Layer — v1"],
)


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(..., min_length=1, max_length=128)
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply: str
    path_used: str
    tools_called: List[Dict[str, Any]] = []
    suggested_actions: List[str] = []


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ── Error helpers ─────────────────────────────────────────────────────────────

def _error(code: str, message: str, retryable: bool, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"error": {"code": code, "message": message, "retryable": retryable}},
    )


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request / validation failure"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": ErrorResponse, "description": "Upstream AI or database service unavailable"},
        500: {"model": ErrorResponse, "description": "Unexpected server error"},
    },
    summary="Send a message to the Real Estate Intelligence Assistant",
)
async def handle_real_estate_chat(payload: ChatRequest, request: Request):
    # ── Rate limiting ─────────────────────────────────────────────────────────
    client_key = payload.session_id or request.client.host
    try:
        limiter.check_rate_limit(client_key)
    except Exception as rate_exc:
        # limiter raises HTTPException(429) directly; re-surface as our schema
        return _error(
            code="RATE_LIMIT_EXCEEDED",
            message=str(getattr(rate_exc, "detail", "Too many requests. Please wait before trying again.")),
            retryable=True,
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # ── Core processing ───────────────────────────────────────────────────────
    try:
        result = await process_chat_message(
            user_query=payload.message,
            session_id=payload.session_id,
            session_context=payload.context or {},
        )
        return ChatResponse(
            reply=result["reply"],
            path_used=result["path_used"],
            tools_called=result.get("tools_called", []),
            suggested_actions=result.get("suggested_actions", []),
        )

    except BadRequestError as e:
        # Groq 400 — usually a tool schema mismatch or bad payload
        logger.error(f"Groq 400 in chat [{payload.session_id}]: {e}")
        return _error(
            code="UPSTREAM_BAD_REQUEST",
            message="The AI service rejected a request due to a configuration issue. Our team has been notified.",
            retryable=False,
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    except Exception as e:
        err_str = str(e).lower()

        # Groq 503 / connection errors
        if any(k in err_str for k in ("503", "connection", "timeout", "service unavailable")):
            logger.warning(f"Upstream service unavailable [{payload.session_id}]: {e}")
            return _error(
                code="UPSTREAM_UNAVAILABLE",
                message="The AI service is temporarily unavailable. Please try again in a moment.",
                retryable=True,
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Groq 429 (LLM-level rate limit, separate from our session limiter)
        if "429" in err_str or "rate limit" in err_str:
            logger.warning(f"LLM rate limit hit [{payload.session_id}]: {e}")
            return _error(
                code="LLM_RATE_LIMIT",
                message="The AI service is under high load. Please wait a few seconds and try again.",
                retryable=True,
                http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Everything else — unexpected internal error
        logger.error(f"Unexpected error in real-estate chat [{payload.session_id}]: {e}", exc_info=True)
        return _error(
            code="INTERNAL_ERROR",
            message="An unexpected error occurred. Please try again shortly.",
            retryable=True,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get(
    "/chat/starters",
    summary="Return suggested starter prompts for the chat UI",
)
async def get_starter_prompts():
    return {
        "starters": [
            "What's today's biggest rate spike?",
            "What does the 7-day trailing average mean?",
            "Which Miami properties are unavailable right now?",
            "Is NYC/NJ Metro trending up or down this week?",
            "How often is listing data refreshed?",
            "Show me the most volatile properties in Miami.",
            "Compare the top 3 available Airbnb properties.",
        ]
    }
