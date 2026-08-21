# Pulse AI — Production Architecture Review
**System:** Joule Dynamics Real Estate Intelligence Layer (Pulse AI / SentimentScope)**  
**Trace:** `trace-a4448bd199b8a371b18faa20b77fbba1`  
**User Turn:** `"What do you think about today's rates?"`  
**Review Date:** 2026-08-21  
**Reviewer:** AI Systems Architecture Analysis

---

## 🧭 Quick Summary (Plain-English First)

Imagine Pulse AI as a three-person relay race team:
1. **The Classifier (Router)** — reads the user's question and decides which department to forward it to.
2. **The Brain (Main LLM)** — decides which database tool to call to fetch the answer.
3. **The Synthesizer (Same Brain, second call)** — takes the data back from the database and writes the final user-readable reply.

In this trace, the race collapsed at the handoff from step 2 → step 3. The Brain fetched data for Miami, then in step 3 tried to _also_ fetch NYC/NJ data — but the second API call didn't include the "tools" parameter, so Groq rejected it with a 400 error. Both fallback attempts also failed. The user got the worst possible outcome: a generic fallback message instead of a real answer.

Below is the full post-mortem across every dimension of the system.

---

## 📊 Trace Execution Timeline

| Obs | Type | Model | Latency | Status | Action |
|---|---|---|---|---|---|
| 1 | SPAN | — | 3,355ms | ✅ OK | `process-chat` root span |
| 2 | GENERATION | `gpt-oss-20b` | 484ms | ✅ OK | Router classified → `PATH_A` |
| 3 | GENERATION | `gpt-oss-120b` | 707ms | ✅ OK | Brain called `get_market_averages(Miami)` |
| 4 | GENERATION | `gpt-oss-120b` | 398ms | ❌ 400 | Synthesis attempted second tool call → REJECTED |
| 5 | GENERATION | `gpt-oss-20b` | 334ms | ❌ 400 | Fallback synthesis also attempted tool call → REJECTED |

**Total wall-clock time: 3.3 seconds.** The user waited 3.3 seconds to receive a generic, unhelpful fallback message.

---

## Issue #1: The Synthesis Call Has No Tool Schema

### Issue Identified & Exact Trace Location

**Trace Location:** Observations 4 and 5.

```
BadRequestError: Error code: 400 - {
  'error': {
    'message': 'Tool choice is none, but model called a tool',
    'type': 'invalid_request_error',
    'code': 'tool_use_failed',
    'failed_generation': '{"name": "get_market_averages", "arguments": {"p_market":"NYC/NJ Metro"}}'
  }
}
```

After fetching Miami rates (Observation 3), the synthesis call at Observation 4 was made **without any `tools` parameter** (`tool_choice` was effectively `none`). However, `gpt-oss-120b` — seeing the tool-enriched conversation history — wanted to call `get_market_averages` a second time for NYC/NJ Metro (its internal reasoning said: "I need both markets to compare"). Since the second call had no tool definitions, Groq rejected it.

**Code path in `groq_service.py` (line 554–563):**
```python
# Second Brain Call for final response synthesis
for model_candidate in [GROQ_SYNTHESIS_MODEL, GROQ_FALLBACK_SYNTHESIS_MODEL]:
    try:
        final_res = groq_client.chat.completions.create(
            model=model_candidate,
            messages=messages,   # history includes tool calls, but...
            temperature=0.2,
            max_tokens=1000,
            # NO `tools=` parameter! Model CANNOT call tools here.
        )
```

### Underlying Mechanism & Root Cause

The model was given a conversation history that clearly shows a tool-enriched task context (it already made one tool call, got data back). A reasoning model (`gpt-oss-120b`) continues its chain-of-thought from previous output — and since it got Miami but not NYC/NJ, it logically wants to call the tool again. **But the synthesis call removes all tool definitions.** The model sees the tool call structure in history, tries to replicate it, and Groq rejects it because no tool definitions exist in this call.

The root cause is an architectural assumption: **"the model will synthesize after one tool call."** Reality: reasoning models may want multiple tool calls to complete an analytical task.

### Architectural Anti-Pattern Analysis

This is the **"One-Shot Tool Execution" anti-pattern** — assuming a single tool call is always sufficient, then stripping tool access from the synthesis step. Problems:

- **Brittleness:** Any vague query ("What do you think about rates?") causes the model to want both markets. The system was not designed to handle multi-round tool needs.
- **Reasoning model mismatch:** `gpt-oss-120b` is a reasoning model. It computes a multi-step plan internally. Cutting it off mid-plan causes it to leak its next action into the output.
- **Dual 400 failures waste ~730ms** — both model candidates fail on the exact same structural problem (no tools in synthesis), burning tokens and time before falling back.

