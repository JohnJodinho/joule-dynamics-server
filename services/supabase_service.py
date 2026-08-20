import os
import re
from datetime import date as dt_date
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
    Parameter names used here MUST match the actual Supabase RPC signatures
    in all_rpcs.sql exactly.
    """
    if func_name in _PYTHON_SIDE_TOOLS:
        return {
            "status": "error",
            "message": f"Tool '{func_name}' is handled client-side and must not route to Supabase."
        }

    # ── Per-function validation & clamping ────────────────────────────────────

    # Parameter normalization across aliases
    if func_name == "search_properties":
        # RPC: search_properties(p_search, p_market, p_platform, p_bedrooms, p_available, p_limit)
        args["p_limit"] = _clamp(args.get("p_limit"), 1, 50, default=20)

    elif func_name == "get_market_averages":
        # RPC: get_market_averages(market_param)
        # Normalize market/p_market alias to market_param
        if "p_market" in args:
            args["market_param"] = args.pop("p_market")
        elif "market" in args:
            args["market_param"] = args.pop("market")

    elif func_name == "get_market_trend":
        # RPC: get_market_trend(p_market, p_days)
        args["p_days"] = _clamp(args.get("p_days"), 7, 90, default=14)

    elif func_name == "get_market_snapshot":
        # RPC: get_market_snapshot(p_market, p_start_date, p_end_date)
        start = args.get("p_start_date")
        end = args.get("p_end_date")
        if not start or not end:
            return {"status": "error", "message": "get_market_snapshot requires both p_start_date and p_end_date."}
        try:
            d1 = dt_date.fromisoformat(start)
            d2 = dt_date.fromisoformat(end)
            if d2 < d1:
                return {"status": "error", "message": "p_end_date must be after p_start_date."}
            if (d2 - d1).days > 90:
                return {"status": "error", "message": "Date range cannot exceed 90 days for get_market_snapshot."}
        except ValueError:
            return {"status": "error", "message": "Invalid date format. Use YYYY-MM-DD for p_start_date and p_end_date."}

    elif func_name == "get_property_rate_changes":
        # RPC: get_property_rate_changes(property_search, days_param, compare_window_days, start_date, end_date)
        start = args.get("start_date")
        end = args.get("end_date")
        if (start and not end) or (end and not start):
            return {"status": "error", "message": "Both start_date and end_date must be provided together."}
        if start and end:
            # Date range mode — days_param is not used by the RPC in this mode
            args.pop("days_param", None)
        else:
            args["days_param"] = _clamp(args.get("days_param"), 1, 30, default=14)
        args["compare_window_days"] = _clamp(args.get("compare_window_days"), 1, 14, default=1)

    elif func_name == "get_property_rate_history":
        # Legacy RPC — keep for compat
        args["days_param"] = _clamp(args.get("days_param"), 1, 90, default=14)

    elif func_name == "get_spike_alerts":
        # RPC: get_spike_alerts(threshold_param, days_param)
        args["days_param"] = _clamp(args.get("days_param"), 1, 30, default=7)
        if args.get("threshold_param") is not None:
            args["threshold_param"] = max(5.0, min(float(args["threshold_param"]), 100.0))

    elif func_name == "get_rate_anomaly_report":
        # RPC: get_rate_anomaly_report(p_property_search, p_days, p_deviation_threshold)
        args["p_days"] = _clamp(args.get("p_days"), 1, 90, default=30)
        if args.get("p_deviation_threshold") is not None:
            args["p_deviation_threshold"] = max(5.0, min(float(args["p_deviation_threshold"]), 100.0))

    elif func_name == "get_property_snapshot":
        # RPC: get_property_snapshot(p_property_search)
        if "property_search" in args and "p_property_search" not in args:
            args["p_property_search"] = args.pop("property_search")

    elif func_name == "get_distance_km":
        # RPC: get_distance_km(property_a_id, property_b_id)
        if not is_valid_uuid(args.get("property_a_id")) or not is_valid_uuid(args.get("property_b_id")):
            return {"status": "error", "message": "Invalid UUID format. Both property IDs must be valid UUIDs."}

    elif func_name == "compare_properties":
        # RPC: compare_properties(p_property_ids text[])
        ids = args.get("p_property_ids") or args.get("property_ids", [])
        if not isinstance(ids, list) or len(ids) < 2:
            return {"status": "error", "message": "compare_properties requires a list of 2–5 property names or UUIDs."}
        args["p_property_ids"] = ids[:5]
        args.pop("property_ids", None)

    elif func_name == "get_most_volatile_properties":
        # RPC: get_most_volatile_properties(p_market, p_days, p_limit)
        if "market" in args and "p_market" not in args:
            args["p_market"] = args.pop("market")
        if "days" in args and "p_days" not in args:
            args["p_days"] = args.pop("days")
        if "limit" in args and "p_limit" not in args:
            args["p_limit"] = args.pop("limit")
        args["p_days"] = _clamp(args.get("p_days"), 7, 90, default=14)
        args["p_limit"] = _clamp(args.get("p_limit"), 1, 10, default=5)

    elif func_name == "get_availability_rate":
        # RPC: get_availability_rate(p_market, p_platform)
        pass

    elif func_name == "get_recently_changed_tracking":
        # RPC: get_recently_changed_tracking(p_days)
        args["p_days"] = _clamp(args.get("p_days"), 1, 90, default=30)

    elif func_name == "get_nearby_properties":
        # RPC: get_nearby_properties(p_latitude, p_longitude, p_radius_km, p_limit)
        if "latitude" in args and "p_latitude" not in args:
            args["p_latitude"] = args.pop("latitude")
        if "longitude" in args and "p_longitude" not in args:
            args["p_longitude"] = args.pop("longitude")
        if args.get("p_latitude") is None or args.get("p_longitude") is None:
            return {
                "status": "error",
                "message": "get_nearby_properties requires p_latitude and p_longitude. Call geocode_address first if you only have an address."
            }
        args["p_radius_km"] = max(0.1, min(float(args.get("p_radius_km", 5.0)), 20.0))
        args["p_limit"] = _clamp(args.get("p_limit"), 1, 20, default=10)

    elif func_name == "get_tracked_markets":
        # RPC: get_tracked_markets(p_platform)
        pass

    elif func_name == "get_dashboard_kpis":
        # RPC: get_dashboard_kpis(p_market, p_platform, p_bedrooms, p_is_active, p_property_ids, p_start_date, p_end_date)
        p_market = args.get("p_market") or args.get("market")
        p_platform = args.get("p_platform") or args.get("platform")
        p_bedrooms = args.get("p_bedrooms") or args.get("bedrooms")
        p_is_active = args.get("p_is_active") if "p_is_active" in args else args.get("is_active")
        p_property_ids = args.get("p_property_ids") or args.get("property_ids")
        p_start_date = args.get("p_start_date") or args.get("start_date")
        p_end_date = args.get("p_end_date") or args.get("end_date")

        args = {
            "p_market": p_market,
            "p_platform": p_platform,
            "p_bedrooms": p_bedrooms,
            "p_is_active": p_is_active,
            "p_property_ids": p_property_ids,
            "p_start_date": p_start_date,
            "p_end_date": p_end_date,
        }

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
                "match_threshold": 0.20,
                "match_count": top_k
            }
        ).execute()
        return [f"### {item['section_title']}\n{item['chunk_content']}" for item in res.data]
    except Exception as e:
        print(f"RAG Retrieval Error: {e}")
        return []
