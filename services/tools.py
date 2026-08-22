"""
Real Estate Intelligence Layer — Tool Definitions & Dynamic Tool Discovery

Implements Hierarchical Dynamic Tool Discovery / Schema Gating:
  - Curated, semantically discriminative retrieval descriptions for all tools.
  - Pre-embedded vector representations for ~20-30ms local cosine ranking.
  - Category gating + Top-K selection with Universal Tools (suggest_actions, generate_data_export).
  - Scalable to hundreds of tools with flat ~600-token prompt footprint.
"""
from typing import List, Dict, Any, Optional
import numpy as np

# ─── 1. UNIVERSAL TOOLS (Always attached to tool payloads) ───────────────────

SUGGEST_ACTIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "suggest_actions",
        "description": (
            "Provide clickable interactive buttons/chips to the user in the UI. Use this to: "
            "1. Ask for missing parameters or clarifications (e.g. ['NYC/NJ Metro', 'Miami']). "
            "2. Offer next-step follow-up queries after answering (e.g. ['View 7-day Spike Report', 'Compare with NYC', 'Export Markdown']). "
            "3. Present binary confirmation choices when asking if user wants deeper analysis (e.g. ['Yes, generate report', 'No, keep overview']). "
            "4. Guide users through multi-step workflows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of 2 to 4 short, action-oriented button labels (max 35 chars each)."
                }
            },
            "required": ["actions"]
        }
    }
}

GENERATE_DATA_EXPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_data_export",
        "description": "Save real estate analysis, market summary, or price breakdown to a downloadable Markdown (.md) report file. Call this tool whenever a user asks to export, save, or download a report.",
        "parameters": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["md"],
                    "description": "Export format, strictly 'md'",
                },
                "content": {
                    "type": "string",
                    "description": "Complete formatted Markdown report content to save into the file",
                },
            },
            "required": ["content"],
        },
    },
}

GENERATE_CONTACT_BUTTONS_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_contact_buttons",
        "description": "Generates clickable Email and WhatsApp contact buttons. Use this whenever the user asks about hiring us, pricing, custom builds, or tracking their own portfolios. The tool returns the exact markdown syntax you must use to display the buttons.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "A personalized, plain English greeting message to pre-fill in the WhatsApp chat based on the user's inquiry (e.g., 'Hi John, I would like to discuss a custom build for my 10 properties in Miami.')."
                }
            },
            "required": ["message"],
        },
    }
}

# ─── 2. DOMAIN TOOLS ─────────────────────────────────────────────────────────

