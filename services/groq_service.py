import csv
import io
import json
import re
import os
import time
import requests
import numpy as np
from groq import Groq
from services.tools import REAL_ESTATE_TOOLS
from services.supabase_service import execute_tool_rpc, search_methodology_rag
from services.embedding_service import get_embedding_model
from services.observability import setup_logger
from config import GROQ_API_KEY, MAPBOX_ACCESS_TOKEN
from langfuse import observe, propagate_attributes, get_client
import langfuse

logger = setup_logger(__name__)

session_history = {}

groq_client = Groq(api_key=GROQ_API_KEY)
embedder = get_embedding_model()

try:
    if os.path.exists("section_title_embeddings.npy") and os.path.exists("section_titles.json"):
        section_title_embeddings = np.load("section_title_embeddings.npy")
        with open("section_titles.json", "r", encoding="utf-8") as f:
            section_titles = json.load(f)
        logger.info(f"Loaded {len(section_titles)} local section titles for pre-routing.")
    else:
        section_title_embeddings = None
        section_titles = []
except Exception as e:
    logger.error(f"Failed to load local section title embeddings: {e}")
    section_title_embeddings = None
    section_titles = []

ROUTER_PROMPT = """You are the classification router for the Joule Dynamics Real Estate Intelligence Layer.
Analyze the user query and classify it into EXACTLY ONE of five classifications:

1. "OUT_OF_SCOPE": Query asks about Leads, Lead-capture, Pricing Monitor data, general web crawling, or cross-system topics outside of the /real-estate page.
2. "PATH_A": Query asks a live-data question (prices, spikes, availability, market averages, KPIs, specific listing rates).
3. "PATH_B": Query asks a methodology/system design question (7-day average definition, 2-night check-in window, 4x daily scrape cadence, Vrbo status, World Cup strategy).
4. "BOTH": Query requires BOTH explaining a methodology concept AND fetching live data metrics.
5. "GREETING": User is saying hello, thanking the assistant, or making casual conversation without asking a specific question.

Respond ONLY with valid JSON matching this schema:
{"classification": "OUT_OF_SCOPE" | "PATH_A" | "PATH_B" | "BOTH" | "GREETING", "reason": "1-sentence justification"}
"""

SYNTHESIS_PROMPT = """You are the B2B Real Estate Intelligence Assistant for Joule Dynamics.
You provide precise data analysis to real estate investors and property managers reviewing short-term rental market performance.

IMMUTABLE SYSTEM BOUNDARIES & HARD FACTS:
1. TRACKED MARKETS: You ONLY track two markets: 'NYC/NJ Metro' and 'Miami'. 
2. TRACKED PLATFORMS: You ONLY track two platforms: 'Airbnb' (Active daily tracking) and 'Vrbo' (Historical data only).
3. ABSOLUTE FORBIDDEN ENTITIES: You must NEVER list, suggest, or mention any other cities (e.g., Los Angeles, Chicago, Houston, Orlando) or other booking platforms (e.g., Booking.com, Expedia, Tripadvisor). If asked about them, state plainly that they are outside Joule Dynamics' current tracking scope.
4. ZERO FABRICATION: Every single price, rate change percentage, property count, and availability status MUST come directly from a returned tool output. If a tool returns no data or an error, state: "I don't have that information in the current real estate scope."
5. NO RAW SQL: Never attempt to write or generate SQL queries. Rely strictly on the registered tool RPCs provided.

OPERATIONAL RULES:
1. NEVER FABRICATE DATA: Rely strictly on returned tool outputs or retrieved methodology chunks. NEVER write ad-hoc SQL. You must exclusively use the registered tools provided.
2. ZERO GUESSING: If data or methodology is missing, state plainly: "I don't have that information in the current real estate scope."
3. SCOPE BOUNDARY: If asked about Leads or Price Monitors, state that you are currently scoped exclusively to the Real Estate Rate Monitor.
4. FORMAT: Always format your final output in valid Markdown. Ensure you use tables, bold headers, and bulleted lists to make data highly readable. NEVER include technical debugging headers or metadata in your response (e.g. do not write "Error Response", "Clarification Needed", "Route:", etc.).
5. CLARIFICATION & ERRORS: If the user's request is ambiguous, a tool is missing parameters (like a market name or UUID), or if a tool returns an error message, DO NOT hallucinate inputs. Stop and provide a seamless, conversational, and human-friendly response explaining the issue and asking for clarification. To provide clickable options to the user, include a specific JSON block at the very end of your response exactly like this:
```json
{"clarification_options": ["Option A", "Option B"]}
```
6. ADVISORY & STRATEGY RESPONSES: When a user asks for pricing recommendations, competitive strategy, or "what should I do?" guidance, you MUST use a strict two-part structure:

   **What the data shows:** (grounded section)
   Present only facts derived directly from tool outputs. Use precise numbers. Format as a table or bullet list.

   **Suggested approach (data-informed):** (reasoned section)
   Offer strategic interpretation based on the data patterns above. Be specific but clearly frame this as inference from data, not a certainty.

   Every advisory response MUST close with this disclaimer on its own line:
   > ⚠️ *This is a data-informed observation, not professional pricing or financial advice. Consult a revenue management specialist for investment decisions.*

   NEVER present a strategic recommendation with the same flat, factual confidence as a queried data point. The boundary between retrieved fact and model reasoning must always be explicit and visible to the user.
"""

