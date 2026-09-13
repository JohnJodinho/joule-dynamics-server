"""Tool JSON schemas and parameter definitions for Real Estate Intelligence Layer."""

from services.tool_schemas_common import market_desc
from services.universal_tool_schemas import (
    SUGGEST_ACTIONS_TOOL,
    GENERATE_DATA_EXPORT_TOOL,
    GENERATE_CONTACT_BUTTONS_TOOL,
    COMMERCIAL_TOOLS,
)
from services.property_tool_schemas import PROPERTY_AND_GEO_TOOLS

_MARKET_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_real_estate_kpis",
            "description": "Fetch overall Real Estate Rate Monitor top KPI metrics (properties tracked, available vs booked count, availability %, 7-day rate changes, 25%+ price spikes, and scrape health), optionally filtered by market, platform, bedrooms, active status, specific property IDs, or stay date window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": f"Optional market region filter. Currently tracked: {market_desc()}",
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
            "description": "Fetch average nightly rates and 7-day trailing average price comparisons for current active listings in a market. Use when the user asks for market baseline, average prices, or nightly benchmarks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "market_param": {
                        "type": "string",
                        "description": f"Market region name. Currently tracked markets: {market_desc()}",
                    }
                },
                "required": ["market_param"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_snapshot",
            "description": f"Fetch a comprehensive single-day snapshot for a market (active property count, average nightly rate, min rate, max rate, availability rate %, and rate spike event count). Use when the user asks for a daily market overview, daily summary, market condition, or specific date performance in any tracked market.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": f"Market region. Currently tracked markets: {market_desc()}",
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
            "description": f"Fetch historical daily average rate trends for a market over a specified number of days (up to 90 days). Use when the user asks how rates have changed over time, weekly/monthly trajectories, or market direction in any tracked market.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": f"Market region. Currently tracked markets: {market_desc()}",
                    },
                    "p_days": {
                        "type": "integer",
                        "description": "Number of historical days to analyze (default 14, max 90)",
                    },
                    "p_platform": {
                        "type": "string",
                        "description": "Optional booking platform filter ('airbnb' or 'vrbo')",
                    },
                    "p_is_active": {
                        "type": "boolean",
                        "description": "Optional active status filter (default true to exclude frozen historical properties)",
                    },
                },
                "required": ["p_market"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_rate_changes",
            "description": (
                "Count how many properties in a market had ANY nightly rate increase or decrease "
                "within the last N days — regardless of magnitude, not just 25%+ spikes. "
                "Returns: total properties with changes, number of increases, number of decreases, "
                "average % change, and up to 5 example properties (aggregated, no token bloat). "
                "Use for: 'how many properties had rate changes', 'rate increases or decreases', "
                "'price movements', 'any rate change at all', 'which listings adjusted their rates'. "
                f"Currently tracked markets: {market_desc()}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": f"Market region to query. Currently tracked: {market_desc()}",
                    },
                    "p_days": {
                        "type": "integer",
                        "description": "Lookback window in days (default 7, max 30). Use 1 for 'last 24h', 3 for 'last few days', 7 for 'last week'.",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Max example properties in response (default 5, max 5 — kept small to avoid token bloat).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spike_alerts",
            "description": "Fetch sudden sharp price changes and 25%+ price spikes, sorted by deviation intensity. Supports ranked slices (top, bottom, middle).",
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold_param": {
                        "type": "number",
                        "description": "Percentage spike deviation threshold (default 25.0)",
                    },
                    "days_param": {
                        "type": "integer",
                        "description": "Historical days lookback (default 7, max 30)",
                    },
                    "p_market": {
                        "type": "string",
                        "description": f"Optional market filter. Currently tracked: {market_desc()}",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Max spikes to return (default 8, max 15)",
                    },
                    "p_rank_position": {
                        "type": "string",
                        "enum": ["top", "bottom", "middle"],
                        "description": "Rank slice: 'top' (most extreme spikes), 'bottom' (mildest spikes), or 'middle' (median spikes). Default 'top'.",
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
            "description": "Fetch a deep-dive anomaly report for a specific property: historical normal price bounds, baseline average, anomaly frequency, and recent abnormal pricing spikes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_property_search": {
                        "type": "string",
                        "description": "Property UUID or search term matching property name",
                    },
                    "p_days": {
                        "type": "integer",
                        "description": "Analysis window in days (default 30, max 90)",
                    },
                    "p_deviation_threshold": {
                        "type": "number",
                        "description": "Percentage threshold defining an anomaly (default 25.0)",
                    },
                },
                "required": ["p_property_search"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_most_volatile_properties",
            "description": "Identify property listings with the highest price volatility and most frequent rate adjustments. Supports ranked slices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": f"Optional market filter. Currently tracked: {market_desc()}",
                    },
                    "p_days": {
                        "type": "integer",
                        "description": "Lookback window in days (default 14, max 90)",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Max properties to return (default 5, max 15)",
                    },
                    "p_rank_position": {
                        "type": "string",
                        "enum": ["top", "bottom", "middle"],
                        "description": "Rank slice: 'top' (highest volatility), 'bottom' (lowest volatility), or 'middle' (median). Default 'top'.",
                    },
                },
                "required": [],
            },
        },
    },
]

REAL_ESTATE_TOOLS = _MARKET_TOOLS + PROPERTY_AND_GEO_TOOLS + [GENERATE_DATA_EXPORT_TOOL]

__all__ = [
    "REAL_ESTATE_TOOLS",
    "COMMERCIAL_TOOLS",
    "SUGGEST_ACTIONS_TOOL",
    "GENERATE_DATA_EXPORT_TOOL",
    "GENERATE_CONTACT_BUTTONS_TOOL",
]