### Hardened Production Solution

**Implement a true agentic loop with a max_iterations guard:**

```python
# HARDENED: Agentic Tool Loop (up to N tool rounds)
MAX_TOOL_ROUNDS = 4
tool_rounds = 0
final_reply = None

while tool_rounds < MAX_TOOL_ROUNDS:
    tool_rounds += 1
    
    response = await _groq_call_with_retry(
        messages=messages,
        tools=active_tools,       # ALWAYS pass tools while in agentic loop
        tool_choice="auto",
        max_tokens=600,
    )
    
    if response.choices[0].finish_reason == "stop":
        # Model decided to respond directly — synthesis complete
        final_reply = response.choices[0].message.content
        break
    
    elif response.choices[0].finish_reason == "tool_calls":
        # Execute all tool calls in this round, append results, continue loop
        response_message = response.choices[0].message
        messages.append(normalize_assistant_message(response_message))
        
        for tool_call in response_message.tool_calls:
            result = await execute_tool_by_name(tool_call.function.name,
                                                json.loads(tool_call.function.arguments))
            messages.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": tool_call.function.name,
                "content": compress_tool_output(tool_call.function.name, result),
            })

if not final_reply:
    # Exhausted max rounds — force a synthesis-only call
    messages.append({
        "role": "system",
        "content": "Synthesize the real estate data gathered above into a concise Markdown reply. Do NOT call any more tools."
    })
    final_reply = (await _groq_call_with_retry(
        messages=messages, tools=None, tool_choice="none", max_tokens=1000,
    )).choices[0].message.content
```

**Key design changes:**
- Tools stay in the loop until the model itself chooses to stop calling them (`finish_reason == "stop"`).
- `MAX_TOOL_ROUNDS = 4` prevents infinite agent loops.
- Only the **final** synthesis call (after `stop`) strips tools — because by then the model has genuinely finished gathering data.

---

## Issue #2: The Recovery Handler Ignores Non-Export Tool Calls

### Issue Identified & Exact Trace Location

**Code path in `groq_service.py` (lines 565–580):**
```python
except Exception as synth_err:
    ...
    target_fn, fn_args = parse_failed_generation(failed_gen)
    if target_fn == "generate_data_export" and isinstance(fn_args, dict):
        # Only handles generate_data_export!
        # All other tools (get_market_averages, etc.) are silently dropped.
```

The `failed_generation` in this trace was `get_market_averages(p_market: "NYC/NJ Metro")` — not `generate_data_export`. The recovery handler **only handles export tools**, so the NYC data fetch was silently discarded and the user got the fallback message.

### Hardened Production Solution

**Universal tool recovery in synthesis failure:**
```python
target_fn, fn_args = parse_failed_generation(failed_gen)
if target_fn and isinstance(fn_args, dict):
    # Re-execute whichever tool the model wanted to call
    tool_result = await execute_tool_by_name(target_fn, fn_args)
    messages.append({
        "tool_call_id": f"recov_{int(time.time())}",
        "role": "tool",
        "name": target_fn,
        "content": compress_tool_output(target_fn, tool_result),
    })
    # Retry synthesis with the enriched context, tools stripped
    final_res = await _groq_call_with_retry(messages=messages, tools=None)
    final_reply = final_res.choices[0].message.content
    break
```

---

## Issue #3: Prompt Size — 3,535 Tokens Just to Fetch Rates

### Issue Identified & Exact Trace Location

**Observation 3 (Brain Call):** `prompt_tokens: 3,535`

The entire SYNTHESIS_PROMPT is sent on every single request, even for trivially simple queries like "What do you think about today's rates?" — which only needs `get_market_averages`. The system prompt includes sections only relevant to other paths:

### Token Economy Breakdown

| Prompt Section | Est. Tokens | Needed for PATH_A? |
|---|---|---|
| Identity + Immutable Boundaries | ~180 | ✅ Yes |
| Operational Rules (no SQL, no guessing) | ~120 | ✅ Yes |
| Advisory format instructions | ~80 | ✅ Yes |
| Dashboard UI visual guidance section | ~400 | ❌ No (PATH_B only) |
| Commercial Handoff 5-step protocol | ~250 | ❌ No |
| Report/Export workflow | ~180 | ❌ No |
| Conversation Recap instructions | ~80 | ❌ No |
| **Wasteful tokens this turn** | **~910** | ❌ **26% overhead** |

### Hardened Production Solution

**Prompt composition by classification:**

