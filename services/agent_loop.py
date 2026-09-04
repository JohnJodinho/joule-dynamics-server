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
from services.tools import REAL_ESTATE_TOOLS, COMMERCIAL_TOOLS, SUGGEST_ACTIONS_TOOL, discover_tools, select_tools

logger = setup_logger(__name__)

MAX_TOOL_ROUNDS = 4  # Maximum tool-call rounds before forcing synthesis

# ─── UNIVERSAL STREAM STRIPPER ────────────────────────────────────────────────
# Suppresses internal model tokens (<think>, <tool_call>, <|constrain|>) from
# reaching the SSE text stream. Operates as a stateful generator filter.

def _strip_internal_tokens(delta_iter):
    """
    Generator filter that consumes raw delta strings and yields only clean
    user-facing text, suppressing:
      - <think>...</think>  (Qwen reasoning blocks)
      - <tool_call>...</tool_call>  (Qwen XML tool attempts)
      - <|constrain|>...<|message|>...  (GPT-OSS internal delimiters)
    """
    buf = ""
    suppressing = False  # True when inside a block we need to discard
    suppress_end = ""    # The closing tag we're waiting for

    for delta in delta_iter:
        buf += delta

        while buf:
            if not suppressing:
                # Check for any suppression start marker
                for start_tag, end_tag in [
                    ("<think>", "</think>"),
                    ("<tool_call>", "</tool_call>"),
                    ("<|constrain|>", None),  # suppress everything from here to end
                ]:
                    idx = buf.find(start_tag)
                    if idx != -1:
                        # Yield everything before the tag
                        before = buf[:idx]
                        if before:
                            yield before
                        buf = buf[idx + len(start_tag):]
                        suppressing = True
                        suppress_end = end_tag
                        break
                else:
                    # No suppression marker found.
                    # Hold back partial tag matches at the tail (e.g. "<thi" could become "<think>")
                    # Only need to worry about '<' at the end
                    last_lt = buf.rfind("<")
                    if last_lt != -1 and last_lt > len(buf) - 15:
                        # Could be a partial tag — hold it
                        yield buf[:last_lt]
                        buf = buf[last_lt:]
                        break
                    else:
                        yield buf
                        buf = ""
            else:
                # We are suppressing — look for the end tag
                if suppress_end is None:
                    # <|constrain|> — suppress everything to end of stream
                    buf = ""
                    break
                end_idx = buf.find(suppress_end)
                if end_idx != -1:
                    # Skip past the closing tag
                    buf = buf[end_idx + len(suppress_end):]
                    # Strip leading whitespace/newlines after closing tag
                    buf = buf.lstrip("\n\r ")
                    suppressing = False
                else:
                    # Haven't found end tag yet — discard buffer, wait for more
                    buf = ""
                    break

    # Flush remaining buffer if not suppressing
    if buf and not suppressing:
        yield buf




# Synthesis directive appended to Turn 2 messages — prevents models from
# attempting manual tool re-invocations when tools=None
_SYNTHESIS_DIRECTIVE = {
    "role": "system",
    "content": (
        "You are now delivering your final response directly to the user in clear Markdown. "
        "Synthesize all real estate data gathered above into a helpful analysis. "
        "If any required parameters (dates, market name) were missing or a tool returned an error, "
        "politely ask the user for clarification in your prose — do NOT output XML tags, tool calls, "
        "code blocks, or pseudo-function syntax of any kind. Respond only in natural language Markdown."
    ),
}

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


