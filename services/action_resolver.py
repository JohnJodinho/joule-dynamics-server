"""Action chip generation and resolution for interactive chat responses."""

import json
from services.groq_client import (
    GROQ_ROUTE_MODEL,
    GROQ_SYNTHESIS_MODEL,
    groq_call,
)
from services.observability import setup_logger
from services.tool_executor import parse_tool_args
from services.tools import SUGGEST_ACTIONS_TOOL

logger = setup_logger(__name__)

ACTION_RESOLVER_PROMPT = """You are an interactive action generator for Joule Dynamics Real Estate Intelligence.
Given the user's query and the assistant's final response, determine 0 to 4 short, highly relevant follow-up actions or clarifying choices for the user.
Guidelines:
- If the assistant asked a clarifying question (e.g. which market or date), provide those exact choices using the actual tracked market names available in the conversation context.
- If the assistant provided market/price analysis, suggest logical next-step actions (e.g. ["Compare market averages", "See Rate Volatility", "Generate Download Report"]).
- If the conversation is complete, a simple greeting, or no follow-up is genuinely useful, return an empty array actions: [].
- You must return ONLY via the suggest_actions tool call. Do not force suggestions if none are genuinely helpful."""

_TERMINAL_INTENTS = frozenset({
    "cancel", "stop", "nevermind", "quit", "exit", "no", "no thanks",
    "thanks", "thank you", "bye", "goodbye",
})


def _extract_suggested_actions_from_response(res: object) -> list[str]:
    """Extract action strings from forced suggest_actions tool call response."""
    msg = getattr(res, "choices", [None])[0].message
    tool_calls = getattr(msg, "tool_calls", None)
    if not tool_calls:
        return []

    args = parse_tool_args(tool_calls[0].function.arguments)
    actions = args.get("actions") or args.get("options") or []
    if not isinstance(actions, list):
        return []

    return [str(a).strip() for a in actions if str(a).strip()][:4]


async def resolve_suggested_actions(user_query: str, assistant_reply: str) -> list[str]:
    """Turn 3: Resolve dynamic follow-up action suggestions using forced tool choice."""
    if not assistant_reply or len(assistant_reply.strip()) < 10:
        return []

    messages = [
        {"role": "system", "content": ACTION_RESOLVER_PROMPT},
        {"role": "user", "content": user_query},
        {"role": "assistant", "content": assistant_reply},
    ]

    for model in [GROQ_ROUTE_MODEL, GROQ_SYNTHESIS_MODEL]:
        try:
            res = await groq_call(
                model=model,
                messages=messages,
                tools=[SUGGEST_ACTIONS_TOOL],
                tool_choice={"type": "function", "function": {"name": "suggest_actions"}},
                max_tokens=500,
                temperature=0.0,
            )
            actions = _extract_suggested_actions_from_response(res)
            if actions:
                return actions
        except Exception as exc:
            logger.warning(f"[resolve_actions] error on {model}: {exc}")

    return []


def is_terminal_intent(query: str) -> bool:
    """Return True if user intent is cancellation or conclusion requiring no action buttons."""
    return query.strip().lower() in _TERMINAL_INTENTS


def extract_invoked_actions(tool_results: list) -> list[str]:
    """Extract actions if suggest_actions was already invoked during tool rounds."""
    for tr in tool_results:
        if tr.get("tool") == "suggest_actions":
            args = tr.get("args") or {}
            actions = args.get("actions") or args.get("options") or []
            if isinstance(actions, list) and actions:
                return [str(a).strip() for a in actions if str(a).strip()][:4]
    return []


async def resolve_actions_safely(user_query: str, reply_text: str, tool_results: list) -> list[str]:
    """Resolve action suggestions without redundant LLM calls."""
    invoked = extract_invoked_actions(tool_results)
    if invoked:
        return invoked
    if is_terminal_intent(user_query):
        return []
    return await resolve_suggested_actions(user_query, reply_text)