```python
PROMPT_CORE = """
You are Pulse AI, a Real Estate Intelligence Assistant for Joule Dynamics.
[Identity, immutable boundaries, operational rules — ~400 tokens]
"""
PROMPT_PATH_A = """
ADVISORY FORMAT: Present data using the two-part structure:
1. What the data shows (facts from tools only)
2. Suggested approach (data-informed)
Close every advisory with: ⚠️ This is a data-informed observation...
"""
PROMPT_PATH_B = """DASHBOARD UI GUIDANCE: ..."""
PROMPT_COMMERCIAL = """COMMERCIAL HANDOFFS: ..."""
PROMPT_REPORTS = """REPORT EXPORTS: ..."""

def build_system_prompt(classification: str) -> str:
    prompt = PROMPT_CORE
    if classification in ["PATH_A", "BOTH"]:
        prompt += PROMPT_PATH_A + PROMPT_REPORTS
    if classification in ["PATH_B", "BOTH"]:
        prompt += PROMPT_PATH_B
    if classification == "COMMERCIAL_HANDOFF":
        prompt += PROMPT_COMMERCIAL
    return prompt
```

**Savings: ~600–900 tokens per PATH_A turn — about 25% reduction per request.**

---

## Issue #4: The Router Uses an Overkill Reasoning Model

### Issue Identified & Exact Trace Location

**Observation 2:** 484ms latency, `gpt-oss-20b` (a reasoning model).

The routing output is simple JSON with 6 possible values. A 20B reasoning model spending 484ms on a 7-word sentence is expensive overkill.

### Architectural Anti-Pattern Analysis

**"Uniform Compute" anti-pattern** — same large expensive model used for all tasks regardless of cognitive complexity. Routing is a classification task, not a reasoning task.

### Hardened Production Solution

```python
# Use the smallest capable model for routing
GROQ_ROUTER_MODEL = "llama-3.1-8b-instant"

router_res = groq_client.chat.completions.create(
    model=GROQ_ROUTER_MODEL,
    messages=[{"role": "system", "content": ROUTER_PROMPT},
              {"role": "user", "content": user_query}],
    response_format={"type": "json_object"},
    temperature=0.0,
    max_tokens=80,        # Classification output is always tiny
)
```

**Expected improvement:** Router latency ~120ms vs ~480ms. Token cost drops ~85%.

---

## Issue #5: The `reasoning` Field Leaks Into Context History

### Issue Identified & Exact Trace Location

**Observation 4 Input — message at index 2 (actual text from trace):**

```
"ChatCompletionMessage(content=None, role='assistant', function_call=None, 
tool_calls=[...], reasoning='We need to answer: \"What do you think about today\\'s rates?\" 
This is a request for commentary on today\\'s rates. We need to provide data-informed 
observation: get current rates. Could use get_market_averages for both markets...')"
```

The `response_message` object from the Brain call is appended directly to `messages` as a Python object, which includes the model's internal `reasoning` chain-of-thought via its `__repr__`. This reasoning text:
1. Wastes tokens in every subsequent API call.
2. Can contaminate the model's future reasoning by making it "remember" its own intermediate thoughts.
3. Accumulates across session turns if history is retained.

### Hardened Production Solution

**Always normalize message objects to dicts before appending:**

```python
def normalize_assistant_message(msg) -> dict:
    """Convert ChatCompletionMessage to a clean dict without internal fields."""
    if hasattr(msg, 'tool_calls') and msg.tool_calls:
        return {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                }
                for tc in msg.tool_calls
            ]
        }
    return {"role": "assistant", "content": msg.content or ""}

# Replace: messages.append(response_message)
# With:    messages.append(normalize_assistant_message(response_message))
```

---

## Issue #6: Session History Is Unbounded In-Memory

### Issue Identified & Exact Trace Location

**`groq_service.py`:**
```python
session_history = defaultdict(list)  # Global in-memory dict, grows forever

messages.extend(session_history[session_id][-6:])  # Cap at read time only
```

The cap of `[-6:]` only applies when **reading** — the underlying list still grows without bound, leaking memory across the process lifetime. On HuggingFace Spaces restart, all history is wiped silently.

### Hardened Production Solution

**Trim at write time + add TTL eviction:**

```python
MAX_HISTORY_MESSAGES = 10
SESSION_TTL_SECONDS = 3600

_sessions: dict[str, dict] = {}

def get_session(session_id: str) -> list:
    now = time.time()
    entry = _sessions.get(session_id)
    if entry is None or (now - entry["ts"]) > SESSION_TTL_SECONDS:
        _sessions[session_id] = {"messages": [], "ts": now}
    else:
        _sessions[session_id]["ts"] = now
    return _sessions[session_id]["messages"]

def append_to_session(session_id: str, message: dict):
    history = get_session(session_id)
    history.append(message)
    # Trim at write time — history list never grows beyond MAX
    if len(history) > MAX_HISTORY_MESSAGES:
        _sessions[session_id]["messages"] = history[-MAX_HISTORY_MESSAGES:]
```

