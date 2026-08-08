import json
import re
from groq import Groq
from services.tools import REAL_ESTATE_TOOLS
from services.supabase_service import execute_tool_rpc, search_methodology_rag
from config import GROQ_API_KEY

session_history = {}

groq_client = Groq(api_key=GROQ_API_KEY)

ROUTER_PROMPT = """You are the classification router for the Joule Dynamics Real Estate Intelligence Layer.
Analyze the user query and classify it into EXACTLY ONE of six classifications:

1. "OUT_OF_SCOPE": Query asks about Leads, Lead-capture, Pricing Monitor data, general web crawling, or cross-system topics outside of the /real-estate page.
2. "PATH_A": Query asks a live-data question (prices, spikes, availability, market averages, KPIs, specific listing rates).
3. "PATH_B": Query asks a methodology/system design question (7-day average definition, 2-night check-in window, 4x daily scrape cadence, Vrbo status, World Cup strategy).
4. "PATH_C": Query asks a general real estate market context question that does not require live data metrics from our system.
5. "BOTH": Query requires BOTH explaining a methodology concept AND fetching live data metrics.
6. "GREETING": User is saying hello, thanking the assistant, or making casual conversation without asking a specific question.

Respond ONLY with valid JSON matching this schema:
{"classification": "OUT_OF_SCOPE" | "PATH_A" | "PATH_B" | "PATH_C" | "BOTH" | "GREETING", "reason": "1-sentence justification"}
"""

SYNTHESIS_PROMPT = """You are the B2B Real Estate Intelligence Assistant for Joule Dynamics.
You provide precise data analysis to real estate investors and property managers reviewing short-term rental market performance.

OPERATIONAL RULES:
1. NEVER FABRICATE DATA: Rely strictly on returned tool outputs or retrieved methodology chunks. NEVER write ad-hoc SQL. You must exclusively use the registered tools provided.
2. ZERO GUESSING: If data or methodology is missing, state plainly: "I don't have that information in the current real estate scope."
3. SCOPE BOUNDARY: If asked about Leads or Price Monitors, state that you are currently scoped exclusively to the Real Estate Rate Monitor.
4. FORMAT: Always format your final output in valid Markdown. Ensure you use tables, bold headers, and bulleted lists to make data highly readable.
5. CLARIFICATION & ERRORS: If the user's request is ambiguous, a tool is missing parameters (like a market name or UUID), or if a tool returns an error message, DO NOT hallucinate inputs. Stop and provide a human-friendly response asking for clarification. To provide clickable options to the user, include a specific JSON block at the very end of your response exactly like this:
```json
{"clarification_options": ["Option A", "Option B"]}
```
"""

async def process_chat_message(user_query: str, session_id: str, session_context: dict) -> dict:
    if session_id not in session_history:
        session_history[session_id] = []

    # STEP 1: Routing Classification (llama-3.1-8b-instant)
    router_messages = [{"role": "system", "content": ROUTER_PROMPT}]
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
    if classification not in ["OUT_OF_SCOPE", "PATH_A", "PATH_B", "PATH_C", "BOTH", "GREETING"]:
        classification = "PATH_A"

    # Guardrail: Immediate short-circuit if Out of Scope
    if classification == "OUT_OF_SCOPE":
        return {
            "reply": "I apologize, but I am currently scoped exclusively to the Real Estate Rate Monitor page. I cannot assist with other topics like lead generation, pricing automation, or general knowledge outside of real estate data.",
            "path_used": "OUT_OF_SCOPE",
            "tools_called": [],
            "suggested_actions": []
        }

    if classification == "GREETING":
        return {
            "reply": "Hello! I'm the Joule Dynamics Real Estate Intelligence Assistant. I can help you with rate spikes, market trends, and availability data. How can I assist you today?",
            "path_used": "GREETING",
            "tools_called": [],
            "suggested_actions": []
        }

    tool_results = []
    rag_chunks = []

    # STEP 2: Execute Vector Search if Path B or Both
    if classification in ["PATH_B", "BOTH"]:
        rag_chunks = await search_methodology_rag(user_query)

    # STEP 2: Execute Vector Search if Path B or Both
    if classification in ["PATH_B", "BOTH"]:
        rag_chunks = await search_methodology_rag(user_query)

    messages = [
        {"role": "system", "content": SYNTHESIS_PROMPT}
    ]
    if classification == "PATH_C":
        messages.append({
            "role": "system",
            "content": "Note: Because this is a PATH_C query, you must prepend a strict disclaimer stating: 'Note: This is general market context, not live data from the Joule Dynamics tracking system.'"
        })

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

    return {
        "reply": final_reply,
        "path_used": classification,
        "tools_called": tool_results,
        "suggested_actions": suggested_actions
    }
