"""
services/tool_compressor.py
─────────────────────────────────────────────────────────────────────────────
Token-efficient tool output compression and the geocode_address_handler.

compress_tool_output():  Converts raw RPC dicts into compact strings for LLM
context. Uses metadata hoisting, null-stripping, nested list unwrapping,
and CSV rendering.
Estimated reduction: 70-90% vs raw JSON.

geocode_address_handler(): Resolves addresses to lat/lng via Mapbox.
"""

import csv
import io
import json
import requests
from services.observability import setup_logger
from config import MAPBOX_ACCESS_TOKEN

logger = setup_logger(__name__)

_MAX_ROWS = 30         # Maximum rows per tool result
_MAX_RESULT_CHARS = 2000  # Hard char cap per compressed result (~500 tokens)

_LIST_KEYS = ("items", "results", "compared", "rate_history", "ranked_properties", "newly_added", "untracked_or_removed")


def _clean_value(val):
    if isinstance(val, str) and len(val) >= 19 and "T" in val and (val.endswith("Z") or "+" in val):
        # Truncate microsecond ISO timestamp to date or compact datetime: 2026-09-04 03:21
        return val[:10]
    return val


def compress_tool_output(func_name: str, db_result: dict) -> str:
    """
    Multi-step compression pipeline:
    1. Unwraps nested list envelopes ({total_matching_count, returned_count, items}).
    2. Metadata hoisting: keys with identical values across all rows are
       extracted to a single header line, removing them from every row.
    3. Null stripping: any key with a null/None value in a row is omitted.
    4. CSV rendering: remaining data written as CSV (headers once, values compact).

    For non-tabular (scalar/dict) results, returns a minimal string.
    """
    if db_result.get("status") != "success":
        msg = db_result.get("message", "unknown error")
        return f"Tool '{func_name}' error: {msg}"

    if "data" in db_result:
        data = db_result.get("data")
    else:
        data = {k: v for k, v in db_result.items() if k != "status"}

    # ── Scalar / single-object results ──
    if data is None or (isinstance(data, dict) and len(data) == 0):
        return f"Tool '{func_name}': no data returned."

    if isinstance(data, (str, int, float, bool)):
        return f"Tool '{func_name}' result: {data}"

    outer_metadata = {}
    # Check if dict contains an inner list envelope
    if isinstance(data, dict):
        found_list_key = None
        for lk in _LIST_KEYS:
            if lk in data and isinstance(data[lk], list):
                found_list_key = lk
                break

        if found_list_key:
            # Hoist outer metadata
            for k, v in data.items():
                if k != found_list_key and not isinstance(v, (list, dict)):
                    outer_metadata[k] = v
            data = data[found_list_key]
        else:
            return f"Tool '{func_name}' result:\n{json.dumps(data, default=str)}"

    if not isinstance(data, list) or len(data) == 0:
        meta_prefix = f"[{', '.join(f'{k}={v}' for k, v in outer_metadata.items())}]\n" if outer_metadata else ""
        return f"Tool '{func_name}':\n{meta_prefix}0 items returned."

    # ── Filter out all-null rows ──
    data = [
        row for row in data
        if isinstance(row, dict) and any(v is not None for v in row.values())
    ]
    if not data:
        return f"Tool '{func_name}': all returned rows were empty."

    # Truncate before processing
    truncated = False
    if len(data) > _MAX_ROWS:
        data = data[:_MAX_ROWS]
        truncated = True

    # ── Step 1: Metadata hoisting ──
    all_keys = list(data[0].keys())
    # Omit property_id from CSV rows if property_name is available to save tokens
    has_name = "property_name" in all_keys
    candidate_keys = [k for k in all_keys if not (k == "property_id" and has_name)]

    hoisted = dict(outer_metadata)
    row_keys = []
    for key in candidate_keys:
        unique_values = {str(row.get(key)) for row in data}
        if len(unique_values) == 1:
            val = data[0].get(key)
            if val is not None:
                hoisted[key] = val
        else:
            row_keys.append(key)

    header_parts = [f"{k}={v}" for k, v in hoisted.items()]
    header_str = f"[{', '.join(header_parts)}]\n" if header_parts else ""

    # ── Step 2 & 3: Null-strip + CSV ──
    active_keys = [k for k in row_keys if any(row.get(k) is not None for row in data)]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(active_keys)
    for row in data:
        writer.writerow([_clean_value(row.get(k)) for k in active_keys])

    truncation_notice = (
        f"\n[Truncated to {_MAX_ROWS} rows. Advise user to narrow date range or add filters.]"
        if truncated else ""
    )
    result_str = f"{header_str}{buf.getvalue().strip()}{truncation_notice}"
    # Hard cap: prevent any single tool result from exceeding budget
    if len(result_str) > _MAX_RESULT_CHARS:
        lines = result_str.split("\n")
        capped = []
        total = 0
        for line in lines:
            if total + len(line) > _MAX_RESULT_CHARS:
                capped.append("[Result capped at 2000 chars. Advise user to narrow filters.]")
                break
            capped.append(line)
            total += len(line)
        result_str = "\n".join(capped)
    return result_str


# ── GEOCODE HANDLER (Mapbox API) ──

def geocode_address_handler(address: str) -> dict:
    """
    Resolves a free-text address to lat/lng via the Mapbox Geocoding API.
    Handles network failures, API errors, and empty results gracefully.
    US-only results, single best match returned.
    """
    if not MAPBOX_ACCESS_TOKEN:
        return {
            "status": "error",
            "message": "We are unable to geocode addresses at this time - the mapping service is not configured.",
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

    except requests.exceptions.Timeout:
        logger.error(f"Mapbox geocode timeout for address: {address}")
        return {"status": "error", "message": "Mapping service timed out. Please try again shortly."}
    except requests.exceptions.HTTPError as e:
        logger.error(f"Mapbox geocode HTTP error {e.response.status_code} for: {address}")
        return {"status": "error", "message": "Mapping service returned an error. Please try again later."}
    except requests.exceptions.RequestException as e:
        logger.error(f"Mapbox geocode request failed for '{address}': {e}")
        return {"status": "error", "message": "Unable to reach the mapping service. Please try again later."}
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Mapbox geocode parsing error for '{address}': {e}")
        return {"status": "error", "message": f"Unable to parse the location for '{address}'. Please try a more specific address."}