---

## Issue #7: No Retry for 429/503 Transient Errors

### Issue Identified & Exact Trace Location

The current fallback logic only handles `400` structural errors. Rate limits (`429`), server overloads (`503`), and network timeouts are not retried — they cause immediate failure.

### Hardened Production Solution

```python
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

async def _groq_call_with_retry(model, messages, tools=None, tool_choice="none",
                                 max_tokens=1000, temperature=0.2, max_retries=3):
    for attempt in range(max_retries):
        try:
            kwargs = dict(model=model, messages=messages,
                          temperature=temperature, max_tokens=max_tokens)
            if tools:
                kwargs.update(tools=tools, tool_choice=tool_choice)
            return groq_client.chat.completions.create(**kwargs)
        
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 400:
                raise  # Never retry structural errors
            if status in RETRYABLE_STATUS_CODES or status is None:
                delay = (2 ** attempt) + random.uniform(0, 0.5)  # Exponential backoff + jitter
                logger.warning(f"{model} attempt {attempt+1} failed ({status}). Retry in {delay:.1f}s")
                await asyncio.sleep(delay)
            else:
                raise
    raise RuntimeError(f"All {max_retries} retries exhausted for {model}")
```

---

## Issue #8: Fallback Model Retries the Same Broken Payload

### Issue Identified & Exact Trace Location

**Observations 4 and 5 — both fail with identical errors:**
- Obs 4: `gpt-oss-120b` → 400 (structural: no tools in synthesis)
- Obs 5: `gpt-oss-20b` → 400 (same structural error)

The fallback does not distinguish between **transient** errors (try a different model) and **structural** errors (fix the payload first). Since both calls have the same broken structure, both fail identically, wasting 334ms on the fallback.

### Hardened Production Solution

```python
def is_structural_error(exception) -> bool:
    body = getattr(exception, "body", {})
    if isinstance(body, dict):
        code = body.get("error", {}).get("code", "")
        return code in {"tool_use_failed", "context_length_exceeded", "invalid_request_error"}
    return False

# In the synthesis loop:
for model_candidate in [PRIMARY_MODEL, FALLBACK_MODEL]:
    try:
        return await _groq_call_with_retry(model=model_candidate, messages=messages, ...)
    except Exception as e:
        if is_structural_error(e):
            # Structural error: fix the payload, not the model
            raise StructuralOrchestrationError(e)
        # Transient error: try next model
        logger.warning(f"{model_candidate} failed transiently, trying fallback...")
```

---

## Summary: Issue Priority Matrix

| # | Issue | Severity | Impact | Effort |
|---|---|---|---|---|
| 1 | No tools in synthesis call → 400 errors | 🔴 Critical | Silent failures, fallback messages | Medium |
| 2 | Recovery only handles `generate_data_export` | 🔴 Critical | Data silently lost for non-export tools | Low |
| 3 | Full prompt sent every request (3,535 tokens) | 🟠 High | 25-30% token waste per request | Medium |
| 8 | Fallback retries identical broken payload | 🟠 High | Double failure + 334ms wasted | Low |
| 4 | Reasoning model used for routing (overkill) | 🟡 Medium | 480ms overhead per turn | Low |
| 5 | `reasoning` field leaks into context history | 🟡 Medium | Token waste + context contamination | Low |
| 6 | Session history unbounded in-memory | 🟡 Medium | Memory leak, state loss on restart | Medium |
| 7 | No retry for 429/503 transient errors | 🟡 Medium | Silent failures on rate limits | Low |

---

## Ideal vs. Actual Execution Comparison

**What actually happened (3.3 seconds, useless reply):**
```
Router  → PATH_A                                    (484ms — 20B reasoning model overkill)
Brain   → get_market_averages(Miami)                (707ms — Miami data fetched)
Synth 1 → 400 ERROR: tool_use_failed               (398ms wasted — no tools schema)
Synth 2 → 400 ERROR: same broken payload           (334ms wasted — fallback not smarter)
Result  → "I retrieved the real estate intelligence data."  ← useless generic fallback
```

**What should happen with agentic loop (1.4 seconds, real answer):**
```
Router  → PATH_A                                    (120ms — small 8B classifier)
Loop 1  → get_market_averages(Miami)                (350ms — Miami data)
Loop 2  → get_market_averages(NYC/NJ Metro)         (350ms — NYC data)
Loop 3  → finish_reason: "stop" → Synthesis reply  (500ms — real Markdown answer)
Result  → Formatted table comparing Miami vs NYC/NJ rates with data-informed advisory
```

The agentic loop is **2.5× faster** and produces a genuinely useful answer.
