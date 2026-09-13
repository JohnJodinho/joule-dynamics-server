import os
import re
from datetime import date as dt_date
from supabase import create_client, Client
from services.embedding_service import get_embedding_model
from config import SUPABASE_URL, SUPABASE_KEY

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
embedder = get_embedding_model()

_PYTHON_SIDE_TOOLS = {"generate_data_export", "geocode_address", "suggest_actions", "generate_contact_buttons"}
_VALID_POSITIONS = {"top", "bottom", "middle"}


def is_valid_uuid(val) -> bool:
    """Validates whether a value is a valid UUID string."""
    if not isinstance(val, str):
        return False
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', val.lower()))


def _first_val(d: dict, *keys, default=None):
    """Returns the first non-empty, non-None value among keys in dict d, or default."""
    for k in keys:
        val = d.get(k)
        if val is not None and val != "":
            return val
    return default


def _clamp(value, lo, hi, default=None):
    """Clamps an integer parameter to [lo, hi]. Returns default on missing or invalid value."""
    if value is None:
        return default
    try:
        return max(lo, min(int(value), hi))
    except (ValueError, TypeError):
        return default


def _norm_rank_pos(pos) -> str:
    """Normalizes rank position to 'top', 'bottom', or 'middle'."""
    pos_str = str(pos or "top").lower()
    return pos_str if pos_str in _VALID_POSITIONS else "top"


def _norm_kpis(args: dict) -> dict:
    """Normalizes parameters for get_real_estate_kpis."""
    bedrooms = _first_val(args, "p_bedrooms", "bedrooms")
    return {
        "p_market": _first_val(args, "p_market", "market"),
        "p_platform": _first_val(args, "p_platform", "platform"),
        "p_bedrooms": int(bedrooms) if bedrooms is not None else None,
        "p_is_active": args.get("p_is_active") if "p_is_active" in args else True,
        "p_property_ids": _first_val(args, "p_property_ids", "property_ids"),
        "p_start_date": _first_val(args, "p_start_date", "start_date"),
        "p_end_date": _first_val(args, "p_end_date", "end_date"),
    }


def _norm_market_averages(args: dict) -> dict:
    """Normalizes parameters for get_market_averages."""
    market = _first_val(args, "market_param", "p_market", "market")
    return {"market_param": str(market)} if market else {}


def _norm_market_snapshot(args: dict) -> dict:
    """Normalizes parameters for get_market_snapshot with date range validation."""
    market = _first_val(args, "p_market", "market")
    if not market:
        return {"status": "error", "message": "p_market is required for get_market_snapshot. Call get_tracked_markets to see available markets."}
    start = _first_val(args, "p_start_date", "start_date")
    end = _first_val(args, "p_end_date", "end_date")
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
    return {
        "p_market": market,
        "p_start_date": start,
        "p_end_date": end,
    }


def _norm_market_trend(args: dict) -> dict:
    """Normalizes parameters for get_market_trend."""
    market = _first_val(args, "p_market", "market")
    if not market:
        return {"status": "error", "message": "p_market is required for get_market_trend. Call get_tracked_markets to see available markets."}
    days = _clamp(_first_val(args, "p_days", "days"), 7, 90, default=14)
    plat = _first_val(args, "p_platform", "platform")
    is_act = args.get("p_is_active") if "p_is_active" in args else True
    return {
        "p_market": market,
        "p_days": days,
        "p_platform": str(plat) if plat else None,
        "p_is_active": bool(is_act),
    }


def _norm_spike_alerts(args: dict) -> dict:
    """Normalizes parameters for get_spike_alerts."""
    thresh = float(_first_val(args, "threshold_param", "p_threshold", "threshold", default=25.0))
    days = _clamp(_first_val(args, "days_param", "p_days", "days"), 1, 30, default=7)
    market = _first_val(args, "p_market", "market")
    limit = _clamp(_first_val(args, "p_limit", "limit"), 1, 15, default=8)
    pos = _norm_rank_pos(_first_val(args, "p_rank_position", "rank_position"))
    return {
        "threshold_param": max(5.0, min(thresh, 100.0)),
        "days_param": days,
        "p_market": str(market) if market else None,
        "p_limit": limit,
        "p_rank_position": pos,
    }


