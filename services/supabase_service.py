import os
import re
from supabase import create_client, Client
from services.embedding_service import get_embedding_model
from config import SUPABASE_URL, SUPABASE_KEY

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
embedder = get_embedding_model()

def is_valid_uuid(val):
    if not isinstance(val, str):
        return False
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', val.lower()))

async def execute_tool_rpc(func_name: str, args: dict) -> dict:
    """Executes a read-only Supabase RPC matching the tool schema."""
    # Application layer clamping and validation
    if func_name == "search_properties":
        if "p_limit" in args and args["p_limit"] is not None:
            args["p_limit"] = max(1, min(int(args["p_limit"]), 200))
    elif func_name == "get_property_rate_changes":
        if "days_param" in args and args["days_param"] is not None:
            args["days_param"] = max(1, min(int(args["days_param"]), 90))
        if "compare_window_days" in args and args["compare_window_days"] is not None:
            args["compare_window_days"] = max(1, min(int(args["compare_window_days"]), 14))
    elif func_name == "get_distance_km":
        if not is_valid_uuid(args.get("property_a_id")) or not is_valid_uuid(args.get("property_b_id")):
            return {"status": "error", "message": "Invalid UUID format. Both property IDs must be valid UUIDs."}

    try:
        res = supabase.rpc(func_name, args).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def search_methodology_rag(query: str, top_k: int = 3) -> list:
    """Embeds query and retrieves top matching methodology documentation chunks."""
    try:
        vector = embedder.encode(query).tolist()
        res = supabase.rpc(
            "match_re_methodology",
            {
                "query_embedding": vector,
                "match_threshold": 0.45,
                "match_count": top_k
            }
        ).execute()
        
        return [f"### {item['section_title']}\n{item['chunk_content']}" for item in res.data]
    except Exception as e:
        print(f"RAG Retrieval Error: {e}")
        return []
