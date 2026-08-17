import csv
import io
import json
import re
import os
import time
import requests
import numpy as np
from groq import Groq
from services.tools import REAL_ESTATE_TOOLS, COMMERCIAL_TOOLS
from services.supabase_service import execute_tool_rpc, search_methodology_rag
from services.embedding_service import get_embedding_model
from services.observability import setup_logger
from config import (
    GROQ_API_KEY,
    MAPBOX_ACCESS_TOKEN,
    CONTACT_EMAIL,
    CONTACT_WHATSAPP,
    GROQ_ROUTE_MODEL,
    GROQ_SYNTHESIS_MODEL,
)
from langfuse import observe, propagate_attributes, get_client
import langfuse

logger = setup_logger(__name__)

session_history = {}

groq_client = Groq(api_key=GROQ_API_KEY)
embedder = get_embedding_model()

try:
    if os.path.exists("section_title_embeddings.npy") and os.path.exists(
        "section_titles.json"
    ):
        section_title_embeddings = np.load("section_title_embeddings.npy")
        with open("section_titles.json", "r", encoding="utf-8") as f:
            section_titles = json.load(f)
        logger.info(
            f"Loaded {len(section_titles)} local section titles for pre-routing."
        )
    else:
        section_title_embeddings = None
        section_titles = []
except Exception as e:
    logger.error(f"Failed to load local section title embeddings: {e}")
    section_title_embeddings = None
    section_titles = []

ROUTER_PROMPT = """You are the classification router for the Joule Dynamics Real Estate Intelligence Layer.
Analyze the user query and classify it into EXACTLY ONE of six classifications:

1. "OUT_OF_SCOPE": Query asks about topics completely unrelated to real estate, data monitoring, or the current conversation (e.g. sports, general coding, cooking, recipes). IMPORTANT: Questions asking who you are ("who are you?"), what model you are, or asking to summarize/recap what was discussed in the current chat ("what have we discussed so far?", "summarize our chat") are ALWAYS IN-SCOPE and must NEVER be classified as OUT_OF_SCOPE.
2. "PATH_A": Query asks a live-data question (prices, spikes, availability, market averages, KPIs, specific listing rates).
3. "PATH_B": Query asks a methodology/system design question (7-day average definition, 2-night check-in window, 4x daily scrape cadence), OR asks a meta-conversational question (e.g. "What have we discussed so far?", "Can you summarize our conversation?", "Recap what we talked about").
4. "BOTH": Query requires BOTH explaining a methodology concept AND fetching live data metrics.
5. "GREETING": User is saying hello, thanking the assistant, asking "who are you?", or making casual conversation.
6. "COMMERCIAL_HANDOFF": Query asks about getting started, hiring Joule Dynamics, custom builds, custom dashboards, pricing for software, or requests tracking for their own specific portfolio outside the demo scope.

Respond ONLY with valid JSON matching this schema:
{"classification": "OUT_OF_SCOPE" | "PATH_A" | "PATH_B" | "BOTH" | "GREETING" | "COMMERCIAL_HANDOFF", "reason": "1-sentence justification"}
"""