def _norm_rate_anomaly_report(args: dict) -> dict:
    """Normalizes parameters for get_rate_anomaly_report."""
    search = _first_val(args, "p_property_search", "p_search", "p_market", "property_search")
    days = _clamp(_first_val(args, "p_days", "days"), 1, 90, default=30)
    dev = float(_first_val(args, "p_deviation_threshold", "p_threshold", "deviation_threshold", default=25.0))
    return {
        "p_property_search": str(search) if search else None,
        "p_days": days,
        "p_deviation_threshold": max(5.0, min(dev, 100.0)),
    }


def _norm_most_volatile_properties(args: dict) -> dict:
    """Normalizes parameters for get_most_volatile_properties."""
    market = _first_val(args, "p_market", "market")
    days = _clamp(_first_val(args, "p_days", "days"), 7, 90, default=14)
    limit = _clamp(_first_val(args, "p_limit", "limit"), 1, 15, default=5)
    pos = _norm_rank_pos(_first_val(args, "p_rank_position", "rank_position"))
    return {
        "p_market": market,
        "p_days": days,
        "p_limit": limit,
        "p_rank_position": pos,
    }


def _norm_property_snapshot(args: dict) -> dict:
    """Normalizes parameters for get_property_snapshot."""
    search = _first_val(args, "p_property_search", "p_property_id", "property_search", "property_id")
    if not search:
        return {"status": "error", "message": "get_property_snapshot requires p_property_search (UUID or property title)."}
    return {"p_property_search": str(search)}


def _norm_property_rate_changes(args: dict) -> dict:
    """Normalizes parameters for get_property_rate_changes."""
    search = _first_val(args, "property_search", "p_property_search", "p_property_id", "property_id", "p_market", default="")
    start = _first_val(args, "start_date", "p_start_date")
    end = _first_val(args, "end_date", "p_end_date")
    days = _clamp(_first_val(args, "days_param", "p_days", "days"), 1, 90, default=14)
    comp = _clamp(args.get("compare_window_days"), 1, 14, default=1)
    limit = _clamp(_first_val(args, "p_limit", "limit"), 1, 30, default=14)
    return {
        "property_search": str(search),
        "days_param": days,
        "compare_window_days": comp,
        "start_date": start,
        "end_date": end,
        "p_limit": limit,
    }


def _norm_compare_properties(args: dict) -> dict:
    """Normalizes parameters for compare_properties."""
    ids = _first_val(args, "p_property_ids", "property_ids", default=[])
    if not isinstance(ids, list) or len(ids) < 2:
        return {"status": "error", "message": "compare_properties requires a list of 2-10 property names or UUIDs in p_property_ids."}
    return {"p_property_ids": ids[:10]}