REAL_ESTATE_TOOLS = [
    # ─── DASHBOARD & MARKET OVERVIEW ─────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_kpis",
            "description": "Fetch overall Real Estate Rate Monitor top KPI metrics (properties tracked, 7-day rate changes, 25%+ price spikes, and scrape health status), optionally filtered by market, platform, bedrooms, active status, specific property IDs, or stay date window. Matches the live dashboard top KPI cards.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Optional market region filter ('Miami' or 'NYC/NJ Metro')",
                    },
                    "p_platform": {
                        "type": "string",
                        "description": "Optional booking platform filter ('airbnb' or 'vrbo')",
                    },
                    "p_bedrooms": {
                        "type": "integer",
                        "description": "Optional bedroom count filter (e.g. 1, 2, 3)",
                    },
                    "p_is_active": {
                        "type": "boolean",
                        "description": "Optional tracking status filter: true for currently tracked, false for untracked/archived",
                    },
                    "p_property_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of specific property UUIDs to filter KPIs",
                    },
                    "p_start_date": {
                        "type": "string",
                        "description": "Optional stay date window start (YYYY-MM-DD)",
                    },
                    "p_end_date": {
                        "type": "string",
                        "description": "Optional stay date window end (YYYY-MM-DD)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_averages",
            "description": "Fetch average nightly rates and 7-day trailing average comparisons for a market across active listings. Use when the user asks for market baseline or price benchmarks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Market region: 'Miami' or 'NYC/NJ Metro'",
                    }
                },
                "required": ["p_market"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_snapshot",
            "description": "Fetch a comprehensive single-day snapshot for a market (active property count, average nightly rate, min rate, max rate, availability rate %, and rate spike event count). Use when the user asks for a daily market overview, daily summary, or specific date performance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Market region: 'Miami' or 'NYC/NJ Metro'",
                    },
                    "p_start_date": {
                        "type": "string",
                        "description": "Snapshot date start (YYYY-MM-DD). Defaults to yesterday if omitted.",
                    },
                    "p_end_date": {
                        "type": "string",
                        "description": "Snapshot date end (YYYY-MM-DD). Defaults to yesterday if omitted.",
                    },
                },
                "required": ["p_market"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_trend",
            "description": "Fetch historical daily average rate trends for a market over a specified number of days (up to 90 days). Use when the user asks how rates have changed over time, weekly/monthly trajectories, or market direction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Market region: 'Miami' or 'NYC/NJ Metro'",
                    },
                    "p_days": {
                        "type": "integer",
                        "description": "Number of historical days to analyze (default 30, max 90)",
                    },
                },
                "required": ["p_market"],
            },
        },
    },

    # ─── SPIKES, ANOMALIES & VOLATILITY ──────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_spike_alerts",
            "description": "Fetch active price spike alerts where a property's nightly rate changed by more than the threshold (default 25%) relative to its 7-day trailing average. Use when the user asks about sudden price jumps, surge pricing, or rate spikes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Optional market filter ('Miami' or 'NYC/NJ Metro')",
                    },
                    "p_threshold": {
                        "type": "number",
                        "description": "Percentage change threshold (default 25.0 for 25%+ spikes)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rate_anomaly_report",
            "description": "Fetch detailed rate anomalies (both spikes AND crashes/drops) with listing name, dates, old rate, new rate, baseline 7d avg, and percentage deviation. Use for in-depth pricing anomaly investigations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Optional market filter ('Miami' or 'NYC/NJ Metro')",
                    },
                    "p_days": {
                        "type": "integer",
                        "description": "Lookback window in days (default 7, max 30)",
                    },
                    "p_threshold": {
                        "type": "number",
                        "description": "Minimum percentage deviation (default 20.0 for 20%+ deviation)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_most_volatile_properties",
            "description": "Fetch listings with the highest number of rate adjustments/changes over a lookback window. Use when user asks which properties change prices most frequently, most dynamic pricing, or most volatile listings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Optional market filter ('Miami' or 'NYC/NJ Metro')",
                    },
                    "p_days": {
                        "type": "integer",
                        "description": "Lookback window in days (default 14, max 60)",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Max properties to return (default 10, max 50)",
                    },
                },
                "required": [],
            },
        },
    },

    # ─── INDIVIDUAL PROPERTY & LISTING LEVEL ──────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_property_snapshot",
            "description": "Fetch complete current profile and latest rate data for a specific property by its UUID. Returns property name, market, bedrooms, platform, listing URL, coordinates, current nightly rate, 7-day average baseline, and availability status. Use when user asks about a specific listing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_property_id": {
                        "type": "string",
                        "description": "UUID of the property",
                    }
                },
                "required": ["p_property_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_property_rate_changes",
            "description": "Fetch chronological history of all rate revisions and price adjustments for a specific property or list of properties. Use to analyze price change history over time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_property_id": {
                        "type": "string",
                        "description": "Optional specific property UUID",
                    },
                    "p_property_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of property UUIDs",
                    },
                    "p_market": {
                        "type": "string",
                        "description": "Optional market filter ('Miami' or 'NYC/NJ Metro')",
                    },
                    "p_days": {
                        "type": "integer",
                        "description": "Lookback window in days (default 30, max 90)",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Max changes to return (default 50, max 200)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_properties",
            "description": "Compare 2 to 10 specific properties side-by-side on current rate, 7-day average, bedroom count, platform, and availability status. Use when the user asks to compare specific listings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_property_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of 2 to 10 property UUIDs to compare",
                    }
                },
                "required": ["p_property_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_properties",
            "description": "Search and filter tracked properties by market, bedroom count, platform, active tracking status, name substring, and price range. Returns listing IDs, titles, bedrooms, current rates, and availability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Optional market ('Miami' or 'NYC/NJ Metro')",
                    },
                    "p_bedrooms": {
                        "type": "integer",
                        "description": "Optional bedroom count filter (e.g. 1, 2, 3)",
                    },
                    "p_platform": {
                        "type": "string",
                        "description": "Optional platform ('airbnb' or 'vrbo')",
                    },
                    "p_is_active": {
                        "type": "boolean",
                        "description": "Optional tracking status filter (default true for active)",
                    },
                    "p_query": {
                        "type": "string",
                        "description": "Optional search term to match listing title or address",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Max results (default 20, max 100)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_availability_rate",
            "description": "Fetch availability percentage and booked vs available listing counts for a market or property subset over a date range. Use when user asks about occupancy rates, calendar status, or vacancy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Optional market ('Miami' or 'NYC/NJ Metro')",
                    },
                    "p_days": {
                        "type": "integer",
                        "description": "Forward window in days (default 30, max 90)",
                    },
                },
                "required": [],
            },
        },
    },

    # ─── GEOGRAPHIC & LOCATION TOOLS ─────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "geocode_address",
            "description": "Convert a street address, landmark, or neighborhood name into latitude and longitude coordinates. Use before querying nearby properties if the user provides an address or landmark name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Street address, landmark, or neighborhood (e.g. 'Ocean Drive, Miami Beach' or 'Brickell, Miami')",
                    }
                },
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nearby_properties",
            "description": "Fetch properties within a specified radius (in kilometers) of a target geographic coordinate. Use when the user asks for listings near a location or landmark.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_latitude": {
                        "type": "number",
                        "description": "Target latitude in decimal degrees",
                    },
                    "p_longitude": {
                        "type": "number",
                        "description": "Target longitude in decimal degrees",
                    },
                    "p_radius_km": {
                        "type": "number",
                        "description": "Search radius in kilometers (default 5.0, max 50.0)",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Max properties to return (default 10, max 50)",
                    },
                },
                "required": ["p_latitude", "p_longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_distance_km",
            "description": "Calculate distance in kilometers between two properties or between a property and coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_from_property_id": {
                        "type": "string",
                        "description": "UUID of origin property",
                    },
                    "p_to_property_id": {
                        "type": "string",
                        "description": "UUID of destination property",
                    },
                },
                "required": ["p_from_property_id", "p_to_property_id"],
            },
        },
    },

    # ─── SYSTEM METADATA & TRACKING TOOLS ────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_tracked_markets",
            "description": "Fetch list of all currently supported and active market regions, listing counts, and platform coverage in the database. Use when user asks what markets or cities are available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_active_only": {
                        "type": "boolean",
                        "description": "Whether to only return actively monitored markets (default true)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recently_changed_tracking",
            "description": "Fetch properties that recently had their monitoring status changed (newly added or paused). Use for audit or tracking health queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_days": {
                        "type": "integer",
                        "description": "Lookback window in days (default 7, max 30)",
                    }
                },
                "required": [],
            },
        },
    },
    GENERATE_DATA_EXPORT_TOOL,
    SUGGEST_ACTIONS_TOOL,
]

