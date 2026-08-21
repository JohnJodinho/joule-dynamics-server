"""
services/groq_client.py
────────────────────────
Groq SDK singleton, model constants, and a retry-aware API wrapper.

Architectural fixes:
  #7 - Exponential backoff with jitter for transient 429/5xx errors.
  #8 - Structural error classification so fallback models are only tried
       for transient failures, never for schema/payload errors.
"""

import asyncio
import random
import time
import json
from typing import Any

from groq import Groq
from services.observability import setup_logger
from config import (
    GROQ_API_KEY,
    GROQ_ROUTE_MODEL,
    GROQ_FALLBACK_ROUTE_MODEL,
    GROQ_SYNTHESIS_MODEL,
    GROQ_FALLBACK_SYNTHESIS_MODEL,
)

logger = setup_logger(__name__)

# ── Singleton Groq client ──────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)

# ── Error classification ───────────────────────────────────────────────────────
_STRUCTURAL_ERROR_CODES = frozenset({
    "tool_use_failed",
    "invalid_request_error",
    "context_length_exceeded",
    "json_validate_failed",
})

_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


def is_structural_error(exc: Exception) -> bool:
    """Return True if error is caused by bad payload/schema, NOT transient."""
    body = getattr(exc, "body", {}) or {}
    if isinstance(body, dict):
        code = body.get("error", {}).get("code", "") or ""
        msg  = body.get("error", {}).get("message", "") or ""
        if code in _STRUCTURAL_ERROR_CODES:
            return True
        if "tool choice is none" in msg.lower():
            return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status == 400


def is_retryable_error(exc: Exception) -> bool:
    """Return True if error is transient and worth retrying."""
    if is_structural_error(exc):
        return False
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in _RETRYABLE_HTTP_STATUSES:
        return True
    return status is None


async def groq_call(
    *,
    model: str,
    messages: list,
    tools: list = None,
    tool_choice: str = "none",
    max_tokens: int = 1000,
    temperature: float = 0.2,
    max_retries: int = 3,
    base_delay: float = 1.0,
    response_format: dict = None,
):
    """
    Async Groq completion call with exponential backoff + jitter.
    - Raises immediately on structural 400 errors (do not waste retries).
    - Returns the raw Groq ChatCompletion object on success.
    """
    last_exc = None

    for attempt in range(max_retries):
        try:
            kwargs = dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = tool_choice
            if response_format:
                kwargs["response_format"] = response_format

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: groq_client.chat.completions.create(**kwargs),
            )
            return result

        except Exception as exc:
            last_exc = exc
            if is_structural_error(exc):
                raise  # Never retry structural errors
            if is_retryable_error(exc):
                delay = (base_delay * (2 ** attempt)) + random.uniform(0, 0.5)
                logger.warning(
                    f"[groq_call] {model} attempt {attempt + 1}/{max_retries} "
                    f"retrying in {delay:.1f}s. Error: {exc}"
                )
                await asyncio.sleep(delay)
            else:
                raise

    raise RuntimeError(
        f"groq_call: all {max_retries} retries exhausted for model={model}"
    ) from last_exc


def extract_failed_generation(exc: Exception) -> str:
    """Pull the failed_generation string from a Groq 400 body, or return empty string."""
    body = getattr(exc, "body", {}) or {}
    if isinstance(body, dict):
        return body.get("error", {}).get("failed_generation", "") or ""
    return ""
