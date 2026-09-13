"""
services/tool_compressor.py
─────────────────────────────────────────────────────────────────────────────
Token-efficient tool output compression and the geocode_address_handler.

compress_tool_output(): Converts raw RPC dicts into compact strings for LLM context.
geocode_address_handler(): Resolves addresses to lat/lng via Mapbox.
"""

import csv
import io
import json
import requests
from services.observability import setup_logger
from config import MAPBOX_ACCESS_TOKEN

logger = setup_logger(__name__)

_MAX_ROWS = 30
_MAX_RESULT_CHARS = 2000

_LIST_KEYS = ("items", "results", "compared", "rate_history", "ranked_properties", "newly_added", "untracked_or_removed")


def _clean_value(val):
    """Truncates ISO datetime strings to date-only format for token economy."""
    if isinstance(val, str) and len(val) >= 19 and "T" in val and (val.endswith("Z") or "+" in val):
        return val[:10]
    return val


def _extract_raw_data(func_name: str, db_result: dict) -> tuple[str | None, any]:
    """Validates status and extracts the payload, handling scalars early."""
    if db_result.get("status") != "success":
        msg = db_result.get("message", "unknown error")
        return f"Tool '{func_name}' error: {msg}", None

    data = db_result.get("data") if "data" in db_result else {k: v for k, v in db_result.items() if k != "status"}
    if not data:
        return f"Tool '{func_name}': no data returned.", None

    if isinstance(data, (str, int, float, bool)):
        return f"Tool '{func_name}' result: {data}", None

    return None, data


def _find_list_envelope(data: dict) -> tuple[str | None, list | None, dict]:
    """Inspects a dict payload for known list envelope keys and extracts metadata."""
    for lk in _LIST_KEYS:
        val = data.get(lk)
        if isinstance(val, list):
            outer_meta = {k: v for k, v in data.items() if k != lk and not isinstance(v, (list, dict))}
            return lk, val, outer_meta
    return None, None, {}


def _unwrap_payload(func_name: str, db_result: dict) -> tuple[str | None, list | None, dict]:
    """Extracts list data and outer metadata from raw RPC result dictionary."""
    early_err, data = _extract_raw_data(func_name, db_result)
    if early_err is not None:
        return early_err, None, {}

    outer_metadata = {}
    if isinstance(data, dict):
        found_key, list_val, outer_metadata = _find_list_envelope(data)
        if not found_key:
            return f"Tool '{func_name}' result:\n{json.dumps(data, default=str)}", None, {}
        data = list_val

    if not isinstance(data, list) or not data:
        meta_prefix = f"[{', '.join(f'{k}={v}' for k, v in outer_metadata.items())}]\n" if outer_metadata else ""
        return f"Tool '{func_name}':\n{meta_prefix}0 items returned.", None, {}

    return None, data, outer_metadata


def _filter_and_slice_rows(func_name: str, rows: list) -> tuple[str | None, list[dict], bool]:
    """Filters out empty rows and slices list to _MAX_ROWS limit."""
    data = [row for row in rows if isinstance(row, dict) and any(v is not None for v in row.values())]
    if not data:
        return f"Tool '{func_name}': all returned rows were empty.", [], False
    if len(data) > _MAX_ROWS:
        return None, data[:_MAX_ROWS], True
    return None, data, False


def _find_candidate_keys(first_row: dict) -> list[str]:
    """Returns column keys to process, omitting redundant property_id when property_name exists."""
    all_keys = list(first_row.keys())
    has_name = "property_name" in all_keys
    if has_name and "property_id" in all_keys:
        return [k for k in all_keys if k != "property_id"]
    return all_keys


def _hoist_metadata(data: list[dict], outer_metadata: dict) -> tuple[str, list[str]]:
    """Hoists columns with identical values across all rows into a bracketed header."""
    candidate_keys = _find_candidate_keys(data[0])
    hoisted = dict(outer_metadata)
    row_keys = []

    for key in candidate_keys:
        unique_values = {str(row.get(key)) for row in data}
        if len(unique_values) == 1 and data[0].get(key) is not None:
            hoisted[key] = data[0].get(key)
        else:
            row_keys.append(key)

    header_parts = [f"{k}={v}" for k, v in hoisted.items()]
    header_str = f"[{', '.join(header_parts)}]\n" if header_parts else ""
    active_keys = [k for k in row_keys if any(row.get(k) is not None for row in data)]
    return header_str, active_keys