# ─── PAYLOAD COMPRESSOR ────────────────────────────────────────────────────────
# MAX rows the LLM receives from any single tool call. Beyond this, data is
# sliced and the LLM is told to recommend narrowing filters.
_MAX_ROWS = 50


def compress_tool_output(func_name: str, db_result: dict) -> str:
    """
    Converts raw tool RPC responses into a token-efficient string for LLM context.
    
    Three-step compression pipeline:
    1. Metadata hoisting — keys with identical values across all rows extracted
       to a single header line, removing them from every row.
    2. Null stripping — any key with a null/None value in a row is omitted.
    3. CSV rendering — remaining data written as CSV (headers once, values compact).
    
    For non-tabular (scalar/dict) results, returns a minimal string representation.
    Estimated reduction: 70–90% vs raw JSON for time-series data.
    """
    if db_result.get("status") != "success":
        msg = db_result.get("message", "unknown error")
        return f"Tool '{func_name}' error: {msg}"

    data = db_result.get("data")

    # ── Scalar / single-object results ──────────────────────────────────────
    if data is None:
        return f"Tool '{func_name}': no data returned."

    if isinstance(data, (str, int, float, bool)):
        return f"Tool '{func_name}' result: {data}"

    if isinstance(data, dict):
        # Already a compact object (e.g. get_rate_anomaly_report, get_market_trend)
        # Just serialise cleanly — these are already small
        return f"Tool '{func_name}' result:\n{json.dumps(data, default=str)}"

    if not isinstance(data, list) or len(data) == 0:
        return f"Tool '{func_name}': empty result."

    # ── List of rows ─────────────────────────────────────────────────────────
    # Filter out rows where everything is None (pure null rows add no signal)
    data = [row for row in data if isinstance(row, dict) and any(v is not None for v in row.values())]

    if not data:
        return f"Tool '{func_name}': all returned rows were empty."

    # Truncate before processing to cap token exposure
    truncated = False
    if len(data) > _MAX_ROWS:
        data = data[:_MAX_ROWS]
        truncated = True

    # ── Step 1: Metadata hoisting ────────────────────────────────────────────
    all_keys = list(data[0].keys())
    hoisted = {}
    row_keys = []
    for key in all_keys:
        unique_values = {str(row.get(key)) for row in data}
        if len(unique_values) == 1:
            val = data[0].get(key)
            if val is not None:
                hoisted[key] = val
        else:
            row_keys.append(key)

    header_parts = [f"{k}={v}" for k, v in hoisted.items()]
    header_str = f"[{', '.join(header_parts)}]\n" if header_parts else ""

    # ── Step 2 & 3: Null-strip + CSV ─────────────────────────────────────────
    # Determine which row-level keys have at least one non-null value across all rows
    active_keys = [k for k in row_keys if any(row.get(k) is not None for row in data)]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(active_keys)
    for row in data:
        writer.writerow([row.get(k) for k in active_keys])

    truncation_notice = (
        f"\n[Truncated to {_MAX_ROWS} rows. Advise user to narrow date range or add filters.]"
        if truncated else ""
    )
    return f"{header_str}{buf.getvalue().strip()}{truncation_notice}"


# ─── GEOCODE HANDLER (Python-side, Mapbox API) ────────────────────────────────