COMMERCIAL_TOOLS = [
    GENERATE_CONTACT_BUTTONS_TOOL,
    SUGGEST_ACTIONS_TOOL,
]


# ─── 3. TOOL REGISTRY & DISCOVERY METADATA ───────────────────────────────────

TOOL_REGISTRY: List[Dict[str, Any]] = [
    {
        "name": "get_dashboard_kpis",
        "category": "MARKET",
        "retrieval_description": "High-level summary cards, aggregate portfolio metrics, active properties count, average market rate, scrape health status.",
        "schema": REAL_ESTATE_TOOLS[0],
    },
    {
        "name": "get_market_averages",
        "category": "MARKET",
        "retrieval_description": "Average nightly rate and price baselines for a specific market over 7-day, 14-day, or 30-day periods.",
        "schema": REAL_ESTATE_TOOLS[1],
    },
    {
        "name": "get_market_snapshot",
        "category": "MARKET",
        "retrieval_description": "Daily overview snapshot of market performance, nightly rates range, minimum maximum rates, and availability on a specific date.",
        "schema": REAL_ESTATE_TOOLS[2],
    },
    {
        "name": "get_market_trend",
        "category": "MARKET",
        "retrieval_description": "Historical pricing trend direction, rising or falling rates over time, week-over-week rate trajectory.",
        "schema": REAL_ESTATE_TOOLS[3],
    },
    {
        "name": "get_spike_alerts",
        "category": "ANOMALY",
        "retrieval_description": "Sudden sharp price jumps, 25% plus rate increases, price spikes, unseasonal rate surges.",
        "schema": REAL_ESTATE_TOOLS[4],
    },
    {
        "name": "get_rate_anomaly_report",
        "category": "ANOMALY",
        "retrieval_description": "Anomalous pricing patterns, drastic rate drops, price crashes, listings deviating significantly from market baseline.",
        "schema": REAL_ESTATE_TOOLS[5],
    },
    {
        "name": "get_most_volatile_properties",
        "category": "ANOMALY",
        "retrieval_description": "Listings with the most frequent price fluctuations, highest number of rate adjustments, unstable pricing.",
        "schema": REAL_ESTATE_TOOLS[6],
    },
    {
        "name": "get_property_snapshot",
        "category": "PROPERTY",
        "retrieval_description": "Detailed current status and single listing profile for a specific property ID or listing URL.",
        "schema": REAL_ESTATE_TOOLS[7],
    },
    {
        "name": "get_property_rate_changes",
        "category": "PROPERTY",
        "retrieval_description": "Historical log of price changes, chronological rate revisions, past rate adjustments for a single property.",
        "schema": REAL_ESTATE_TOOLS[8],
    },
    {
        "name": "compare_properties",
        "category": "PROPERTY",
        "retrieval_description": "Side-by-side head-to-head comparison of multiple property listings, prices, bedrooms, and metrics.",
        "schema": REAL_ESTATE_TOOLS[9],
    },
    {
        "name": "search_properties",
        "category": "PROPERTY",
        "retrieval_description": "Find and filter property listings by bedroom count, price range, market, availability status, or name.",
        "schema": REAL_ESTATE_TOOLS[10],
    },
    {
        "name": "get_availability_rate",
        "category": "PROPERTY",
        "retrieval_description": "Percentage of units booked versus available, occupancy rates, reservation calendar status.",
        "schema": REAL_ESTATE_TOOLS[11],
    },
    {
        "name": "geocode_address",
        "category": "GEO",
        "retrieval_description": "Convert street address, landmark, or neighborhood name into latitude and longitude geographic coordinates.",
        "schema": REAL_ESTATE_TOOLS[12],
    },
    {
        "name": "get_nearby_properties",
        "category": "GEO",
        "retrieval_description": "Find listings closest to a specific geographic coordinate, landmark, or neighborhood radius.",
        "schema": REAL_ESTATE_TOOLS[13],
    },
    {
        "name": "get_distance_km",
        "category": "GEO",
        "retrieval_description": "Calculate straight-line distance in kilometers between two properties or locations.",
        "schema": REAL_ESTATE_TOOLS[14],
    },
    {
        "name": "get_tracked_markets",
        "category": "MARKET",
        "retrieval_description": "List all cities and regions currently supported and monitored in the database (NYC/NJ Metro, Miami).",
        "schema": REAL_ESTATE_TOOLS[15],
    },
    {
        "name": "get_recently_changed_tracking",
        "category": "MARKET",
        "retrieval_description": "Properties recently added to or removed from active monitoring tracking status.",
        "schema": REAL_ESTATE_TOOLS[16],
    },
]


