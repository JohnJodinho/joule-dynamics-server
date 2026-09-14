"""Agentic orchestration: multi-round tool execution, synthesis, and streaming."""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

from langfuse import get_client, observe, propagate_attributes

from services.groq_client import (
    GROQ_FALLBACK_ROUTE_MODEL,
    GROQ_FALLBACK_SYNTHESIS_MODEL,
    GROQ_ROUTE_MODEL,
    GROQ_SYNTHESIS_MODEL,
    extract_failed_generation,
    groq_call,
    groq_client as _gc,
    is_structural_error,
)
from services.market_registry import market_registry
from services.observability import setup_logger
from services.prompts import build_system_prompt
from services.query_router import classify_query
from services.session import append_message, get_context_window
from services.stream_utils import (
    compress_for_history,
    extract_clarification_options,
    strip_internal_model_markers,
    strip_internal_tokens,
)
from services.supabase_service import search_methodology_rag
from services.tool_compressor import compress_tool_output
from services.tool_executor import (
    execute_tool_by_name,
    normalize_assistant_message,
    parse_failed_generation,
    parse_tool_args,
)
from services.action_resolver import resolve_actions_safely
from services.tools import (
    COMMERCIAL_TOOLS,
    SUGGEST_ACTIONS_TOOL,
    discover_tools,
)

logger = setup_logger(__name__)

MAX_TOOL_ROUNDS = 2

_TERMINAL_TOOLS = frozenset({
    "suggest_actions",
    "create_alert_subscription",
    "generate_contact_buttons",
    "generate_data_export",
})

_SYNTHESIS_DIRECTIVE = {
    "role": "user",
    "content": (
        "You are now delivering your final response directly to the user in clear Markdown. "
        "Synthesize all real estate data gathered above into a helpful analysis. "
        "If any required parameters (dates, market name) were missing or a tool returned an error, "
        "politely ask the user for clarification in your prose — do NOT output XML tags, tool calls, "
        "code blocks, or pseudo-function syntax of any kind. Respond only in natural language Markdown."
    ),
}

_ACTION_DIRECTIVE = {
    "role": "user",
    "content": (
        "You are now delivering your final response directly to the user in clear Markdown. "
        "Confirm the action or alert subscription clearly and concisely based strictly on the tool result above. "
        "Do NOT re-generate previous market data tables, bullet points, or analyses from earlier in the chat."
    ),
}

_REPLY_OUT_OF_SCOPE = (
    "I apologize, but I am currently scoped exclusively to the Real Estate Rate Monitor. "
    "I cannot assist with topics outside real estate data."
)
_REPLY_GREETING = (
    "Hello! I'm Pulse AI, a Real Estate Intelligence Assistant for Joule Dynamics. "
    "I can help you analyze rate spikes, market trends, pricing volatility, and availability "
    "data across our tracked markets. How can I assist you today?"
)
_REPLY_FALLBACK = (
    "I was unable to retrieve the data needed to answer your question. "
    "Please try rephrasing, or ask 'What markets do you track?' to see all available options."
)

_FIXED_RESPONSES = {
    "OUT_OF_SCOPE": _REPLY_OUT_OF_SCOPE,
    "GREETING": _REPLY_GREETING,
}



async def _recover_failed_generation(exc: Exception, messages: list[dict], tool_results: list) -> bool:
    """Execute tool call recovered from Groq 400 error body and append result to context."""
    failed_gen = extract_failed_generation(exc)
    if not (failed_gen and is_structural_error(exc)):
        return False

    fn, fn_args = parse_failed_generation(failed_gen)
    if not (fn and isinstance(fn_args, dict)):
        return False

    tool_result = await execute_tool_by_name(fn, fn_args)
    tool_results.append({"tool": fn, "args": fn_args})
    messages.append({
        "tool_call_id": f"recov_{int(time.time())}",
        "role": "tool",
        "name": fn,
        "content": compress_tool_output(fn, tool_result),
    })
    return True


