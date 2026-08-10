import json
import re
import os
import time
import numpy as np
from groq import Groq
from services.tools import REAL_ESTATE_TOOLS
from services.supabase_service import execute_tool_rpc, search_methodology_rag
from services.embedding_service import get_embedding_model
from services.observability import setup_logger
from config import GROQ_API_KEY
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
4. ZERO FABRICATION: Every single price, rate change percentage, property count, and availability status MUST come directly from a returned tool output JSON. If a tool returns no data or an error, state: "I don't have that information in the current real estate scope."
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
"""

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
    
    # To save tokens on routing, only include the last 4 messages of history
    router_messages.extend(session_history[session_id][-4:])
    router_messages.append({"role": "user", "content": user_query})

    router_res = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=router_messages,
        temperature=0.0,
        response_format={"type": "json_object"}
    )
    
    routing = json.loads(router_res.choices[0].message.content)
    
    # Handle cases where the LLM might unexpectedly return a JSON array instead of an object
    if isinstance(routing, list) and len(routing) > 0:
        routing = routing[0]
    elif not isinstance(routing, dict):
        routing = {}
        
    classification = routing.get("classification", "PATH_A")
    reason = routing.get("reason")
    if classification not in ["OUT_OF_SCOPE", "PATH_A", "PATH_B", "BOTH", "GREETING"]:
        classification = "PATH_A"

    get_client().update_current_span(metadata={"classification": classification, "reason": reason})
    snapshot_history = session_history[session_id].copy() if session_id in session_history else []

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
        reply_greeting = "Hello! I'm the Joule Dynamics Real Estate Intelligence Assistant. I can help you with rate spikes, market trends, and availability data. How can I assist you today?"
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

    messages.extend(session_history[session_id])

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
            
            if func_name == "generate_data_export":
                from services.appwrite_service import upload_document_to_appwrite
                url = await upload_document_to_appwrite(func_args.get("content", ""), func_args.get("format", "md"))
                db_result = {"status": "success", "url": url}
            else:
                db_result = await execute_tool_rpc(func_name, func_args)
                
            tool_results.append({"tool": func_name, "args": func_args})

            messages.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": func_name,
                "content": json.dumps(db_result)
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

    # Track assistant reply in history
    session_history[session_id].append({"role": "assistant", "content": final_reply})
    if len(session_history[session_id]) > 20:
        session_history[session_id] = session_history[session_id][-20:]

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