# ─── 4. SEMANTIC EMBEDDING ENGINE (Lazy Loaded Singleton) ─────────────────────

_TOOL_EMBEDDINGS: Optional[np.ndarray] = None


def _get_tool_embeddings() -> np.ndarray:
    """Compute and cache normalized vector embeddings for all retrieval descriptions."""
    global _TOOL_EMBEDDINGS
    if _TOOL_EMBEDDINGS is None:
        from services.embedding_service import get_embedding_model
        embed_model = get_embedding_model()
        descs = [entry["retrieval_description"] for entry in TOOL_REGISTRY]
        _TOOL_EMBEDDINGS = embed_model.encode(descs, normalize_embeddings=True)
    return _TOOL_EMBEDDINGS


def discover_tools(
    user_query: str,
    categories: Optional[List[str]] = None,
    top_k: int = 4,
    include_export: bool = True,
) -> List[Dict[str, Any]]:
    """
    Hierarchical Dynamic Tool Discovery:
      1. Embed user_query using local embedding model (~20ms).
      2. Filter candidate pool by categories if category gating is active.
      3. Compute cosine similarity against candidate tool retrieval descriptions.
      4. Select Top-K most relevant tools.
      5. Attach Universal Tools (suggest_actions, generate_data_export).

    Guarantees strictly 4 to 6 tools (~600 tokens total) are exposed to the LLM.
    """
    try:
        from services.embedding_service import get_embedding_model
        embed_model = get_embedding_model()
        tool_vectors = _get_tool_embeddings()

        # Category gating
        if categories:
            allowed_cats = {c.upper() for c in categories}
            candidate_indices = [
                i for i, entry in enumerate(TOOL_REGISTRY)
                if entry["category"].upper() in allowed_cats
            ]
        else:
            candidate_indices = list(range(len(TOOL_REGISTRY)))

        if not candidate_indices:
            candidate_indices = list(range(len(TOOL_REGISTRY)))

        q_vec = embed_model.encode([user_query], normalize_embeddings=True)[0]
        candidate_vecs = tool_vectors[candidate_indices]
        sims = candidate_vecs @ q_vec

        # Rank candidates
        ranked_local_indices = np.argsort(sims)[::-1][:top_k]
        selected_tools = [
            TOOL_REGISTRY[candidate_indices[local_idx]]["schema"]
            for local_idx in ranked_local_indices
        ]
    except Exception as exc:
        # Fallback to default market tools if embedding fails
        selected_tools = [
            t for t in REAL_ESTATE_TOOLS
            if t["function"]["name"] in ("get_market_averages", "get_market_snapshot", "get_spike_alerts")
        ]

    # Universal tools: suggest_actions (always) and generate_data_export (optional)
    results = []
    seen_names = set()

    for tool in selected_tools:
        name = tool["function"]["name"]
        if name not in seen_names:
            seen_names.add(name)
            results.append(tool)

    if include_export and "generate_data_export" not in seen_names:
        results.append(GENERATE_DATA_EXPORT_TOOL)
        seen_names.add("generate_data_export")

    if "suggest_actions" not in seen_names:
        results.append(SUGGEST_ACTIONS_TOOL)
        seen_names.add("suggest_actions")

    return results


# ─── 5. BACKWARDS COMPATIBLE SELECTOR ─────────────────────────────────────────

def select_tools(user_query: str) -> List[Dict[str, Any]]:
    """Backwards-compatible alias forwarding to discover_tools()."""
    return discover_tools(user_query)
