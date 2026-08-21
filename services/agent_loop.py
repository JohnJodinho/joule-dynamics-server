"""
services/agent_loop.py
───────────────────────
Agentic orchestration: routing, RAG, and the multi-round tool execution loop.

Architectural fixes implemented here:
  #1 - True agentic tool loop (up to MAX_TOOL_ROUNDS rounds). Tools remain
       available until the model emits finish_reason="stop". This eliminates
       the "One-Shot Tool Execution" anti-pattern that caused 400 errors when
       reasoning models needed multiple tool calls.
  #2 - Universal failed_generation recovery: any tool extracted from a Groq
       400 error body is executed and its result is fed back into the context
       before re-attempting synthesis.
  #3 - Prompt composition by classification (via build_system_prompt).
  #4 - Small fast model for routing (llama-3.1-8b-instant via GROQ_ROUTE_MODEL
       env var, default is the smaller non-reasoning model).
"""

import json
import time
import asyncio
import re
from typing import AsyncIterator

from langfuse import observe, propagate_attributes, get_client

from services.observability import setup_logger
from services.groq_client import (
    groq_call,
    is_structural_error,
    extract_failed_generation,
    GROQ_ROUTE_MODEL,
    GROQ_FALLBACK_ROUTE_MODEL,
    GROQ_SYNTHESIS_MODEL,
    GROQ_FALLBACK_SYNTHESIS_MODEL,
)
from services.prompts import ROUTER_PROMPT, build_system_prompt
from services.session import get_context_window, append_message
from services.tool_executor import (
    execute_tool_by_name,
    normalize_assistant_message,
    parse_tool_args,
    parse_failed_generation,
)
from services.tool_compressor import compress_tool_output
from services.supabase_service import search_methodology_rag
from services.tools import REAL_ESTATE_TOOLS, COMMERCIAL_TOOLS, select_tools

logger = setup_logger(__name__)

MAX_TOOL_ROUNDS = 4  # Maximum tool-call rounds before forcing synthesis

# ─── STATIC RESPONSES ─────────────────────────────────────────────────────────

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
    "Please try rephrasing, or ask about a specific market (Miami or NYC/NJ Metro)."
)


# ─── ROUTER ───────────────────────────────────────────────────────────────────


# Valid classification labels - used to validate router output
_VALID_CLASSIFICATIONS = frozenset({
    "OUT_OF_SCOPE", "PATH_A", "PATH_B", "BOTH", "GREETING", "COMMERCIAL_HANDOFF"
})


