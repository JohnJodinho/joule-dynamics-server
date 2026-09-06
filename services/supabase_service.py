import os
import re
from datetime import date as dt_date
from supabase import create_client, Client
from services.embedding_service import get_embedding_model
from config import SUPABASE_URL, SUPABASE_KEY

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
embedder = get_embedding_model()

# Tools handled entirely in Python — never routed to Supabase
_PYTHON_SIDE_TOOLS = {"generate_data_export", "geocode_address", "suggest_actions", "generate_contact_buttons"}


def is_valid_uuid(val):
    if not isinstance(val, str):
        return False
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', val.lower()))


def _clamp(value, lo, hi, default=None):
    """Clamps an integer param. Returns default if value is None."""
    if value is None:
        return default
    try:
        return max(lo, min(int(value), hi))
    except (ValueError, TypeError):
        return default


async def execute_tool_rpc(func_name: str, args: dict) -> dict:
    """Executes a read-only Supabase RPC matching the tool schema.

    All parameter normalization, clamping, and validation is applied here.
    Constructs clean keyword arguments matching the exact Postgres DDL signatures
    in pulse_ai_revamped_rpcs.sql to prevent PostgREST schema lookup errors.
    """
    if func_name in _PYTHON_SIDE_TOOLS:
        return {
            "status": "error",
            "message": f"Tool '{func_name}' is handled client-side and must not route to Supabase."
        }

    clean_args: dict = {}

    # ── 1. get_real_estate_kpis (and legacy get_dashboard_kpis) ──
    if func_name in ("get_real_estate_kpis", "get_dashboard_kpis"):
        clean_args = {
            "p_market": args.get("p_market") or args.get("market"),
            "p_platform": args.get("p_platform") or args.get("platform"),
            "p_bedrooms": int(args["p_bedrooms"]) if args.get("p_bedrooms") is not None else None,
            "p_is_active": args.get("p_is_active") if "p_is_active" in args else True,
            "p_property_ids": args.get("p_property_ids") or args.get("property_ids"),
            "p_start_date": args.get("p_start_date") or args.get("start_date"),
            "p_end_date": args.get("p_end_date") or args.get("end_date"),
        }

    # ── 2. get_market_averages ──
    elif func_name == "get_market_averages":
        market = args.get("market_param") or args.get("p_market") or args.get("market")
        if market:
            clean_args["market_param"] = str(market)

    # ── 3. get_market_snapshot ──
    elif func_name == "get_market_snapshot":
        market = args.get("p_market") or args.get("market")
        if not market:
            return {"status": "error", "message": "p_market is required for get_market_snapshot. Call get_tracked_markets to see available markets."}
        start = args.get("p_start_date") or args.get("start_date")
        end = args.get("p_end_date") or args.get("end_date")
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
        clean_args = {
            "p_market": market,
            "p_start_date": start,
            "p_end_date": end,
        }

    # ── 4. get_market_trend ──
    elif func_name == "get_market_trend":
        market = args.get("p_market") or args.get("market")
        if not market:
            return {"status": "error", "message": "p_market is required for get_market_trend. Call get_tracked_markets to see available markets."}
        days = _clamp(args.get("p_days") or args.get("days"), 7, 90, default=14)
        plat = args.get("p_platform") or args.get("platform")
        is_act = args.get("p_is_active") if "p_is_active" in args else True
        clean_args = {
            "p_market": market,
            "p_days": days,
            "p_platform": str(plat) if plat else None,
            "p_is_active": bool(is_act),
        }

    # ── 5. get_spike_alerts ──
    elif func_name == "get_spike_alerts":
        thresh = args.get("threshold_param") or args.get("p_threshold") or args.get("threshold") or 25.0
        days = _clamp(args.get("days_param") or args.get("p_days") or args.get("days"), 1, 30, default=7)
        market = args.get("p_market") or args.get("market")
        limit = _clamp(args.get("p_limit") or args.get("limit"), 1, 15, default=8)
        pos = args.get("p_rank_position") or args.get("rank_position") or "top"
        pos = str(pos).lower() if str(pos).lower() in ("top", "bottom", "middle") else "top"
        clean_args = {
            "threshold_param": max(5.0, min(float(thresh), 100.0)),
            "days_param": days,
            "p_market": str(market) if market else None,
            "p_limit": limit,
            "p_rank_position": pos,
        }

    # ── 6. get_rate_anomaly_report ──
    elif func_name == "get_rate_anomaly_report":
        search = args.get("p_property_search") or args.get("p_search") or args.get("p_market") or args.get("property_search") or ""
        days = _clamp(args.get("p_days") or args.get("days"), 1, 90, default=30)
        dev = args.get("p_deviation_threshold") or args.get("p_threshold") or args.get("deviation_threshold") or 25.0
        clean_args = {
            "p_property_search": str(search) if search else None,
            "p_days": days,
            "p_deviation_threshold": max(5.0, min(float(dev), 100.0)),
        }

    # ── 7. get_most_volatile_properties ──
    elif func_name == "get_most_volatile_properties":
        market = args.get("p_market") or args.get("market")
        days = _clamp(args.get("p_days") or args.get("days"), 7, 90, default=14)
        limit = _clamp(args.get("p_limit") or args.get("limit"), 1, 15, default=5)
        pos = args.get("p_rank_position") or args.get("rank_position") or "top"
        pos = str(pos).lower() if str(pos).lower() in ("top", "bottom", "middle") else "top"
        clean_args = {
            "p_market": market,
            "p_days": days,
            "p_limit": limit,
            "p_rank_position": pos,
        }

    # ── 8. get_property_snapshot ──
    elif func_name == "get_property_snapshot":
        search = args.get("p_property_search") or args.get("p_property_id") or args.get("property_search") or args.get("property_id")
        if not search:
            return {"status": "error", "message": "get_property_snapshot requires p_property_search (UUID or property title)."}
        clean_args = {"p_property_search": str(search)}

    # ── 9. get_property_rate_changes ──
    elif func_name == "get_property_rate_changes":
        search = args.get("property_search") or args.get("p_property_search") or args.get("p_property_id") or args.get("property_id") or args.get("p_market") or ""
        start = args.get("start_date") or args.get("p_start_date")
        end = args.get("end_date") or args.get("p_end_date")
        days = _clamp(args.get("days_param") or args.get("p_days") or args.get("days"), 1, 90, default=14)
        comp = _clamp(args.get("compare_window_days"), 1, 14, default=1)
        limit = _clamp(args.get("p_limit") or args.get("limit"), 1, 30, default=14)
        clean_args = {
            "property_search": str(search),
            "days_param": days,
            "compare_window_days": comp,
            "start_date": start,
            "end_date": end,
            "p_limit": limit,
        }

    # ── 10. compare_properties ──
    elif func_name == "compare_properties":
        ids = args.get("p_property_ids") or args.get("property_ids") or []
        if not isinstance(ids, list) or len(ids) < 2:
            return {"status": "error", "message": "compare_properties requires a list of 2-10 property names or UUIDs in p_property_ids."}
        clean_args = {"p_property_ids": ids[:10]}

    # ── 11. search_properties ──
    elif func_name == "search_properties":
        search = args.get("p_search") or args.get("p_query") or args.get("query")
        market = args.get("p_market") or args.get("market")
        platform = args.get("p_platform") or args.get("platform")
        bedrooms = args.get("p_bedrooms") or args.get("bedrooms")
        avail = args.get("p_available") if "p_available" in args else args.get("available")
        is_act = args.get("p_is_active") if "p_is_active" in args else True
        pos = args.get("p_rank_position") or args.get("rank_position") or "top"
        pos = str(pos).lower() if str(pos).lower() in ("top", "bottom", "middle") else "top"
        sort_by = args.get("p_sort_by") or args.get("sort_by") or "rate"
        limit = _clamp(args.get("p_limit") or args.get("limit"), 1, 20, default=6)
        clean_args = {
            "p_search": str(search) if search else None,
            "p_market": str(market) if market else None,
            "p_platform": str(platform) if platform else None,
            "p_bedrooms": int(bedrooms) if bedrooms is not None else None,
            "p_available": bool(avail) if avail is not None else None,
            "p_is_active": bool(is_act) if is_act is not None else None,
            "p_rank_position": pos,
            "p_sort_by": str(sort_by),
            "p_limit": limit,
        }

    # ── 12. get_availability_rate ──
    elif func_name == "get_availability_rate":
        market = args.get("p_market") or args.get("market")
        platform = args.get("p_platform") or args.get("platform")
        clean_args = {
            "p_market": str(market) if market else None,
            "p_platform": str(platform) if platform else None,
        }

    # ── 13. get_nearby_properties ──
    elif func_name == "get_nearby_properties":
        lat = args.get("p_latitude") or args.get("latitude")
        lon = args.get("p_longitude") or args.get("longitude")
        if lat is None or lon is None:
            return {
                "status": "error",
                "message": "get_nearby_properties requires p_latitude and p_longitude. Call geocode_address first if you only have an address."
            }
        rad = max(0.1, min(float(args.get("p_radius_km") or args.get("radius_km") or 5.0), 20.0))
        limit = _clamp(args.get("p_limit") or args.get("limit"), 1, 20, default=6)
        clean_args = {
            "p_latitude": float(lat),
            "p_longitude": float(lon),
            "p_radius_km": rad,
            "p_limit": limit,
        }

    # ── 14. get_distance_km ──
    elif func_name == "get_distance_km":
        p_a = args.get("property_a_id") or args.get("p_from_property_id") or args.get("from_property_id")
        p_b = args.get("property_b_id") or args.get("p_to_property_id") or args.get("to_property_id")
        if not is_valid_uuid(p_a) or not is_valid_uuid(p_b):
            return {"status": "error", "message": "Invalid UUID format. Both property IDs must be valid UUIDs."}
        clean_args = {
            "property_a_id": p_a,
            "property_b_id": p_b,
        }

    # ── 15. get_tracked_markets ──
    elif func_name == "get_tracked_markets":
        plat = args.get("p_platform") or args.get("platform")
        clean_args = {
            "p_platform": str(plat) if plat else None,
        }

    # ── 16. get_recently_changed_tracking ──
    elif func_name == "get_recently_changed_tracking":
        days = _clamp(args.get("p_days") or args.get("days"), 1, 90, default=30)
        clean_args = {"p_days": days}

    # ── 17. get_property_detail ──
    elif func_name == "get_property_detail":
        search = args.get("p_property_search") or args.get("property_search") or args.get("property_id") or args.get("name")
        if not search:
            return {"status": "error", "message": "get_property_detail requires p_property_search (UUID or property title)."}
        days = _clamp(args.get("p_history_days") or args.get("history_days") or args.get("days"), 1, 30, default=14)
        clean_args = {
            "p_property_search": str(search),
            "p_history_days": days,
        }

    # ── get_market_rate_changes ──
    elif func_name == "get_market_rate_changes":
        market = args.get("p_market") or args.get("market")
        days = _clamp(args.get("p_days") or args.get("days"), 1, 30, default=7)
        # Hard cap at 5 — examples array must stay tiny to avoid token bloat
        limit = _clamp(args.get("p_limit") or args.get("limit"), 1, 5, default=5)
        clean_args = {
            "p_market": str(market) if market else None,
            "p_days": days,
            "p_limit": limit,
        }

    else:
        return {
            "status": "error",
            "message": f"Unknown tool name: '{func_name}'"
        }

    try:
        # Strip None values to allow Postgres DEFAULT expressions to resolve cleanly
        query_params = {k: v for k, v in clean_args.items() if v is not None}
        res = supabase.rpc(func_name, query_params).execute()
        raw = res.data

        if raw is None:
            return {"status": "success", "data": []}

        if isinstance(raw, dict):
            if "status" not in raw:
                raw["status"] = "success"
            return raw

        if isinstance(raw, list):
            return {"status": "success", "data": raw}

        return {"status": "success", "data": raw}

    except Exception as e:
        err_msg = str(e)
        if "PGRST202" in err_msg:
            return {
                "status": "error",
                "message": f"Tool argument schema mismatch for '{func_name}'. Ensure parameters strictly match pulse_ai_revamped_rpcs.sql."
            }
        return {"status": "error", "message": err_msg}


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
