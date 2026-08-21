"""
services/tool_compressor.py
────────────────────────────
Token-efficient tool output compression and the geocode_address_handler.

compress_tool_output():  Converts raw RPC dicts into compact strings for LLM
context. Uses metadata hoisting, null-stripping, and CSV rendering.
Estimated reduction: 70-90% vs raw JSON for time-series data.

geocode_address_handler(): Resolves addresses to lat/lng via Mapbox.
"""

import csv
import io
import json
import requests
from services.observability import setup_logger
from config import MAPBOX_ACCESS_TOKEN

logger = setup_logger(__name__)

_MAX_ROWS = 50   # Maximum rows delivered to LLM from any single tool call


def compress_tool_output(func_name: str, db_result: dict) -> str:
    """
    Three-step compression pipeline:
    1. Metadata hoisting: keys with identical values across all rows are
       extracted to a single header line, removing them from every row.
    2. Null stripping: any key with a null/None value in a row is omitted.
    3. CSV rendering: remaining data written as CSV (headers once, values compact).

    For non-tabular (scalar/dict) results, returns a minimal string.
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
        return f"Tool '{func_name}' result:\n{json.dumps(data, default=str)}"

    if not isinstance(data, list) or len(data) == 0:
        return f"Tool '{func_name}': empty result."

    # ── Filter out all-null rows ─────────────────────────────────────────────
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
    active_keys = [k for k in row_keys if any(row.get(k) is not None for row in data)]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(active_keys)
    for row in data:
        writer.writerow([row.get(k) for k in active_keys])

    truncation_notice = (
        f"\n[Truncated to {_MAX_ROWS} rows. Advise user to narrow date range or add filters.]"
        if truncated else ""
    )
    return f"{header_str}{buf.getvalue().strip()}{truncation_notice}"


# ─── GEOCODE HANDLER (Mapbox API) ─────────────────────────────────────────────

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