def _extract_json_from_text(text: str) -> dict:
    """
    Extract a JSON object from raw model output.

    Handles two patterns:
      1. Thinking models (qwen): output wrapped in <think>...</think> before JSON.
      2. Standard: clean JSON object in the content.

    Falls back to regex extraction if json.loads fails on the full text.
    """
    if not text:
        return {}

    # Strip <think>...</think> block if present (Qwen thinking models)
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Try direct parse
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Regex: find first {...} block
    match = re.search(r"\{[^{}]*\}", clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


async def classify_query(user_query: str) -> str:
    """
    Classify the user query into one of 6 route tags using a fast router model.

    NOTE: response_format={"type": "json_object"} is intentionally NOT used.
    Qwen thinking models (qwen/qwen3.6-27b) reject JSON mode with a 400
    json_validate_failed error. We ask for JSON via the prompt instead and
    extract it with _extract_json_from_text() which handles <think> blocks
    and falls back to regex extraction.
    """
    messages = [
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": user_query},
    ]
    for model in [GROQ_ROUTE_MODEL, GROQ_FALLBACK_ROUTE_MODEL]:
        try:
            res = await groq_call(
                model=model,
                messages=messages,
                max_tokens=500,   # Allow thinking models to finish <think> block
                temperature=0.0,
                # No response_format - unsupported by Qwen thinking models
            )
            raw = res.choices[0].message.content or "{}"
            data = _extract_json_from_text(raw)
            classification = data.get("classification", "")

            if classification not in _VALID_CLASSIFICATIONS:
                logger.warning(
                    f"[router] {model} returned invalid classification '{classification}', defaulting PATH_A"
                )
                classification = "PATH_A"

            logger.info(
                f"[router] {model} -> {classification}: {data.get('reason', '')}"
            )
            return classification

        except Exception as exc:
            if is_structural_error(exc):
                logger.warning(f"[router] structural error on {model}: {exc}")
            else:
                logger.warning(f"[router] {model} failed, trying fallback: {exc}")

    return "PATH_A"  # Safe default


# ─── AGENTIC TOOL LOOP ────────────────────────────────────────────────────────


async def run_agent_loop(
    messages: list[dict],
    active_tools: list | None,
    tool_results: list,
) -> str | None:
    """
    Core agentic loop (Fix #1).

    Keeps tools available until the model emits finish_reason='stop'.
    Handles failed_generation recovery (Fix #2) for any tool, not just exports.
    Returns the final text reply or None if all attempts failed.
    """
    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        response_message = None

        # ── Brain call (primary + fallback) ───────────────────────────────────
        # Synthesis detection: strip tools + raise max_tokens when we already
        # have tool results and the last message is a tool result. The model
        # has all data it needs and should write the reply, not call more tools.
        last_is_tool = bool(messages) and messages[-1].get('role') == 'tool'
        has_data = bool(tool_results) and last_is_tool
        tools_now = None if has_data else active_tools
        choice_now = 'none' if has_data else ('auto' if active_tools else 'none')
        tokens_now = 2000 if has_data else 300  # tool-call JSON is <30 tokens
        if has_data:
            logger.info('[agent_loop] synthesis mode: tools stripped, max_tokens=2000')
        for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
            try:
                brain_res = await groq_call(
                    model=model,
                    messages=messages,
                    tools=tools_now,
                    tool_choice=choice_now,
                    max_tokens=tokens_now,
                    temperature=0.2,
                )
                response_message = brain_res.choices[0].message
                logger.info(
                    f"[agent_loop] round={round_num} model={model} "
                    f"finish_reason={brain_res.choices[0].finish_reason}"
                )
                break

            except Exception as exc:
                failed_gen = extract_failed_generation(exc)
                if failed_gen and is_structural_error(exc):
                    # Model tried to call a tool but tools weren't in this call,
                    # or schema mismatch — recover by executing the tool directly.
                    fn, fn_args = parse_failed_generation(failed_gen)
                    if fn and isinstance(fn_args, dict):
                        logger.info(
                            f"[agent_loop] recovering tool={fn} from failed_generation"
                        )
                        tool_result = await execute_tool_by_name(fn, fn_args)
                        tool_results.append({"tool": fn, "args": fn_args})
                        messages.append(
                            {
                                "tool_call_id": f"recov_{int(time.time())}",
                                "role": "tool",
                                "name": fn,
                                "content": compress_tool_output(fn, tool_result),
                            }
                        )
                        # After injecting result, break to top of outer loop to retry
                        break
                logger.warning(f"[agent_loop] {model} error round={round_num}: {exc}")

        if response_message is None:
            # Recovery injected a tool result — retry the loop
            continue

        finish_reason = ""
        if hasattr(response_message, "tool_calls") and response_message.tool_calls:
            finish_reason = "tool_calls"
        elif hasattr(response_message, "content") and response_message.content:
            finish_reason = "stop"

        # ── Tool call round ───────────────────────────────────────────────────
        if finish_reason == "tool_calls":
            messages.append(normalize_assistant_message(response_message))  # Fix #5
            for tc in response_message.tool_calls:
                fn = tc.function.name
                args = parse_tool_args(tc.function.arguments)
                logger.info(f"[agent_loop] executing tool={fn} args={args}")

                result = await execute_tool_by_name(fn, args)
                tool_results.append({"tool": fn, "args": args})
                messages.append(
                    {
                        "tool_call_id": tc.id,
                        "role": "tool",
                        "name": fn,
                        "content": compress_tool_output(fn, result),
                    }
                )
            # Continue loop — model may want more tools or synthesis next round

        # ── Direct text reply — done ──────────────────────────────────────────
        elif finish_reason == "stop":
            return response_message.content

    # Loop exhausted without finish_reason=stop → force synthesis
    logger.warning("[agent_loop] max rounds reached, forcing synthesis")
    for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
        try:
            synth_messages = list(messages) + [
                {
                    "role": "system",
                    "content": (
                        "Synthesize the real estate data gathered above into a clear, "
                        "concise Markdown reply for the user. Do NOT call any more tools."
                    ),
                }
            ]
            final_res = await groq_call(
                model=model,
                messages=synth_messages,
                tools=None,
                tool_choice="none",
                max_tokens=2000,
                temperature=0.2,
            )
            return final_res.choices[0].message.content
        except Exception as exc:
            # Fix #2: Even here, recover generate_data_export from failed_generation
            failed_gen = extract_failed_generation(exc)
            fn, fn_args = parse_failed_generation(failed_gen)
            if fn == "generate_data_export" and isinstance(fn_args, dict):
                from services.appwrite_service import upload_document_to_appwrite

                content = fn_args.get("content", "")
                upload_res = await upload_document_to_appwrite(content, "md")
                dl_url = upload_res.get("download_url", "")
                if dl_url:
                    return f"{content}\n\n[Download Markdown Report]({dl_url})"
                return content
            logger.warning(f"[agent_loop] forced synthesis failed on {model}: {exc}")

    return None


# ─── STREAMING AGENT LOOP (for SSE) ───────────────────────────────────────────


async def run_agent_loop_streaming(
    messages: list[dict],
    active_tools: list | None,
    tool_results: list,
) -> AsyncIterator[dict]:
    """
    Streaming version of run_agent_loop for SSE delivery.

    Yields dicts:
      {"type": "tool_call", "tool": str, "args": dict}
      {"type": "token",     "token": str}
      {"type": "done",      "tools_called": list}

    Tool-call rounds run identically to the non-streaming loop. Only the
    final synthesis step streams tokens chunk-by-chunk.
    """
    loop = asyncio.get_event_loop()

    # Run all tool rounds (non-streaming, same as normal loop)
    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        response_message = None

        last_is_tool = bool(messages) and messages[-1].get('role') == 'tool'
        has_data = bool(tool_results) and last_is_tool
        tools_now = None if has_data else active_tools
        choice_now = 'none' if has_data else ('auto' if active_tools else 'none')
        tokens_now = 2000 if has_data else 300
        for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
            try:
                brain_res = await groq_call(
                    model=model,
                    messages=messages,
                    tools=tools_now,
                    tool_choice=choice_now,
                    max_tokens=tokens_now,
                    temperature=0.2,
                )
                response_message = brain_res.choices[0].message
                break
            except Exception as exc:
                failed_gen = extract_failed_generation(exc)
                if failed_gen and is_structural_error(exc):
                    fn, fn_args = parse_failed_generation(failed_gen)
                    if fn and isinstance(fn_args, dict):
                        tool_result = await execute_tool_by_name(fn, fn_args)
                        tool_results.append({"tool": fn, "args": fn_args})
                        messages.append(
                            {
                                "tool_call_id": f"recov_{int(time.time())}",
                                "role": "tool",
                                "name": fn,
                                "content": compress_tool_output(fn, tool_result),
                            }
                        )
                        yield {"type": "tool_call", "tool": fn, "args": fn_args}
                        break
                logger.warning(f"[stream_loop] {model} error round={round_num}: {exc}")

        if response_message is None:
            continue

        has_tool_calls = bool(getattr(response_message, "tool_calls", None))
        if has_tool_calls:
            messages.append(normalize_assistant_message(response_message))
            for tc in response_message.tool_calls:
                fn = tc.function.name
                args = parse_tool_args(tc.function.arguments)
                yield {"type": "tool_call", "tool": fn, "args": args}
                result = await execute_tool_by_name(fn, args)
                tool_results.append({"tool": fn, "args": args})
                messages.append(
                    {
                        "tool_call_id": tc.id,
                        "role": "tool",
                        "name": fn,
                        "content": compress_tool_output(fn, result),
                    }
                )
        else:
            # Model returned a direct text reply — stream it token by token
            if response_message.content:
                for token in _split_tokens(response_message.content):
                    yield {"type": "token", "token": token}
            yield {"type": "done", "tools_called": tool_results}
            return

    # Forced synthesis with streaming
    for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
        try:
            synth_messages = list(messages) + [
                {
                    "role": "system",
                    "content": "Synthesize the real estate data gathered above into a clear Markdown reply. Do NOT call any more tools.",
                }
            ]

            def _stream_call():
                return groq_client.chat.completions.create(
                    model=model,
                    messages=synth_messages,
                    temperature=0.2,
                    max_tokens=1000,
                    stream=True,
                )

            from services.groq_client import groq_client as _gc

            stream = await loop.run_in_executor(
                None,
                lambda: _gc.chat.completions.create(
                    model=model,
                    messages=synth_messages,
                    temperature=0.2,
                    max_tokens=1000,
                    stream=True,
                ),
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield {"type": "token", "token": delta}
            yield {"type": "done", "tools_called": tool_results}
            return
        except Exception as exc:
            logger.warning(f"[stream_loop] forced synthesis failed on {model}: {exc}")

    yield {"type": "token", "token": _REPLY_FALLBACK}
    yield {"type": "done", "tools_called": tool_results}


def _split_tokens(text: str, chunk_size: int = 4):
    """Split text into small chunks to simulate streaming for non-stream calls."""
    words = text.split(" ")
    buf = []
    for word in words:
        buf.append(word)
        if len(buf) >= chunk_size:
            yield " ".join(buf) + " "
            buf = []
    if buf:
        yield " ".join(buf)


# ─── CLARIFICATION PARSER ─────────────────────────────────────────────────────


def extract_clarification_options(reply: str) -> tuple[str, list[str]]:
    """Strip embedded JSON clarification options from the reply text."""
    suggested_actions: list[str] = []
    json_match = re.search(
        r'```json\s*(\{.*?"clarification_options".*?\})\s*```', reply, re.DOTALL
    )
    if not json_match:
        json_match = re.search(r'(\{.*?"clarification_options".*?\})', reply, re.DOTALL)

    if json_match:
        try:
            data = json.loads(json_match.group(1))
            suggested_actions = data.get("clarification_options", [])
            reply = reply.replace(json_match.group(0), "").strip()
        except json.JSONDecodeError:
            logger.error(f"Failed to parse clarification options from reply: {reply}")
    return reply, suggested_actions



# ─── HISTORY COMPRESSOR ───────────────────────────────────────────────────────

MAX_HISTORY_CHARS = 400  # Per-turn cap for stored assistant replies


def _compress_for_history(reply: str) -> str:
    """
    Strip heavy markdown formatting from an assistant reply before storing in session.
    Preserves table cell contents and key factual values while removing table borders,
    formatting markup, code fences, and redundant whitespace.
    """
    # Remove table divider lines like |---|---| or |:---:|
    text = re.sub(r"\|[\s\-:]+\|[\s\-:|]*", " ", reply)
    # Replace table column separator pipes with commas/spaces
    text = re.sub(r"\|", " , ", text)
    # Remove markdown headers, bold, italic, code fences, blockquotes, bullets
    text = re.sub(r"[*#`>\-~_]+", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_HISTORY_CHARS:
        return text[:MAX_HISTORY_CHARS] + "\u2026"
    return text


# ─── PUBLIC ENTRY POINT ───────────────────────────────────────────────────────


@observe(name="process-chat")
async def process_chat_message(
    user_query: str,
    session_id: str,
    session_context: dict,
) -> dict:
    """
    Main non-streaming handler. Returns a complete dict:
      {reply, path_used, tools_called, suggested_actions}
    """
    with propagate_attributes(session_id=session_id, tags=["real-estate-chat"]):
        get_client().update_current_span(input=user_query)

        # STEP 1: Route
        classification = await classify_query(user_query)

        if classification == "OUT_OF_SCOPE":
            get_client().update_current_span(output=_REPLY_OUT_OF_SCOPE)
            return {
                "reply": _REPLY_OUT_OF_SCOPE,
                "path_used": "OUT_OF_SCOPE",
                "tools_called": [],
                "suggested_actions": [],
            }

        if classification == "GREETING":
            get_client().update_current_span(output=_REPLY_GREETING)
            return {
                "reply": _REPLY_GREETING,
                "path_used": "GREETING",
                "tools_called": [],
                "suggested_actions": [],
            }

        # STEP 2: RAG retrieval for knowledge-base paths
        rag_chunks: list[str] = []
        if classification in ("PATH_B", "BOTH", "COMMERCIAL_HANDOFF"):
            rag_chunks = await search_methodology_rag(user_query)

        # STEP 3: Build message context
        system_prompt = build_system_prompt(classification)  # Fix #3
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(get_context_window(session_id))  # Fix #6 (trimmed history)

        user_msg = f"User Context Filters: {json.dumps(session_context)}\nUser Query: {user_query}"
        messages.append({"role": "user", "content": user_msg})
        append_message(session_id, {"role": "user", "content": user_msg})

        if rag_chunks:
            messages.append(
                {
                    "role": "system",
                    "content": "Retrieved Methodology Context:\n"
                    + "\n---\n".join(rag_chunks),
                }
            )

        # STEP 4: Select tools — use keyword-based subset to minimize tool definition tokens
        # select_tools() picks 4-6 relevant tools instead of sending all 18 (~3,130 tokens)
        active_tools = None
        if classification in ("PATH_A", "BOTH"):
            active_tools = select_tools(user_query)
            logger.info(f"[process_chat] selected {len(active_tools)} tools for classification={classification}")
        elif classification == "COMMERCIAL_HANDOFF":
            active_tools = COMMERCIAL_TOOLS

        # STEP 5: Run agentic loop (Fix #1)
        tool_results: list[dict] = []
        final_reply = await run_agent_loop(messages, active_tools, tool_results)

        if (
            not final_reply
            or final_reply.strip().startswith('{"name":')
            or final_reply.strip().startswith("<function=")
        ):
            final_reply = _REPLY_FALLBACK

        # STEP 6: Store compressed reply in session (full reply returned to user)
        # Storing full markdown in history triples token cost per turn.
        append_message(session_id, {
            "role": "assistant",
            "content": _compress_for_history(final_reply),
        })

        # STEP 7: Strip clarification options from reply text
        final_reply, suggested_actions = extract_clarification_options(final_reply)

        get_client().update_current_span(output=final_reply)

        return {
            "reply": final_reply,
            "path_used": classification,
            "tools_called": tool_results,
            "suggested_actions": suggested_actions,
        }


@observe(name="stream-chat")
async def stream_chat_message(
    user_query: str,
    session_id: str,
    session_context: dict,
) -> AsyncIterator[dict]:
    """
    SSE streaming handler.
    Yields: {"type": "status"|"tool_call"|"token"|"done", ...}
    """
    with propagate_attributes(
        session_id=session_id, tags=["real-estate-chat", "stream"]
    ):
        # STEP 1: Route
        classification = await classify_query(user_query)
        yield {"type": "status", "classification": classification}

        if classification == "OUT_OF_SCOPE":
            yield {"type": "token", "token": _REPLY_OUT_OF_SCOPE}
            yield {
                "type": "done",
                "tools_called": [],
                "suggested_actions": [],
                "path_used": "OUT_OF_SCOPE",
            }
            return

        if classification == "GREETING":
            yield {"type": "token", "token": _REPLY_GREETING}
            yield {
                "type": "done",
                "tools_called": [],
                "suggested_actions": [],
                "path_used": "GREETING",
            }
            return

        # STEP 2: RAG
        rag_chunks: list[str] = []
        if classification in ("PATH_B", "BOTH", "COMMERCIAL_HANDOFF"):
            rag_chunks = await search_methodology_rag(user_query)

        # STEP 3: Messages
        system_prompt = build_system_prompt(classification)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(get_context_window(session_id))
        user_msg = f"User Context Filters: {json.dumps(session_context)}\nUser Query: {user_query}"
        messages.append({"role": "user", "content": user_msg})
        append_message(session_id, {"role": "user", "content": user_msg})
        if rag_chunks:
            messages.append(
                {
                    "role": "system",
                    "content": "Retrieved Methodology Context:\n"
                    + "\n---\n".join(rag_chunks),
                }
            )

        # STEP 4: Tools — keyword-based subset selection
        active_tools = None
        if classification in ("PATH_A", "BOTH"):
            active_tools = select_tools(user_query)
            logger.info(f"[stream_chat] selected {len(active_tools)} tools for classification={classification}")
        elif classification == "COMMERCIAL_HANDOFF":
            active_tools = COMMERCIAL_TOOLS

        # STEP 5: Streaming agentic loop
        tool_results: list[dict] = []
        full_reply_parts: list[str] = []

        async for event in run_agent_loop_streaming(
            messages, active_tools, tool_results
        ):
            if event["type"] == "token":
                full_reply_parts.append(event["token"])
            yield event

        # STEP 6: Persist compressed reply to session (full text was streamed to user)
        full_reply = "".join(full_reply_parts)
        if full_reply:
            append_message(session_id, {
                "role": "assistant",
                "content": _compress_for_history(full_reply),
            })