def _norm_search_properties(args: dict) -> dict:
    """Normalizes parameters for search_properties."""
    search = _first_val(args, "p_search", "p_query", "query")
    market = _first_val(args, "p_market", "market")
    platform = _first_val(args, "p_platform", "platform")
    bedrooms = _first_val(args, "p_bedrooms", "bedrooms")
    avail = args.get("p_available") if "p_available" in args else args.get("available")
    is_act = args.get("p_is_active") if "p_is_active" in args else True
    pos = _norm_rank_pos(_first_val(args, "p_rank_position", "rank_position"))
    sort_by = _first_val(args, "p_sort_by", "sort_by", default="rate")
    limit = _clamp(_first_val(args, "p_limit", "limit"), 1, 20, default=6)
    return {
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


def _norm_availability_rate(args: dict) -> dict:
    """Normalizes parameters for get_availability_rate."""
    market = _first_val(args, "p_market", "market")
    platform = _first_val(args, "p_platform", "platform")
    return {
        "p_market": str(market) if market else None,
        "p_platform": str(platform) if platform else None,
    }


def _norm_nearby_properties(args: dict) -> dict:
    """Normalizes parameters for get_nearby_properties."""
    lat = _first_val(args, "p_latitude", "latitude")
    lon = _first_val(args, "p_longitude", "longitude")
    if lat is None or lon is None:
        return {
            "status": "error",
            "message": "get_nearby_properties requires p_latitude and p_longitude. Call geocode_address first if you only have an address."
        }
    rad_raw = _first_val(args, "p_radius_km", "radius_km", default=5.0)
    rad = max(0.1, min(float(rad_raw), 20.0))
    limit = _clamp(_first_val(args, "p_limit", "limit"), 1, 20, default=6)
    return {
        "p_latitude": float(lat),
        "p_longitude": float(lon),
        "p_radius_km": rad,
        "p_limit": limit,
    }


def _norm_distance_km(args: dict) -> dict:
    """Normalizes parameters for get_distance_km."""
    p_a = _first_val(args, "property_a_id", "p_from_property_id", "from_property_id")
    p_b = _first_val(args, "property_b_id", "p_to_property_id", "to_property_id")
    if not is_valid_uuid(p_a) or not is_valid_uuid(p_b):
        return {"status": "error", "message": "Invalid UUID format. Both property IDs must be valid UUIDs."}
    return {
        "property_a_id": p_a,
        "property_b_id": p_b,
    }


def _norm_tracked_markets(args: dict) -> dict:
    """Normalizes parameters for get_tracked_markets."""
    plat = _first_val(args, "p_platform", "platform")
    return {"p_platform": str(plat) if plat else None}


def _norm_recently_changed_tracking(args: dict) -> dict:
    """Normalizes parameters for get_recently_changed_tracking."""
    days = _clamp(_first_val(args, "p_days", "days"), 1, 90, default=30)
    return {"p_days": days}


def _norm_property_detail(args: dict) -> dict:
    """Normalizes parameters for get_property_detail."""
    search = _first_val(args, "p_property_search", "property_search", "property_id", "name")
    if not search:
        return {"status": "error", "message": "get_property_detail requires p_property_search (UUID or property title)."}
    days = _clamp(_first_val(args, "p_history_days", "history_days", "days"), 1, 30, default=14)
    return {
        "p_property_search": str(search),
        "p_history_days": days,
    }


def _norm_market_rate_changes(args: dict) -> dict:
    """Normalizes parameters for get_market_rate_changes."""
    market = _first_val(args, "p_market", "market")
    days = _clamp(_first_val(args, "p_days", "days"), 1, 30, default=7)
    limit = _clamp(_first_val(args, "p_limit", "limit"), 1, 5, default=5)
    return {
        "p_market": str(market) if market else None,
        "p_days": days,
        "p_limit": limit,
    }


_TOOL_NORMALIZERS = {
    "get_real_estate_kpis": _norm_kpis,
    "get_dashboard_kpis": _norm_kpis,
    "get_market_averages": _norm_market_averages,
    "get_market_snapshot": _norm_market_snapshot,
    "get_market_trend": _norm_market_trend,
    "get_spike_alerts": _norm_spike_alerts,
    "get_rate_anomaly_report": _norm_rate_anomaly_report,
    "get_most_volatile_properties": _norm_most_volatile_properties,
    "get_property_snapshot": _norm_property_snapshot,
    "get_property_rate_changes": _norm_property_rate_changes,
    "compare_properties": _norm_compare_properties,
    "search_properties": _norm_search_properties,
    "get_availability_rate": _norm_availability_rate,
    "get_nearby_properties": _norm_nearby_properties,
    "get_distance_km": _norm_distance_km,
    "get_tracked_markets": _norm_tracked_markets,
    "get_recently_changed_tracking": _norm_recently_changed_tracking,
    "get_property_detail": _norm_property_detail,
    "get_market_rate_changes": _norm_market_rate_changes,
}


async def _call_supabase_rpc(func_name: str, clean_args: dict) -> dict:
    """Invokes Supabase RPC with cleaned parameters and formats response."""
    try:
        query_params = {k: v for k, v in clean_args.items() if v is not None}
        res = supabase.rpc(func_name, query_params).execute()
        raw = res.data

        if raw is None:
            return {"status": "success", "data": []}

        if isinstance(raw, dict):
            if "status" not in raw:
                raw["status"] = "success"
            return raw

        return {"status": "success", "data": raw}

    except Exception as e:
        err_msg = str(e)
        if "PGRST202" in err_msg:
            return {
                "status": "error",
                "message": f"Tool argument schema mismatch for '{func_name}'. Ensure parameters strictly match pulse_ai_revamped_rpcs.sql."
            }
        return {"status": "error", "message": err_msg}


async def execute_tool_rpc(func_name: str, args: dict) -> dict:
    """Executes a read-only Supabase RPC matching the tool schema."""
    if func_name in _PYTHON_SIDE_TOOLS:
        return {
            "status": "error",
            "message": f"Tool '{func_name}' is handled client-side and must not route to Supabase."
        }

    normalizer = _TOOL_NORMALIZERS.get(func_name)
    if not normalizer:
        return {
            "status": "error",
            "message": f"Unknown tool name: '{func_name}'"
        }

    clean_args = normalizer(args)
    if "status" in clean_args and clean_args["status"] == "error":
        return clean_args

    return await _call_supabase_rpc(func_name, clean_args)


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