def geocode_address_handler(address: str) -> dict:
    """
    Resolves a free-text address to lat/lng via the Mapbox Geocoding API.
    Handles network failures, API errors, and empty results gracefully.
    US-only results, single best match returned.
    """
    if not MAPBOX_ACCESS_TOKEN:
        return {
            "status": "error",
            "message": "We are unable to geocode addresses at this time — the mapping service is not configured."
        }
    if not address or not address.strip():
        return {"status": "error", "message": "No address was provided to geocode."}

    url = "https://api.mapbox.com/search/geocode/v6/forward"
    params = {
        "q": address.strip(),
        "access_token": MAPBOX_ACCESS_TOKEN,
        "country": "US",
        "limit": 1
    }

    try:
        response = requests.get(url, params=params, timeout=6)
        response.raise_for_status()
        data = response.json()

        features = data.get("features", [])
        if not features:
            return {
                "status": "error",
                "message": f"We are unable to locate coordinates for '{address}' at this time. Please try a more specific address or city name."
            }

        feature = features[0]
        lon, lat = feature["geometry"]["coordinates"]
        resolved = feature.get("properties", {}).get("full_address", address)
        return {
            "status": "success",
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "resolved_address": resolved
        }

    except requests.exceptions.Timeout:
        logger.error(f"Mapbox geocode timeout for address: {address}")
        return {
            "status": "error",
            "message": "We are unable to geocode this address at this time — the mapping service timed out. Please try again shortly."
        }
    except requests.exceptions.HTTPError as e:
        logger.error(f"Mapbox geocode HTTP error {e.response.status_code} for: {address}")
        return {
            "status": "error",
            "message": "We are unable to geocode this address at this time due to a service error. Please try again later."
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Mapbox geocode request failed for '{address}': {e}")
        return {
            "status": "error",
            "message": "We are unable to reach the mapping service at this time. Please try again later."
        }
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Mapbox geocode parsing error for '{address}': {e}")
        return {
            "status": "error",
            "message": f"We are unable to parse the location for '{address}'. Please try a more specific address."
        }


# ─── MAIN CHAT HANDLER ────────────────────────────────────────────────────────

@observe(name="process-chat")
async def process_chat_message(user_query: str, session_id: str, session_context: dict) -> dict:
    global section_title_embeddings, section_titles
    start_time = time.time()
    
    with propagate_attributes(session_id=session_id, tags=["real-estate-chat"]):
        get_client().update_current_span(input=user_query)
        
        if session_id not in session_history:
            session_history[session_id] = []

    # STEP 1: Pre-Router Local Vector Search
    pre_check_hint = ""
    if section_title_embeddings is None and os.path.exists("section_title_embeddings.npy"):
        try:
            section_title_embeddings = np.load("section_title_embeddings.npy")
            with open("section_titles.json", "r", encoding="utf-8") as f:
                section_titles = json.load(f)
        except Exception as e:
            logger.error(f"Lazy load failed: {e}")

    if section_title_embeddings is not None and len(section_titles) > 0:
        query_emb = embedder.encode([user_query], normalize_embeddings=True)[0]
        sims = section_title_embeddings @ query_emb
        top_idx = np.argsort(sims)[::-1][:3]
        matched_titles = [section_titles[i] for i in top_idx if sims[i] >= 0.45]
        
        if matched_titles:
            pre_check_hint = f"\n\nLocal Methodology Pre-Check: High similarity match with section titles: {matched_titles}. Consider classifying as PATH_B or BOTH."
            get_client().update_current_span(metadata={"matched_section_titles": matched_titles, "similarity_scores": [float(sims[i]) for i in top_idx if sims[i] >= 0.45]})

    router_sys_prompt = ROUTER_PROMPT + pre_check_hint
    router_messages = [{"role": "system", "content": router_sys_prompt}]
    
    # Router only needs last 4 history messages — cheap model, keep it lean
    router_messages.extend(session_history[session_id][-4:])
    router_messages.append({"role": "user", "content": user_query})

    router_res = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=router_messages,
        temperature=0.0,
        response_format={"type": "json_object"}
    )
    
    routing = json.loads(router_res.choices[0].message.content)
    
    if isinstance(routing, list) and len(routing) > 0:
        routing = routing[0]
    elif not isinstance(routing, dict):
        routing = {}
        
    classification = routing.get("classification", "PATH_A")
    reason = routing.get("reason")
    if classification not in ["OUT_OF_SCOPE", "PATH_A", "PATH_B", "BOTH", "GREETING"]:
        classification = "PATH_A"

    get_client().update_current_span(metadata={"classification": classification, "reason": reason})

    # Guardrail: Immediate short-circuit if Out of Scope
    if classification == "OUT_OF_SCOPE":
        reply_out = "I apologize, but I am currently scoped exclusively to the Real Estate Rate Monitor page. I cannot assist with other topics like lead generation, pricing automation, or general knowledge outside of real estate data."
        get_client().update_current_span(output=reply_out)
        return {
            "reply": reply_out,
            "path_used": "OUT_OF_SCOPE",
            "tools_called": [],
            "suggested_actions": []
        }

    if classification == "GREETING":
        reply_greeting = "Hello! I'm the Joule Dynamics Real Estate Intelligence Assistant. I can help you with rate spikes, market trends, property investigations, and availability data. How can I assist you today?"
        get_client().update_current_span(output=reply_greeting)
        return {
            "reply": reply_greeting,
            "path_used": "GREETING",
            "tools_called": [],
            "suggested_actions": []
        }

    tool_results = []
    rag_chunks = []

    # STEP 2: Execute Vector Search if Path B or Both
    if classification in ["PATH_B", "BOTH"]:
        rag_chunks = await search_methodology_rag(user_query)

    messages = [
        {"role": "system", "content": SYNTHESIS_PROMPT}
    ]

    # Cap history at 6 messages to avoid token inflation in multi-turn sessions
    messages.extend(session_history[session_id][-6:])

    user_msg_content = f"User Context Filters: {json.dumps(session_context)}\nUser Query: {user_query}"
    messages.append({"role": "user", "content": user_msg_content})
    session_history[session_id].append({"role": "user", "content": user_msg_content})

    if rag_chunks:
        messages.append({
            "role": "system", 
            "content": "Retrieved Methodology Context:\n" + "\n---\n".join(rag_chunks)
        })

    # STEP 3: Initial Brain Completion (llama-3.3-70b-versatile)
    brain_res = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        tools=REAL_ESTATE_TOOLS if classification in ["PATH_A", "BOTH"] else None,
        tool_choice="auto" if classification in ["PATH_A", "BOTH"] else "none",
        temperature=0.2,
        max_tokens=600
    )

    response_message = brain_res.choices[0].message

    # STEP 4: Process Tool Calls if Triggered
    if response_message.tool_calls:
        messages.append(response_message)
        for tool_call in response_message.tool_calls:
            func_name = tool_call.function.name
            func_args = json.loads(tool_call.function.arguments)
            
            # ── Python-side tool handlers (not routed to Supabase) ──────────
            if func_name == "generate_data_export":
                from services.appwrite_service import upload_document_to_appwrite
                url = await upload_document_to_appwrite(func_args.get("content", ""), func_args.get("format", "md"))
                db_result = {"status": "success", "url": url}

            elif func_name == "geocode_address":
                # Mapbox API call handled in Python — never hits Supabase
                db_result = geocode_address_handler(func_args.get("address", ""))

            else:
                db_result = await execute_tool_rpc(func_name, func_args)
                
            tool_results.append({"tool": func_name, "args": func_args})

            # ── Payload compression before entering LLM context ─────────────
            compressed_content = compress_tool_output(func_name, db_result)

            messages.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": func_name,
                "content": compressed_content
            })

        # Second Brain Call to Synthesize Final Output
        final_res = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.2,
            max_tokens=600
        )
        final_reply = final_res.choices[0].message.content
    else:
        final_reply = response_message.content

    # Track assistant reply in history — cap at 6 to prevent token creep
    session_history[session_id].append({"role": "assistant", "content": final_reply})
    if len(session_history[session_id]) > 6:
        session_history[session_id] = session_history[session_id][-6:]

    # Parse clarification options
    suggested_actions = []
    json_match = re.search(r'```json\s*(\{.*"clarification_options".*\})\s*```', final_reply, re.DOTALL)
    if not json_match:
        json_match = re.search(r'(\{.*"clarification_options".*\})', final_reply, re.DOTALL)
        
    if json_match:
        try:
            clarification_data = json.loads(json_match.group(1))
            suggested_actions = clarification_data.get("clarification_options", [])
            final_reply = final_reply.replace(json_match.group(0), "").strip()
        except json.JSONDecodeError:
            pass

    get_client().update_current_span(output=final_reply)

    return {
        "reply": final_reply,
        "path_used": classification,
        "tools_called": tool_results,
        "suggested_actions": suggested_actions
    }