async def classify_query(user_query: str) -> tuple[str, list[str]]:
    """
    Classify the user query and extract tool categories for category gating.

    Returns:
      (classification, tool_categories) e.g. ("PATH_A", ["MARKET", "ANOMALY"])
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
                max_tokens=600,
                temperature=0.0,
            )
            raw = res.choices[0].message.content or "{}"
            data = _extract_json_from_text(raw)
            classification = data.get("classification", "")
            tool_categories = data.get("tool_categories", [])
            if isinstance(tool_categories, str):
                tool_categories = [tool_categories]
            elif not isinstance(tool_categories, list):
                tool_categories = []

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

    return "PATH_A", []  # Safe default


# ─── AGENTIC TOOL LOOP ────────────────────────────────────────────────────────



# ─── TURN 3: DYNAMIC ACTION RESOLUTION (Strictly Forced Tool Call) ──────────────

ACTION_RESOLVER_PROMPT = """You are an interactive action generator for Joule Dynamics Real Estate Intelligence.
Given the user's query and the assistant's final response, determine 0 to 4 short, highly relevant follow-up actions or clarifying choices for the user.
Guidelines:
- If the assistant asked a clarifying question (e.g. which market or date), provide those exact choices (e.g. ["Miami", "NYC/NJ Metro"]).
- If the assistant provided market/price analysis, suggest logical next-step actions (e.g. ["Compare with Miami", "See Rate Volatility", "Generate Download Report"]).
- If the conversation is complete, a simple greeting, or no follow-up is genuinely useful, return an empty array actions: [].
- You must return ONLY via the suggest_actions tool call. Do not force suggestions if none are genuinely helpful."""


async def resolve_suggested_actions(user_query: str, assistant_reply: str) -> list[str]:
    """
    Turn 3: Decoupled Action Resolution.
    Invokes the model with forced tool_choice on suggest_actions.
    Because tool_choice is forced and no text is generated, this CANNOT leak tool syntax into markdown.
    """
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
                max_tokens=250,
                temperature=0.0,
            )
            msg = res.choices[0].message
            if getattr(msg, "tool_calls", None):
                tc = msg.tool_calls[0]
                args = parse_tool_args(tc.function.arguments)
                actions = args.get("actions") or args.get("options") or []
                if isinstance(actions, list):
                    return [str(a).strip() for a in actions if str(a).strip()][:4]
            return []
        except Exception as exc:
            logger.warning(f"[resolve_actions] error on {model}: {exc}")

    return []


async def run_agent_loop(
    messages: list[dict],
    active_tools: list | None,
    tool_results: list,
    user_query: str = "",
    suggested_actions_out: list | None = None,
) -> str | None:
    """
    3-Turn Agentic State Machine (Non-streaming):
      Turn 1: Data Retrieval loop (up to MAX_TOOL_ROUNDS=4 consecutive data tool calls).
      Turn 2: Markdown Synthesis with tools=None (structurally immune to tool-call leakage).
      Turn 3: Action Resolution via resolve_suggested_actions() with forced tool_choice.
    """
    # ── Turn 1: Multi-round data tool retrieval (up to 4 consecutive tool calls) ─
    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        if not active_tools:
            break

        response_message = None
        for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
            try:
                brain_res = await groq_call(
                    model=model,
                    messages=messages,
                    tools=active_tools,
                    tool_choice="auto",
                    max_tokens=2500,
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
                        break
                logger.warning(f"[agent_loop] {model} error round={round_num}: {exc}")

        if response_message is None:
            continue

        has_tool_calls = bool(getattr(response_message, "tool_calls", None))
        if has_tool_calls:
            messages.append(normalize_assistant_message(response_message))
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
            # Continue data loop — model may request another consecutive tool call
        else:
            # Model decided it has gathered all necessary data
            break

    # ── Turn 2: Markdown Synthesis (tools=None guarantees pure markdown) ────────
    synth_messages = list(messages) + [_SYNTHESIS_DIRECTIVE]
    reply_text = ""
    for model in [GROQ_ROUTE_MODEL, GROQ_FALLBACK_ROUTE_MODEL]:
        try:
            synth_res = await groq_call(
                model=model,
                messages=synth_messages,
                tools=None,
                tool_choice="none",
                max_tokens=2500,
                temperature=0.2,
            )
            raw = synth_res.choices[0].message.content or ""
            # Strip any residual <think> or <tool_call> blocks from non-streaming
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"<tool_call>.*?</tool_call>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"<\|constrain\|>.*", "", raw, flags=re.DOTALL).strip()
            reply_text = raw
            break
        except Exception as exc:
            logger.warning(f"[agent_loop] synthesis failed on {model}: {exc}")

    if not reply_text:
        reply_text = _REPLY_FALLBACK

    # ── Turn 3: Action Resolution (Forced suggest_actions call) ─────────────────
    if suggested_actions_out is not None:
        actions = await resolve_suggested_actions(user_query, reply_text)
        suggested_actions_out.extend(actions)

    return reply_text


# ─── STREAMING AGENT LOOP (for SSE) ───────────────────────────────────────────

async def run_agent_loop_streaming(
    messages: list[dict],
    active_tools: list | None,
    tool_results: list,
    user_query: str = "",
    suggested_actions_out: list | None = None,
) -> AsyncIterator[dict]:
    """
    3-Turn Agentic State Machine (Streaming SSE):
      Turn 1: Data Retrieval loop (up to MAX_TOOL_ROUNDS=4 consecutive data tool calls).
      Turn 2: Live Markdown Synthesis with tools=None (streams tokens live, zero leaked syntax).
      Turn 3: Action Resolution via resolve_suggested_actions() emitted in event: done.
    """
    loop = asyncio.get_event_loop()

    # ── Turn 1: Multi-round data tool retrieval (up to 4 consecutive tool calls) ─
    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        if not active_tools:
            break

        response_message = None
        for model in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
            try:
                brain_res = await groq_call(
                    model=model,
                    messages=messages,
                    tools=active_tools,
                    tool_choice="auto",
                    max_tokens=2500,
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
            # Continue data loop — model may request another consecutive tool call
        else:
            # Model finished calling data tools
            break

    # ── Turn 2: Live Markdown Synthesis with tools=None (Zero tool leakage!) ────
    synth_messages = list(messages) + [_SYNTHESIS_DIRECTIVE]
    full_reply_text = ""
    from services.groq_client import groq_client as _gc

    synthesis_succeeded = False
    for model in [GROQ_ROUTE_MODEL, GROQ_FALLBACK_ROUTE_MODEL]:
        try:
            stream = await loop.run_in_executor(
                None,
                lambda m=model: _gc.chat.completions.create(
                    model=m,
                    messages=synth_messages,
                    tools=None,
                    tool_choice="none",
                    temperature=0.2,
                    max_tokens=2500,
                    stream=True,
                ),
            )

            # Raw delta generator from the stream
            def _raw_deltas():
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield delta

            # Pass through universal stripper before yielding to SSE
            for clean_token in _strip_internal_tokens(_raw_deltas()):
                if clean_token:
                    full_reply_text += clean_token
                    yield {"type": "token", "token": clean_token}
            synthesis_succeeded = True
            break
        except Exception as exc:
            logger.warning(f"[stream_loop] synthesis failed on {model}: {exc}")

    if not synthesis_succeeded:
        full_reply_text = _REPLY_FALLBACK
        yield {"type": "token", "token": _REPLY_FALLBACK}

    # ── Turn 3: Action Resolution (Forced suggest_actions call) ─────────────────
    suggested_actions = await resolve_suggested_actions(user_query, full_reply_text)
    if suggested_actions_out is not None:
        suggested_actions_out.extend(suggested_actions)

    yield {
        "type": "done",
        "tools_called": tool_results,
        "suggested_actions": suggested_actions,
    }


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
def _clean_reply_text(text: str) -> str:
    """Strip any dummy markdown action links e.g. [Label](action:...) or [Label](#) to prevent UI duplication."""
    if not text:
        return ""
    # Remove dummy action links like [Label](action:xxx) or [Label](action:...)
    cleaned = re.sub(r'\[([^\]]+)\]\(action:[^\)]*\)', r'', text)
    # Clean up double blank lines or dangling "Please choose one of the buttons below:" if buttons were stripped
    cleaned = re.sub(r'(?i)\*\*Please (?:choose|select|click) one of the buttons below:?\*\*\s*$', '', cleaned)
    cleaned = re.sub(r'(?i)Please (?:choose|select|click) one of the buttons below:?\s*$', '', cleaned)
    return cleaned.strip()


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

        # STEP 1: Route & Category Gating
        classification, tool_categories = await classify_query(user_query)

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

        # STEP 4: Dynamic Tool Discovery (Context-Enriched Semantic Embedding + Category Gating)
        active_tools = None
        if classification in ("PATH_A", "BOTH"):
            # Context enrichment for short/ambiguous queries (e.g. 'Miami' following a clarification)
            discovery_query = user_query
            if len(user_query.strip()) < 25 and len(messages) > 2:
                # Find previous user message
                for prev_msg in reversed(messages[:-1]):
                    if prev_msg.get("role") == "user":
                        prev_content = prev_msg.get("content", "")
                        discovery_query = f"{prev_content} {user_query}"
                        break
            active_tools = discover_tools(discovery_query, categories=tool_categories, top_k=4)
            logger.info(f"[process_chat] discovered {len(active_tools)} tools for classification={classification} categories={tool_categories}")
        elif classification == "COMMERCIAL_HANDOFF":
            active_tools = COMMERCIAL_TOOLS
        elif classification == "PATH_B":
            # Universal tool support: allow PATH_B to suggest actions/clarifications natively
            active_tools = [SUGGEST_ACTIONS_TOOL]

        # STEP 5: Run agentic loop
        tool_results: list[dict] = []
        suggested_actions_list: list[str] = []
        final_reply = await run_agent_loop(
            messages, active_tools, tool_results, suggested_actions_out=suggested_actions_list
        )

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

        # STEP 7: Strip clarification options if any and merge suggested actions
        final_reply, extracted_actions = extract_clarification_options(final_reply)
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
    """
    SSE streaming handler.
    Yields: {"type": "status"|"tool_call"|"token"|"done", ...}
    """
    with propagate_attributes(
        session_id=session_id, tags=["real-estate-chat", "stream"]
    ):
        # STEP 1: Route & Category Gating
        classification, tool_categories = await classify_query(user_query)
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

        # STEP 4: Dynamic Tool Discovery (Context-Enriched Semantic Embedding + Category Gating)
        active_tools = None
        if classification in ("PATH_A", "BOTH"):
            discovery_query = user_query
            if len(user_query.strip()) < 25 and len(messages) > 2:
                for prev_msg in reversed(messages[:-1]):
                    if prev_msg.get("role") == "user":
                        prev_content = prev_msg.get("content", "")
                        discovery_query = f"{prev_content} {user_query}"
                        break
            active_tools = discover_tools(discovery_query, categories=tool_categories, top_k=4)
            logger.info(f"[stream_chat] discovered {len(active_tools)} tools for classification={classification} categories={tool_categories}")
        elif classification == "COMMERCIAL_HANDOFF":
            active_tools = COMMERCIAL_TOOLS
        elif classification == "PATH_B":
            # Universal tool support: allow PATH_B to suggest actions/clarifications natively
            active_tools = [SUGGEST_ACTIONS_TOOL]

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