async def _call_retrieval_model(messages: list[dict], active_tools: list, round_num: int, tool_results: list) -> object | None:
    """Invoke synthesis models with fallback to obtain tool calls or final answer."""
    for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
        try:
            res = await groq_call(
                model=model,
                messages=messages,
                tools=active_tools,
                tool_choice="auto",
                max_tokens=3500,
                temperature=0.2,
            )
            return res.choices[0].message
        except Exception as exc:
            recovered = await _recover_failed_generation(exc, messages, tool_results)
            if recovered:
                return None
            logger.warning(f"[agent_loop] {model} error round={round_num}: {exc}")
    return None


async def _execute_tool_calls(tool_calls: list, messages: list[dict], tool_results: list) -> None:
    """Execute all tool calls emitted in a round and append their outputs to message context."""
    for tc in tool_calls:
        fn = tc.function.name
        args = parse_tool_args(tc.function.arguments)
        logger.info(f"[agent_loop] executing tool={fn} args={args}")
        result = await execute_tool_by_name(fn, args)
        tool_results.append({"tool": fn, "args": args})
        messages.append({
            "tool_call_id": tc.id,
            "role": "tool",
            "name": fn,
            "content": compress_tool_output(fn, result),
        })


async def _execute_streaming_tool_calls(
    tool_calls: list,
    messages: list[dict],
    tool_results: list,
) -> AsyncIterator[dict]:
    """Execute tool calls for streaming mode, yielding events and appending results to context."""
    for tc in tool_calls:
        fn = tc.function.name
        args = parse_tool_args(tc.function.arguments)
        yield {"type": "tool_call", "tool": fn, "args": args}
        result = await execute_tool_by_name(fn, args)
        tool_results.append({"tool": fn, "args": args})
        messages.append({
            "tool_call_id": tc.id,
            "role": "tool",
            "name": fn,
            "content": compress_tool_output(fn, result),
        })


def _has_terminal_tool(tool_calls: list) -> bool:
    """Return True if any tool call represents a terminal user interaction."""
    for tc in tool_calls:
        fn = getattr(getattr(tc, "function", None), "name", None)
        if fn in _TERMINAL_TOOLS:
            return True
    return False


def _choose_directive(tool_results: list) -> dict:
    """Return action confirmation directive if action tools were called, else market synthesis directive."""
    if any(res.get("tool") in _TERMINAL_TOOLS for res in tool_results):
        return _ACTION_DIRECTIVE
    return _SYNTHESIS_DIRECTIVE


def _extract_early_prose(msg: object, round_num: int) -> str | None:
    """Extract cleaned prose if model responded without tool calls on round 1."""
    if round_num != 1:
        return None
    content = getattr(msg, "content", None)
    if not content:
        return None
    return strip_internal_model_markers(content)


async def _synthesize_markdown(messages: list[dict], directive: dict | None = None) -> str:
    """Turn 2: Synthesize retrieved data into natural language Markdown with tools disabled."""
    synth_messages = list(messages) + [directive or _SYNTHESIS_DIRECTIVE]
    for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL, GROQ_FALLBACK_ROUTE_MODEL]:
        try:
            res = await groq_call(
                model=model,
                messages=synth_messages,
                tools=None,
                max_tokens=3500,
                temperature=0.2,
            )
            raw = res.choices[0].message.content or ""
            cleaned = strip_internal_model_markers(raw)
            if cleaned:
                return cleaned
        except Exception as exc:
            logger.warning(f"[agent_loop] synthesis failed on {model}: {exc}")
    return _REPLY_FALLBACK


async def run_agent_loop(
    messages: list[dict],
    active_tools: list | None,
    tool_results: list,
    user_query: str = "",
    suggested_actions_out: list | None = None,
) -> str | None:
    """3-Turn Agentic State Machine (Non-streaming)."""
    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        if not active_tools:
            break

        msg = await _call_retrieval_model(messages, active_tools, round_num, tool_results)
        if msg is None:
            continue

        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            early_prose = _extract_early_prose(msg, round_num)
            if early_prose:
                if suggested_actions_out is not None:
                    suggested_actions_out.extend(await resolve_actions_safely(user_query, early_prose, tool_results))
                return early_prose
            break

        messages.append(normalize_assistant_message(msg))
        await _execute_tool_calls(tool_calls, messages, tool_results)
        if _has_terminal_tool(tool_calls):
            break

    directive = _choose_directive(tool_results)
    reply_text = await _synthesize_markdown(messages, directive)

    if suggested_actions_out is not None:
        suggested_actions_out.extend(await resolve_actions_safely(user_query, reply_text, tool_results))

    return reply_text


