"""
Real Estate Intelligence Layer — v1 API routes.

Endpoints:
  POST /api/v1/real-estate/chat          — non-streaming JSON response (backwards compat)
  POST /api/v1/real-estate/chat/stream   — SSE streaming response (preferred)
  GET  /api/v1/real-estate/chat/starters — suggested prompt starters
"""
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.groq_service import process_chat_message, stream_chat_message
from services.groq_client import is_structural_error
from services.rate_limiter import limiter
from services.observability import setup_logger

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v1/real-estate",
    tags=["Real Estate Intelligence Layer — v1"],
)


class ChatRequest(BaseModel):
    message:    str                         = Field(..., min_length=1, max_length=2000)
    session_id: str                         = Field(..., min_length=1, max_length=128)
    context:    Optional[Dict[str, Any]]    = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply:            str
    path_used:        str
    tools_called:     List[Dict[str, Any]] = []
    suggested_actions: List[str]           = []


class ErrorDetail(BaseModel):
    code:      str
    message:   str
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorDetail


def _error(code: str, message: str, retryable: bool, http_status: int) -> JSONResponse:
    """Formats standardized JSON error response."""
    return JSONResponse(
        status_code=http_status,
        content={"error": {"code": code, "message": message, "retryable": retryable}},
    )


def _sse_error_stream(code: str, message: str):
    """Yield a single SSE error event then close."""
    payload = json.dumps({"type": "error", "code": code, "message": message})
    yield f"event: error\ndata: {payload}\n\n"


def _check_rate_limit(client_key: str):
    """Raises HTTPException(429) via the limiter if limit exceeded."""
    limiter.check_rate_limit(client_key)


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request / validation failure"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": ErrorResponse, "description": "Upstream AI or database service unavailable"},
        500: {"model": ErrorResponse, "description": "Unexpected server error"},
    },
    summary="Send a message to the Real Estate Intelligence Assistant (non-streaming)",
)
async def handle_real_estate_chat(payload: ChatRequest, request: Request):
    """Handles non-streaming conversational turns for backward compatibility."""
    client_key = payload.session_id or request.client.host
    try:
        _check_rate_limit(client_key)
    except Exception as rate_exc:
        return _error(
            code="RATE_LIMIT_EXCEEDED",
            message=str(getattr(rate_exc, "detail", "Too many requests. Please wait before trying again.")),
            retryable=True,
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

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

    except Exception as exc:
        return _handle_exception(exc, payload.session_id)


@router.post(
    "/chat/stream",
    summary="Send a message to Pulse AI — receives Server-Sent Events stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE stream. Events: status | tool_call | token | done | error",
            "content": {"text/event-stream": {}},
        },
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
async def handle_real_estate_chat_stream(payload: ChatRequest, request: Request):
    """SSE streaming endpoint providing status, tool calls, and text tokens."""
    client_key = payload.session_id or request.client.host
    try:
        _check_rate_limit(client_key)
    except Exception as rate_exc:
        msg = str(getattr(rate_exc, "detail", "Too many requests. Please wait before trying again."))
        return StreamingResponse(
            _sse_error_stream("RATE_LIMIT_EXCEEDED", msg),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            media_type="text/event-stream",
        )

    async def event_generator() -> AsyncIterator[str]:
        try:
            async for event in stream_chat_message(
                user_query=payload.message,
                session_id=payload.session_id,
                session_context=payload.context or {},
            ):
                event_type = event.get("type", "token")
                data = json.dumps(event)

                if event_type in ("status", "tool_call", "token", "done"):
                    yield f"event: {event_type}\ndata: {data}\n\n"
                else:
                    yield f"event: {event_type}\ndata: {data}\n\n"

        except Exception as exc:
            logger.error(f"SSE stream error [{payload.session_id}]: {exc}", exc_info=True)
            err_payload = json.dumps({
                "type": "error",
                "code": "STREAM_ERROR",
                "message": "An error occurred while generating the response. Please try again.",
            })
            yield f"event: error\ndata: {err_payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/chat/starters",
    summary="Return suggested starter prompts for the chat UI",
)
async def get_starter_prompts():
    """Returns curated starter prompts for initial user suggestions."""
    return {
        "starters": [
            "What markets are you currently tracking?",
            "Which properties have the biggest rate spike today?",
            "What does the 7-day trailing average mean?",
            "Show me the most volatile properties across all markets.",
            "Are there any 25%+ price spikes this week?",
            "What's the average nightly rate in Abuja right now?",
            "Compare the top 3 available Airbnb properties.",
        ]
    }


def _handle_exception(exc: Exception, session_id: str) -> JSONResponse:
    """Classifies runtime exceptions and maps to structured error responses."""
    err_str = str(exc).lower()

    if is_structural_error(exc):
        logger.error(f"Structural Groq error [{session_id}]: {exc}")
        return _error(
            code="UPSTREAM_BAD_REQUEST",
            message="The AI service rejected a request due to a configuration issue.",
            retryable=False,
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if any(k in err_str for k in ("503", "connection", "timeout", "service unavailable")):
        logger.warning(f"Upstream unavailable [{session_id}]: {exc}")
        return _error(
            code="UPSTREAM_UNAVAILABLE",
            message="The AI service is temporarily unavailable. Please try again in a moment.",
            retryable=True,
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if "429" in err_str or "rate limit" in err_str:
        logger.warning(f"LLM rate limit hit [{session_id}]: {exc}")
        return _error(
            code="LLM_RATE_LIMIT",
            message="The AI service is under high load. Please wait a few seconds and try again.",
            retryable=True,
            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    logger.error(f"Unexpected error [{session_id}]: {exc}", exc_info=True)
    return _error(
        code="INTERNAL_ERROR",
        message="An unexpected error occurred. Please try again shortly.",
        retryable=True,
        http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
