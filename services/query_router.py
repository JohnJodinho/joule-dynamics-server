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
    "OUT_OF_SCOPE", "PATH_A", "PATH_B", "BOTH", "GREETING", "COMMERCIAL_HANDOFF"
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


async def classify_query(user_query: str) -> tuple[str, list[str]]:
    """Classify user query and extract tool categories for category gating."""
    messages = [
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": user_query},
    ]

    for model in [GROQ_ROUTE_MODEL, GROQ_FALLBACK_ROUTE_MODEL]:
        try:
            res = await groq_call(
                model=model,
                messages=messages,
                max_tokens=600,
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

            logger.info(
                f"[router] {model} -> {classification} (categories={tool_categories}): {data.get('reason', '')}"
            )
            return classification, tool_categories

        except Exception as exc:
            if is_structural_error(exc):
                logger.warning(f"[router] structural error on {model}: {exc}")
            else:
                logger.warning(f"[router] {model} failed, trying fallback: {exc}")

    return "PATH_A", []