async def _stream_synthesis(synth_messages: list[dict], loop: asyncio.AbstractEventLoop) -> AsyncIterator[dict]:
    """Stream synthesized markdown tokens through universal token stripper."""
    synthesis_succeeded = False
    for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL, GROQ_FALLBACK_ROUTE_MODEL]:
        try:
            stream = await loop.run_in_executor(
                None,
                lambda m=model: _gc.chat.completions.create(
                    model=m,
                    messages=synth_messages,
                    temperature=0.2,
                    max_tokens=3500,
                    stream=True,
                ),
            )

            def _raw_deltas():
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield delta

            for clean_token in strip_internal_tokens(_raw_deltas()):
                if clean_token:
                    yield {"type": "token", "token": clean_token}

            synthesis_succeeded = True
            break
        except Exception as exc:
            logger.warning(f"[stream_loop] synthesis failed on {model}: {exc}")

    if not synthesis_succeeded:
        yield {"type": "token", "token": _REPLY_FALLBACK}


async def _stream_synthesis_and_complete(
    messages: list[dict],
    tool_results: list,
    user_query: str,
    suggested_actions_out: list | None,
    loop: asyncio.AbstractEventLoop,
) -> AsyncIterator[dict]:
    """Stream final synthesis tokens and yield completion event with resolved actions."""
    full_reply_text = ""
    directive = _choose_directive(tool_results)
    synth_messages = list(messages) + [directive]
    async for event in _stream_synthesis(synth_messages, loop):
        if event["type"] == "token":
            full_reply_text += event["token"]
        yield event

    suggested_actions = await resolve_actions_safely(user_query, full_reply_text, tool_results)
    if suggested_actions_out is not None:
        suggested_actions_out.extend(suggested_actions)

    yield {
        "type": "done",
        "tools_called": tool_results,
        "suggested_actions": suggested_actions,
    }


async def run_agent_loop_streaming(
    messages: list[dict],
    active_tools: list | None,
    tool_results: list,
    user_query: str = "",
    suggested_actions_out: list | None = None,
) -> AsyncIterator[dict]:
    """3-Turn Agentic State Machine (Streaming SSE)."""
    loop = asyncio.get_event_loop()

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        if not active_tools:
            break

        msg = await _call_retrieval_model(messages, active_tools, round_num, tool_results)
        if msg is None:
            continue

        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            early_prose = _extract_early_prose(msg, round_num)
            if early_prose:
                yield {"type": "token", "token": early_prose}
                actions = await resolve_actions_safely(user_query, early_prose, tool_results)
                if suggested_actions_out is not None:
                    suggested_actions_out.extend(actions)
                yield {
                    "type": "done",
                    "tools_called": tool_results,
                    "suggested_actions": actions,
                }
                return
            break

        messages.append(normalize_assistant_message(msg))
        async for event in _execute_streaming_tool_calls(tool_calls, messages, tool_results):
            yield event

        if _has_terminal_tool(tool_calls):
            break

    async for event in _stream_synthesis_and_complete(messages, tool_results, user_query, suggested_actions_out, loop):
        yield event


async def _build_chat_messages(user_query: str, session_id: str, session_context: dict, classification: str) -> list[dict]:
    """Build system prompt with live markets, context window, and RAG methodology if applicable."""
    rag_chunks = await search_methodology_rag(user_query) if classification in ("PATH_B", "BOTH", "COMMERCIAL_HANDOFF") else []
    system_prompt = build_system_prompt(classification, markets=market_registry.get_markets())
    user_msg = f"User Context Filters: {json.dumps(session_context)}\nUser Query: {user_query}"
    append_message(session_id, {"role": "user", "content": user_msg})

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        *get_context_window(session_id),
    ]
    if rag_chunks:
        messages.append({
            "role": "system",
            "content": "Retrieved Methodology Context:\n" + "\n---\n".join(rag_chunks),
        })
    return messages


