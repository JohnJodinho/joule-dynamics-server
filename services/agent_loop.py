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
from services.tools import REAL_ESTATE_TOOLS, COMMERCIAL_TOOLS

logger = setup_logger(__name__)

MAX_TOOL_ROUNDS = 4   # Maximum tool-call rounds before forcing synthesis

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

async def classify_query(user_query: str) -> str:
    """
    Use a small, fast model to classify the query into one of 6 route tags.
    Fix #4: Previously used a 20B reasoning model (484ms). Now uses the
    lightweight GROQ_ROUTE_MODEL (default: llama-3.1-8b-instant, ~120ms).
    """
    messages = [
        {"role": "system", "content": ROUTER_PROMPT},
        {"role": "user", "content": user_query},
    ]
    # Try primary route model, fall back to secondary
    for model in [GROQ_ROUTE_MODEL, GROQ_FALLBACK_ROUTE_MODEL]:
        try:
            res = await groq_call(
                model=model,
                messages=messages,
                max_tokens=80,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw = res.choices[0].message.content or "{}"
            data = json.loads(raw)
            classification = data.get("classification", "PATH_A")
            logger.info(f"[router] {model} -> {classification}: {data.get('reason', '')}")
            return classification
        except Exception as exc:
            if is_structural_error(exc):
                logger.warning(f"[router] structural error on {model}: {exc}")
            else:
                logger.warning(f"[router] {model} failed, trying fallback: {exc}")
    return "PATH_A"   # Safe default


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
        for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
            try:
                brain_res = await groq_call(
                    model=model,
                    messages=messages,
                    tools=active_tools if active_tools else None,
                    tool_choice="auto" if active_tools else "none",
                    max_tokens=600 if active_tools else 1000,
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
                        logger.info(f"[agent_loop] recovering tool={fn} from failed_generation")
                        tool_result = await execute_tool_by_name(fn, fn_args)
                        tool_results.append({"tool": fn, "args": fn_args})
                        messages.append({
                            "tool_call_id": f"recov_{int(time.time())}",
                            "role": "tool",
                            "name": fn,
                            "content": compress_tool_output(fn, tool_result),
                        })
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
                fn   = tc.function.name
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
            # Continue loop — model may want more tools or synthesis next round

        # ── Direct text reply — done ──────────────────────────────────────────
        elif finish_reason == "stop":
            return response_message.content

    # Loop exhausted without finish_reason=stop → force synthesis
    logger.warning("[agent_loop] max rounds reached, forcing synthesis")
    for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
        try:
            synth_messages = list(messages) + [{
                "role": "system",
                "content": (
                    "Synthesize the real estate data gathered above into a clear, "
                    "concise Markdown reply for the user. Do NOT call any more tools."
                ),
            }]
            final_res = await groq_call(
                model=model,
                messages=synth_messages,
                tools=None,
                tool_choice="none",
                max_tokens=1000,
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

        for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
            try:
                brain_res = await groq_call(
                    model=model,
                    messages=messages,
                    tools=active_tools if active_tools else None,
                    tool_choice="auto" if active_tools else "none",
                    max_tokens=600,
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
                        messages.append({
                            "tool_call_id": f"recov_{int(time.time())}",
                            "role": "tool",
                            "name": fn,
                            "content": compress_tool_output(fn, tool_result),
                        })
                        yield {"type": "tool_call", "tool": fn, "args": fn_args}
                        break
                logger.warning(f"[stream_loop] {model} error round={round_num}: {exc}")

        if response_message is None:
            continue

        has_tool_calls = bool(getattr(response_message, "tool_calls", None))
        if has_tool_calls:
            messages.append(normalize_assistant_message(response_message))
            for tc in response_message.tool_calls:
                fn   = tc.function.name
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
            synth_messages = list(messages) + [{
                "role": "system",
                "content": "Synthesize the real estate data gathered above into a clear Markdown reply. Do NOT call any more tools.",
            }]

            def _stream_call():
                return groq_client.chat.completions.create(
                    model=model,
                    messages=synth_messages,
                    temperature=0.2,
                    max_tokens=1000,
                    stream=True,
                )

            from services.groq_client import groq_client as _gc
            stream = await loop.run_in_executor(None, lambda: _gc.chat.completions.create(
                model=model,
                messages=synth_messages,
                temperature=0.2,
                max_tokens=1000,
                stream=True,
            ))
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
            pass
    return reply, suggested_actions


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
            return {"reply": _REPLY_OUT_OF_SCOPE, "path_used": "OUT_OF_SCOPE",
                    "tools_called": [], "suggested_actions": []}

        if classification == "GREETING":
            get_client().update_current_span(output=_REPLY_GREETING)
            return {"reply": _REPLY_GREETING, "path_used": "GREETING",
                    "tools_called": [], "suggested_actions": []}

        # STEP 2: RAG retrieval for knowledge-base paths
        rag_chunks: list[str] = []
        if classification in ("PATH_B", "BOTH", "COMMERCIAL_HANDOFF"):
            rag_chunks = await search_methodology_rag(user_query)

        # STEP 3: Build message context
        system_prompt = build_system_prompt(classification)  # Fix #3
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(get_context_window(session_id))       # Fix #6 (trimmed history)

        user_msg = f"User Context Filters: {json.dumps(session_context)}\nUser Query: {user_query}"
        messages.append({"role": "user", "content": user_msg})
        append_message(session_id, {"role": "user", "content": user_msg})

        if rag_chunks:
            messages.append({
                "role": "system",
                "content": "Retrieved Methodology Context:\n" + "\n---\n".join(rag_chunks),
            })

        # STEP 4: Select tools
        active_tools = None
        if classification in ("PATH_A", "BOTH"):
            active_tools = REAL_ESTATE_TOOLS
        elif classification == "COMMERCIAL_HANDOFF":
            active_tools = COMMERCIAL_TOOLS

        # STEP 5: Run agentic loop (Fix #1)
        tool_results: list[dict] = []
        final_reply = await run_agent_loop(messages, active_tools, tool_results)

        if not final_reply or final_reply.strip().startswith('{"name":') or final_reply.strip().startswith("<function="):
            final_reply = _REPLY_FALLBACK

        # STEP 6: Store assistant reply in session
        append_message(session_id, {"role": "assistant", "content": final_reply})

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
    with propagate_attributes(session_id=session_id, tags=["real-estate-chat", "stream"]):

        # STEP 1: Route
        classification = await classify_query(user_query)
        yield {"type": "status", "classification": classification}

        if classification == "OUT_OF_SCOPE":
            yield {"type": "token", "token": _REPLY_OUT_OF_SCOPE}
            yield {"type": "done", "tools_called": [], "suggested_actions": [],
                   "path_used": "OUT_OF_SCOPE"}
            return

        if classification == "GREETING":
            yield {"type": "token", "token": _REPLY_GREETING}
            yield {"type": "done", "tools_called": [], "suggested_actions": [],
                   "path_used": "GREETING"}
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
            messages.append({
                "role": "system",
                "content": "Retrieved Methodology Context:\n" + "\n---\n".join(rag_chunks),
            })

        # STEP 4: Tools
        active_tools = None
        if classification in ("PATH_A", "BOTH"):
            active_tools = REAL_ESTATE_TOOLS
        elif classification == "COMMERCIAL_HANDOFF":
            active_tools = COMMERCIAL_TOOLS

        # STEP 5: Streaming agentic loop
        tool_results: list[dict] = []
        full_reply_parts: list[str] = []

        async for event in run_agent_loop_streaming(messages, active_tools, tool_results):
            if event["type"] == "token":
                full_reply_parts.append(event["token"])
            yield event

        # STEP 6: Persist assembled reply to session
        full_reply = "".join(full_reply_parts)
        if full_reply:
            append_message(session_id, {"role": "assistant", "content": full_reply})
