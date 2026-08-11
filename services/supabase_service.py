import os
import re
from supabase import create_client, Client
from services.embedding_service import get_embedding_model
from config import SUPABASE_URL, SUPABASE_KEY

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
embedder = get_embedding_model()

# Tools handled entirely in Python — never routed to Supabase
_PYTHON_SIDE_TOOLS = {"generate_data_export", "geocode_address"}


def is_valid_uuid(val):
    if not isinstance(val, str):
        return False
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', val.lower()))


def _clamp(value, lo, hi, default=None):
    """Clamps an integer param. Returns default if value is None."""
    if value is None:
        return default
    return max(lo, min(int(value), hi))


async def execute_tool_rpc(func_name: str, args: dict) -> dict:
    """Executes a read-only Supabase RPC matching the tool schema.
    
    All parameter clamping and validation is applied here so the LLM
    cannot craft out-of-bound inputs that would return huge datasets.
    """
    if func_name in _PYTHON_SIDE_TOOLS:
        # Safety guard — these should be intercepted before reaching here
        return {"status": "error", "message": f"Tool '{func_name}' is handled client-side and should not route to Supabase."}

    # ── Per-function validation & clamping ────────────────────────────────────

    if func_name == "search_properties":
        args["p_limit"] = _clamp(args.get("p_limit"), 1, 50, default=20)

    elif func_name == "get_property_rate_changes":
        args["days_param"] = _clamp(args.get("days_param"), 1, 30, default=14)
        args["compare_window_days"] = _clamp(args.get("compare_window_days"), 1, 14, default=1)
        # Validate optional date range params
        start = args.get("start_date")
        end = args.get("end_date")
        if (start and not end) or (end and not start):
            return {"status": "error", "message": "Both start_date and end_date must be provided together."}
        # If dates provided, remove days_param to let SQL use date range mode
        if start and end:
            args.pop("days_param", None)

    elif func_name == "get_property_rate_history":
        args["days_param"] = _clamp(args.get("days_param"), 1, 90, default=14)

    elif func_name == "get_spike_alerts":
        args["days"] = _clamp(args.get("days"), 1, 30, default=7)
        if "threshold" in args and args["threshold"] is not None:
            args["threshold"] = max(5.0, min(float(args["threshold"]), 100.0))

    elif func_name == "get_distance_km":
        if not is_valid_uuid(args.get("property_a_id")) or not is_valid_uuid(args.get("property_b_id")):
            return {"status": "error", "message": "Invalid UUID format. Both property IDs must be valid UUIDs."}

    elif func_name == "get_rate_anomaly_report":
        args["days_param"] = _clamp(args.get("days_param"), 1, 90, default=30)
        if "deviation_threshold" in args and args["deviation_threshold"] is not None:
            args["deviation_threshold"] = max(5.0, min(float(args["deviation_threshold"]), 100.0))

    elif func_name == "get_market_snapshot":
        start = args.get("start_date")
        end = args.get("end_date")
        if not start or not end:
            return {"status": "error", "message": "get_market_snapshot requires both start_date and end_date."}
        # Enforce max 90-day span to prevent massive aggregations
        try:
            from datetime import date
            d1 = date.fromisoformat(start)
            d2 = date.fromisoformat(end)
            if d2 < d1:
                return {"status": "error", "message": "end_date must be after start_date."}
            if (d2 - d1).days > 90:
                return {"status": "error", "message": "Date range cannot exceed 90 days for get_market_snapshot."}
        except ValueError:
            return {"status": "error", "message": "Invalid date format. Use YYYY-MM-DD."}

    elif func_name == "get_market_trend":
        args["days"] = _clamp(args.get("days"), 7, 90, default=14)

    elif func_name == "compare_properties":
        ids = args.get("property_ids", [])
        if not isinstance(ids, list) or len(ids) < 2:
            return {"status": "error", "message": "compare_properties requires a list of 2–5 property names or UUIDs."}
        args["property_ids"] = ids[:5]  # Hard cap at 5

    elif func_name == "get_most_volatile_properties":
        args["days"] = _clamp(args.get("days"), 7, 90, default=14)
        args["limit"] = _clamp(args.get("limit"), 1, 10, default=5)

    elif func_name == "get_recently_changed_tracking":
        args["days"] = _clamp(args.get("days"), 1, 90, default=30)

    elif func_name == "get_nearby_properties":
        if args.get("latitude") is None or args.get("longitude") is None:
            return {"status": "error", "message": "get_nearby_properties requires latitude and longitude coordinates. Call geocode_address first if you only have an address."}
        args["radius_km"] = max(0.1, min(float(args.get("radius_km", 5.0)), 20.0))
        args["limit"] = _clamp(args.get("limit"), 1, 20, default=10)

    # ── Execute RPC ───────────────────────────────────────────────────────────
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