_STATIC_TOOLS_MAP = {
    "COMMERCIAL_HANDOFF": COMMERCIAL_TOOLS,
    "PATH_B": [SUGGEST_ACTIONS_TOOL],
}


def _resolve_active_tools(classification: str, user_query: str, messages: list[dict], tool_categories: list[str]) -> list[dict] | None:
    """Resolve and filter available tools using semantic discovery and category gating."""
    if classification in ("PATH_A", "BOTH"):
        discovery_query = user_query
        if len(user_query.strip()) < 25 and len(messages) > 2:
            for prev_msg in reversed(messages[:-1]):
                if prev_msg.get("role") == "user":
                    discovery_query = f"{prev_msg.get('content', '')} {user_query}"
                    break
        return discover_tools(discovery_query, categories=tool_categories, top_k=4)

    if classification == "ALERT_SUBSCRIPTION":
        from services.alert_tool_schema import CREATE_ALERT_SUBSCRIPTION_TOOL
        return [CREATE_ALERT_SUBSCRIPTION_TOOL]

    return _STATIC_TOOLS_MAP.get(classification)


@observe(name="process-chat")
async def process_chat_message(
    user_query: str,
    session_id: str,
    session_context: dict,
) -> dict:
    """Main non-streaming chat handler."""
    with propagate_attributes(session_id=session_id, tags=["real-estate-chat"]):
        get_client().update_current_span(input=user_query)

        recent_history = get_context_window(session_id, limit=4)
        classification, tool_categories = await classify_query(user_query, recent_history=recent_history)

        if classification in _FIXED_RESPONSES:
            reply = _FIXED_RESPONSES[classification]
            get_client().update_current_span(output=reply)
            return {"reply": reply, "path_used": classification, "tools_called": [], "suggested_actions": []}

        messages = await _build_chat_messages(user_query, session_id, session_context, classification)
        active_tools = _resolve_active_tools(classification, user_query, messages, tool_categories)

        tool_results: list[dict] = []
        suggested_actions_list: list[str] = []
        final_reply = await run_agent_loop(
            messages, active_tools, tool_results, user_query=user_query, suggested_actions_out=suggested_actions_list
        )

        if not final_reply or final_reply.strip().startswith('{"name":') or final_reply.strip().startswith("<function="):
            final_reply = _REPLY_FALLBACK

        append_message(session_id, {
            "role": "assistant",
            "content": compress_for_history(final_reply),
        })

        extracted_actions = extract_clarification_options(final_reply)
        all_actions = list(dict.fromkeys(suggested_actions_list + extracted_actions))

        get_client().update_current_span(output=final_reply)

        return {
            "reply": final_reply,
            "path_used": classification,
            "tools_called": tool_results,
            "suggested_actions": all_actions,
        }


@observe(name="stream-chat")
async def stream_chat_message(
    user_query: str,
    session_id: str,
    session_context: dict,
) -> AsyncIterator[dict]:
    """SSE streaming chat handler."""
    with propagate_attributes(session_id=session_id, tags=["real-estate-chat", "stream"]):
        recent_history = get_context_window(session_id, limit=4)
        classification, tool_categories = await classify_query(user_query, recent_history=recent_history)
        yield {"type": "status", "classification": classification}

        if classification in _FIXED_RESPONSES:
            reply = _FIXED_RESPONSES[classification]
            yield {"type": "token", "token": reply}
            yield {"type": "done", "tools_called": [], "suggested_actions": [], "path_used": classification}
            return

        messages = await _build_chat_messages(user_query, session_id, session_context, classification)
        active_tools = _resolve_active_tools(classification, user_query, messages, tool_categories)

        tool_results: list[dict] = []
        full_reply_parts: list[str] = []

        async for event in run_agent_loop_streaming(messages, active_tools, tool_results, user_query=user_query):
            if event["type"] == "token":
                full_reply_parts.append(event["token"])
            yield event

        full_reply = "".join(full_reply_parts)
        if full_reply:
            append_message(session_id, {
                "role": "assistant",
                "content": compress_for_history(full_reply),
            })