SYNTHESIS_PROMPT = """You are Pulse AI, a Real Estate Intelligence Assistant for Joule Dynamics.
You provide precise data analysis to real estate investors and property managers reviewing short-term rental market performance. If asked about your identity or underlying model, identify yourself as Pulse AI, developed for Joule Dynamics.

IMMUTABLE SYSTEM BOUNDARIES & HARD FACTS:
1. SANDBOX SCOPE: The data you have access to is a proof-of-concept showcase tracking a curated sample of properties. It is NOT a complete city-wide market census. You must clarify this if a user attempts to use this data for macro-market investment decisions.
2. TRACKED MARKETS: You ONLY track two markets: 'NYC/NJ Metro' and 'Miami'. 
3. TRACKED PLATFORMS: You ONLY track 'Airbnb' (Active daily tracking) and 'Vrbo' (Historical data only).
4. ABSOLUTE FORBIDDEN ENTITIES: You must NEVER list, suggest, or mention any other cities or other booking platforms.
5. ZERO FABRICATION: Every single price, rate change percentage, property count, and availability status MUST come directly from a returned tool output. 

OPERATIONAL RULES:
1. CONVERSATIONAL ELEGANCE (NO RAW TOOLS): NEVER output raw technical tool names (e.g., `get_market_averages`, `geocode_address`). NEVER dump long, robotic lists of your capabilities. If asked what you can do, reply conversationally and naturally (e.g., "I can track live rate spikes, calculate trailing averages, and analyze pricing volatility.").
2. NO RAW SQL: Never attempt to write or generate SQL queries. Rely strictly on the registered tool RPCs provided.
3. ZERO GUESSING: If data or methodology is missing, state plainly: "I don't have that information in the current real estate scope."
4. FORMAT: Always format your final output in valid Markdown. Use tables, bold headers, and bulleted lists. NEVER include technical debugging headers or metadata in your response.
5. CLARIFICATION & ERRORS: If a tool is missing parameters or returns an error, DO NOT hallucinate inputs. Provide a human-friendly response asking for clarification. To provide clickable options, include this exact JSON block at the very end of your response:
```json
{"clarification_options": ["Option A", "Option B"]}
ADVISORY & STRATEGY RESPONSES: When a user asks for pricing recommendations, use a strict two-part structure:
What the data shows: Present only facts derived directly from tool outputs.
Suggested approach (data-informed): Offer strategic interpretation based on the data.
Every advisory response MUST close with this disclaimer on its own line:

⚠️ This is a data-informed observation, not professional pricing or financial advice.

EXPORTS & DOWNLOADS: When a user requests an export or download, invoke `generate_data_export`. In your final response, provide the download URL as a clean markdown link (e.g. `[Download CSV Report](<download_url>)`). Do NOT dump the full raw CSV/Markdown text into the chat message.

CONVERSATION RECAPS & SUMMARIES: When a user asks to summarize what was discussed, recap findings, or review earlier turns (e.g. "What have we discussed so far?"), review the conversation history in context and provide a structured, bullet-point summary of all properties, metrics, markets, and questions discussed in the session.

COMMERCIAL HANDOFFS (JOULE DYNAMICS BESPOKE BUILDS): 
When a user asks about custom builds, deploying this system for their business, getting custom dashboards, pricing, hiring us, or tracking their own portfolio outside this sandbox:
1. Clarify that this rate monitor is a live sandbox demonstration built by Joule Dynamics. Pulse AI is just one specialized component of Joule Dynamics' broader data architecture and engineering capabilities.
2. Frame the solution around Joule Dynamics designing and building private, bespoke data architectures, dedicated scrapers, custom dashboards, and automated pipelines tailored to the client's specific problem (e.g. their portfolio size, target markets, or custom metrics).
3. Focus your answer directly on solving the client's specific problem. Do NOT refer to Joule Dynamics or the custom build as "Pulse AI".
4. Invoke the generate_contact_buttons tool with a personalized greeting message for WhatsApp referencing Joule Dynamics and the client's specific inquiry (e.g. "Hi John, I would like to discuss a custom build by Joule Dynamics for my 50 units in Brickell.").
5. Include the exact markdown buttons returned by the tool at the end of your response to render the action buttons correctly.
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

    if "data" in db_result:
        data = db_result.get("data")
    else:
        data = {k: v for k, v in db_result.items() if k != "status"}

    # ── Scalar / single-object results ──────────────────────────────────────
    if data is None or (isinstance(data, dict) and len(data) == 0):
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
    data = [
        row
        for row in data
        if isinstance(row, dict) and any(v is not None for v in row.values())
    ]

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
        if truncated
        else ""
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
            "message": "We are unable to geocode addresses at this time — the mapping service is not configured.",
        }
    if not address or not address.strip():
        return {"status": "error", "message": "No address was provided to geocode."}

    url = "https://api.mapbox.com/search/geocode/v6/forward"
    params = {
        "q": address.strip(),
        "access_token": MAPBOX_ACCESS_TOKEN,
        "country": "US",
        "limit": 1,
    }

    try:
        response = requests.get(url, params=params, timeout=6)
        response.raise_for_status()
        data = response.json()

        features = data.get("features", [])
        if not features:
            return {
                "status": "error",
                "message": f"We are unable to locate coordinates for '{address}' at this time. Please try a more specific address or city name.",
            }

        feature = features[0]
        lon, lat = feature["geometry"]["coordinates"]
        resolved = feature.get("properties", {}).get("full_address", address)
        return {
            "status": "success",
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "resolved_address": resolved,
        }

    except requests.exceptions.Timeout:
        logger.error(f"Mapbox geocode timeout for address: {address}")
        return {
            "status": "error",
            "message": "We are unable to geocode this address at this time — the mapping service timed out. Please try again shortly.",
        }
    except requests.exceptions.HTTPError as e:
        logger.error(
            f"Mapbox geocode HTTP error {e.response.status_code} for: {address}"
        )
        return {
            "status": "error",
            "message": "We are unable to geocode this address at this time due to a service error. Please try again later.",
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Mapbox geocode request failed for '{address}': {e}")
        return {
            "status": "error",
            "message": "We are unable to reach the mapping service at this time. Please try again later.",
        }
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Mapbox geocode parsing error for '{address}': {e}")
        return {
            "status": "error",
            "message": f"We are unable to parse the location for '{address}'. Please try a more specific address.",
        }


# ─── MAIN CHAT HANDLER ────────────────────────────────────────────────────────


@observe(name="process-chat")
async def process_chat_message(
    user_query: str, session_id: str, session_context: dict
) -> dict:
    global section_title_embeddings, section_titles
    start_time = time.time()

    with propagate_attributes(session_id=session_id, tags=["real-estate-chat"]):
        get_client().update_current_span(input=user_query)

        if session_id not in session_history:
            session_history[session_id] = []

    # STEP 1: Pre-Router Local Vector Search
    pre_check_hint = ""
    if section_title_embeddings is None and os.path.exists(
        "section_title_embeddings.npy"
    ):
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
            get_client().update_current_span(
                metadata={
                    "matched_section_titles": matched_titles,
                    "similarity_scores": [
                        float(sims[i]) for i in top_idx if sims[i] >= 0.45
                    ],
                }
            )

    router_sys_prompt = ROUTER_PROMPT + pre_check_hint
    router_messages = [{"role": "system", "content": router_sys_prompt}]

    # Router only needs last 4 history messages — cheap model, keep it lean
    router_messages.extend(session_history[session_id][-4:])
    router_messages.append({"role": "user", "content": user_query})

    try:
        router_res = groq_client.chat.completions.create(
            model=GROQ_ROUTE_MODEL,
            messages=router_messages,
            temperature=0.0,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        routing = json.loads(router_res.choices[0].message.content)
    except Exception as router_err:
        logger.warning(f"Router model {GROQ_ROUTE_MODEL} failed: {router_err}. Trying fallback {GROQ_FALLBACK_ROUTE_MODEL}...")
        try:
            router_res = groq_client.chat.completions.create(
                model=GROQ_FALLBACK_ROUTE_MODEL,
                messages=router_messages,
                temperature=0.0,
                max_tokens=600,
            )
            raw_content = router_res.choices[0].message.content
            cleaned_content = re.sub(r'<think>.*?</think>', '', raw_content, flags=re.DOTALL).strip()
            # If JSON parseable, load it; otherwise extract tag directly
            try:
                routing = json.loads(cleaned_content)
            except Exception:
                for tag in ["OUT_OF_SCOPE", "PATH_A", "PATH_B", "BOTH", "GREETING", "COMMERCIAL_HANDOFF"]:
                    if tag in cleaned_content:
                        routing = {"classification": tag, "reason": "Fallback extracted"}
                        break
                else:
                    routing = {"classification": "PATH_A"}
        except Exception as fb_err:
            logger.error(f"Fallback router {GROQ_FALLBACK_ROUTE_MODEL} also failed: {fb_err}. Defaulting to PATH_A.")
            routing = {"classification": "PATH_A"}

    if isinstance(routing, list) and len(routing) > 0:
        routing = routing[0]
    elif not isinstance(routing, dict):
        routing = {}

    classification = routing.get("classification", "PATH_A")
    reason = routing.get("reason")
    if classification not in [
        "OUT_OF_SCOPE",
        "PATH_A",
        "PATH_B",
        "BOTH",
        "GREETING",
        "COMMERCIAL_HANDOFF",
    ]:
        classification = "PATH_A"

    get_client().update_current_span(
        metadata={"classification": classification, "reason": reason}
    )

    # Guardrail: Immediate short-circuit if Out of Scope
    if classification == "OUT_OF_SCOPE":
        reply_out = "I apologize, but I am currently scoped exclusively to the Real Estate Rate Monitor page. I cannot assist with other topics like lead generation, pricing automation, or general knowledge outside of real estate data."
        get_client().update_current_span(output=reply_out)
        return {
            "reply": reply_out,
            "path_used": "OUT_OF_SCOPE",
            "tools_called": [],
            "suggested_actions": [],
        }

    if classification == "GREETING":
        reply_greeting = "Hello! I'm Pulse AI, a Real Estate Intelligence Assistant for Joule Dynamics. I can help you analyze rate spikes, market trends, pricing volatility, and availability data across our tracked markets. How can I assist you today?"
        get_client().update_current_span(output=reply_greeting)
        return {
            "reply": reply_greeting,
            "path_used": "GREETING",
            "tools_called": [],
            "suggested_actions": [],
        }

    tool_results = []
    rag_chunks = []

    # STEP 2: Execute Vector Search if Path B, Both, or Commercial Handoff
    if classification in ["PATH_B", "BOTH", "COMMERCIAL_HANDOFF"]:
        rag_chunks = await search_methodology_rag(user_query)

    messages = [{"role": "system", "content": SYNTHESIS_PROMPT}]

    # Cap history at 6 messages to avoid token inflation in multi-turn sessions
    messages.extend(session_history[session_id][-6:])

    user_msg_content = (
        f"User Context Filters: {json.dumps(session_context)}\nUser Query: {user_query}"
    )
    messages.append({"role": "user", "content": user_msg_content})
    session_history[session_id].append({"role": "user", "content": user_msg_content})

    if rag_chunks:
        messages.append(
            {
                "role": "system",
                "content": "Retrieved Methodology Context:\n"
                + "\n---\n".join(rag_chunks),
            }
        )

    # Select active tools based on classification
    active_tools = None
    if classification in ["PATH_A", "BOTH"]:
        active_tools = REAL_ESTATE_TOOLS
    elif classification == "COMMERCIAL_HANDOFF":
        active_tools = COMMERCIAL_TOOLS

    # STEP 3: Initial Brain Completion (llama-3.3-70b-versatile)
    brain_res = groq_client.chat.completions.create(
        model=GROQ_SYNTHESIS_MODEL,
        messages=messages,
        tools=active_tools,
        tool_choice="auto" if active_tools else "none",
        temperature=0.2,
        max_tokens=600,
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

                db_result = await upload_document_to_appwrite(
                    func_args.get("content", ""), func_args.get("format", "md")
                )

            elif func_name == "geocode_address":
                # Mapbox API call handled in Python — never hits Supabase
                db_result = geocode_address_handler(func_args.get("address", ""))

            elif func_name == "generate_contact_buttons":
                import urllib.parse

                raw_message = func_args.get(
                    "message", "Hi, I'd like to discuss a custom build."
                )
                encoded_message = urllib.parse.quote(raw_message)

                db_result = {
                    "status": "success",
                    "email_button_markdown": f'[Get in touch via Email](<mailto:{CONTACT_EMAIL} "button">)',
                    "whatsapp_button_markdown": f'[Chat on WhatsApp](<https://wa.me/{CONTACT_WHATSAPP}?text={encoded_message} "button">)',
                }

            else:
                db_result = await execute_tool_rpc(func_name, func_args)

            tool_results.append({"tool": func_name, "args": func_args})

            # ── Payload compression before entering LLM context ─────────────
            compressed_content = compress_tool_output(func_name, db_result)

            messages.append(
                {
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": func_name,
                    "content": compressed_content,
                }
            )

        # Second Brain Call to Synthesize Final Output
        final_res = groq_client.chat.completions.create(
            model=GROQ_SYNTHESIS_MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=600,
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
    json_match = re.search(
        r'```json\s*(\{.*"clarification_options".*\})\s*```', final_reply, re.DOTALL
    )
    if not json_match:
        json_match = re.search(
            r'(\{.*"clarification_options".*\})', final_reply, re.DOTALL
        )

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
        "suggested_actions": suggested_actions,
    }