def _render_csv_table(data: list[dict], active_keys: list[str], header_str: str, truncated: bool) -> str:
    """Renders data rows as CSV text with optional truncation notice."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(active_keys)
    for row in data:
        writer.writerow([_clean_value(row.get(k)) for k in active_keys])

    truncation_notice = (
        f"\n[Truncated to {_MAX_ROWS} rows. Advise user to narrow date range or add filters.]"
        if truncated else ""
    )
    return f"{header_str}{buf.getvalue().strip()}{truncation_notice}"


def _cap_result_string(result_str: str) -> str:
    """Enforces _MAX_RESULT_CHARS character cap on formatted output."""
    if len(result_str) <= _MAX_RESULT_CHARS:
        return result_str
    lines = result_str.split("\n")
    capped = []
    total = 0
    for line in lines:
        if total + len(line) > _MAX_RESULT_CHARS:
            capped.append("[Result capped at 2000 chars. Advise user to narrow filters.]")
            break
        capped.append(line)
        total += len(line)
    return "\n".join(capped)


def compress_tool_output(func_name: str, db_result: dict) -> str:
    """Converts raw RPC dicts into compact strings for LLM context."""
    early_return, data, outer_metadata = _unwrap_payload(func_name, db_result)
    if early_return is not None:
        return early_return

    early_filter_err, valid_rows, truncated = _filter_and_slice_rows(func_name, data)
    if early_filter_err is not None:
        return early_filter_err

    header_str, active_keys = _hoist_metadata(valid_rows, outer_metadata)
    rendered = _render_csv_table(valid_rows, active_keys, header_str, truncated)
    return _cap_result_string(rendered)


def _parse_geocode_feature(features: list, address: str) -> dict:
    """Extracts coordinates and formatted address from Mapbox features."""
    if not features:
        return {
            "status": "error",
            "message": f"We are unable to locate coordinates for '{address}'. Please try a more specific address.",
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


def geocode_address_handler(address: str) -> dict:
    """Resolves a free-text address to lat/lng via the Mapbox Geocoding API."""
    if not MAPBOX_ACCESS_TOKEN:
        return {
            "status": "error",
            "message": "We are unable to geocode addresses at this time - the mapping service is not configured.",
        }
    clean_addr = address.strip() if address else ""
    if not clean_addr:
        return {"status": "error", "message": "No address was provided to geocode."}

    params = {
        "q": clean_addr,
        "access_token": MAPBOX_ACCESS_TOKEN,
        "limit": 1,
    }
    from config import GEOCODE_COUNTRY_FILTER
    if GEOCODE_COUNTRY_FILTER:
        params["country"] = GEOCODE_COUNTRY_FILTER

    try:
        response = requests.get("https://api.mapbox.com/search/geocode/v6/forward", params=params, timeout=6)
        response.raise_for_status()
        return _parse_geocode_feature(response.json().get("features", []), clean_addr)
    except requests.exceptions.Timeout:
        logger.error(f"Mapbox geocode timeout for address: {clean_addr}")
        return {"status": "error", "message": "Mapping service timed out. Please try again shortly."}
    except requests.exceptions.HTTPError as e:
        logger.error(f"Mapbox geocode HTTP error {e.response.status_code} for: {clean_addr}")
        return {"status": "error", "message": "Mapping service returned an error. Please try again later."}
    except requests.exceptions.RequestException as e:
        logger.error(f"Mapbox geocode request failed for '{clean_addr}': {e}")
        return {"status": "error", "message": "Unable to reach the mapping service. Please try again later."}
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Mapbox geocode parsing error for '{clean_addr}': {e}")
        return {"status": "error", "message": f"Unable to parse the location for '{clean_addr}'. Please try a more specific address."}
