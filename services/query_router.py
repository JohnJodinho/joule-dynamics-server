"""Query classification and category gating router for Joule Dynamics Real Estate Intelligence."""

from __future__ import annotations

import json
import re

from services.observability import setup_logger
from services.groq_client import (
    groq_call,
    is_structural_error,
    GROQ_ROUTE_MODEL,
    GROQ_FALLBACK_ROUTE_MODEL,
)
from services.prompts import ROUTER_PROMPT

logger = setup_logger(__name__)

_VALID_CLASSIFICATIONS = frozenset({
    "OUT_OF_SCOPE", "PATH_A", "PATH_B", "BOTH", "GREETING", "COMMERCIAL_HANDOFF",
    "ALERT_SUBSCRIPTION",
})


def extract_json_from_text(text: str) -> dict:
    """Extract a JSON object from raw model output, handling think tags and markdown."""
    if not text:
        return {}

    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[^{}]*\}", clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


def _normalize_tool_categories(categories: object) -> list[str]:
    """Normalize raw parsed tool categories into a clean list of strings."""
    if isinstance(categories, str):
        return [categories]
    if isinstance(categories, list):
        return [str(c) for c in categories]
    return []


def _build_router_messages(user_query: str, recent_history: list[dict] | None = None) -> list[dict]:
    """Constructs router message list with recent conversation history for multi-turn awareness."""
    messages = [{"role": "system", "content": ROUTER_PROMPT}]
    if recent_history:
        for msg in recent_history[-2:]:
            content = msg.get("content", "")
            if "User Query:" in content:
                content = content.split("User Query:", 1)[-1].strip()
            messages.append({
                "role": msg.get("role", "user"),
                "content": content[:400],
            })
    messages.append({"role": "user", "content": user_query})
    return messages


def _find_last_assistant_message(recent_history: list[dict]) -> str:
    """Extracts the text of the most recent assistant turn in the history."""
    for msg in reversed(recent_history):
        if msg.get("role") == "assistant":
            return msg.get("content", "").lower()
    return ""


def _rescue_classification(
    classification: str,
    user_query: str,
    recent_history: list[dict] | None,
) -> str:
    """Architectural guardrail: rescues false OUT_OF_SCOPE classifications during active conversational flows."""
    if classification != "OUT_OF_SCOPE" or not recent_history:
        return classification

    query_lower = user_query.strip().lower()

    if re.search(r"[\w\.-]+@[\w\.-]+\.\w+", query_lower):
        logger.info("[router] Rescuing OUT_OF_SCOPE -> ALERT_SUBSCRIPTION (email address in active session)")
        return "ALERT_SUBSCRIPTION"

    last_assistant_msg = _find_last_assistant_message(recent_history)
    alert_keywords = ("alert", "notification", "email address", "subscribe", "digest")
    if any(kw in last_assistant_msg for kw in alert_keywords) and len(query_lower.split()) <= 8:
        logger.info("[router] Rescuing OUT_OF_SCOPE -> ALERT_SUBSCRIPTION (continuation of alert setup)")
        return "ALERT_SUBSCRIPTION"

    if len(query_lower.split()) <= 5 and any(c in last_assistant_msg for c in ("?", "which", "would you", "what")):
        logger.info("[router] Rescuing OUT_OF_SCOPE -> PATH_A (short answer to assistant question)")
        return "PATH_A"

    return classification


async def classify_query(
    user_query: str,
    recent_history: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """Classify user query and extract tool categories for category gating with multi-turn awareness."""
    messages = _build_router_messages(user_query, recent_history)

    for model in [GROQ_ROUTE_MODEL, GROQ_FALLBACK_ROUTE_MODEL]:
        try:
            res = await groq_call(
                model=model,
                messages=messages,
                max_tokens=1500,
                temperature=0.0,
            )
            raw = res.choices[0].message.content or "{}"
            data = extract_json_from_text(raw)
            classification = data.get("classification", "")
            tool_categories = _normalize_tool_categories(data.get("tool_categories", []))

            if classification not in _VALID_CLASSIFICATIONS:
                logger.warning(
                    f"[router] {model} returned invalid classification '{classification}', defaulting PATH_A"
                )
                classification = "PATH_A"

            classification = _rescue_classification(classification, user_query, recent_history)

            logger.info(
                f"[router] {model} -> {classification} (categories={tool_categories}): {data.get('reason', '')}"
            )
            return classification, tool_categories

        except Exception as exc:
            if is_structural_error(exc):
                logger.warning(f"[router] structural error on {model}: {exc}")
            else:
                logger.warning(f"[router] {model} failed, trying fallback: {exc}")

    fallback_classification = _rescue_classification("PATH_A", user_query, recent_history)
    return fallback_classification, []
