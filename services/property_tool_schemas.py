"""Property-level, geospatial, and tracking tool schemas."""

from services.tool_schemas_common import market_desc

PROPERTY_AND_GEO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_property_snapshot",
            "description": "Fetch complete current profile and latest rate data for a specific property by its UUID or listing title. Returns property name, market, bedrooms, platform, listing URL, coordinates, current nightly rate, 7-day average baseline, is_active, and availability status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_property_search": {
                        "type": "string",
                        "description": "Property UUID or search term for listing name",
                    }
                },
                "required": ["p_property_search"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_property_detail",
            "description": "Fetch a deep single-property profile including current status, active tracking state, coordinates, listing URL, and daily-aggregated recent rate history (up to 14 days). Use when user asks to explore or dive deep into a specific property.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_property_search": {
                        "type": "string",
                        "description": "Property UUID or search term for listing name",
                    },
                    "p_history_days": {
                        "type": "integer",
                        "description": "Number of days of daily rate history to include (default 14, max 30)",
                    },
                },
                "required": ["p_property_search"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_property_rate_changes",
            "description": "Fetch chronological daily history of rate revisions and price adjustments for a specific property. Returns one row per calendar day.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_search": {
                        "type": "string",
                        "description": "Property UUID or listing title search term",
                    },
                    "days_param": {
                        "type": "integer",
                        "description": "Lookback window in days (default 14, max 90)",
                    },
                    "compare_window_days": {
                        "type": "integer",
                        "description": "Comparison interval in days (default 1)",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Optional stay date start window (YYYY-MM-DD)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Optional stay date end window (YYYY-MM-DD)",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Max daily entries to return (default 14, max 30)",
                    },
                },
                "required": ["property_search"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_properties",
            "description": "Compare 2 to 10 specific properties side-by-side on current rate, 7-day average, bedroom count, platform, is_active, and availability status. Use when the user asks to compare specific listings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_property_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of 2 to 10 property UUIDs or names to compare",
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
            "description": f"Search and filter tracked properties by market (any tracked region), bedroom count, platform ('airbnb' or 'vrbo'), availability status, title substring, and ranked slices. Currently tracked markets: {market_desc()}",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_search": {
                        "type": "string",
                        "description": "Optional search term to match listing title, address, or neighborhood",
                    },
                    "p_market": {
                        "type": "string",
                        "description": f"Optional market filter. Currently tracked: {market_desc()}",
                    },
                    "p_platform": {
                        "type": "string",
                        "description": "Optional platform ('airbnb' or 'vrbo')",
                    },
                    "p_bedrooms": {
                        "type": "integer",
                        "description": "Optional bedroom count filter (e.g. 1, 2, 3)",
                    },
                    "p_available": {
                        "type": "boolean",
                        "description": "Optional availability filter: true for currently available, false for booked",
                    },
                    "p_is_active": {
                        "type": "boolean",
                        "description": "Optional active status filter (default true for currently tracked)",
                    },
                    "p_rank_position": {
                        "type": "string",
                        "enum": ["top", "bottom", "middle"],
                        "description": "Rank slice: 'top', 'bottom', or 'middle' (default 'top')",
                    },
                    "p_sort_by": {
                        "type": "string",
                        "enum": ["rate", "deviation"],
                        "description": "Sort metric: 'rate' (by nightly price) or 'deviation' (by % deviation from 7d avg). Default 'rate'.",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Max results to return (default 6, max 15). Keep small for conversational conciseness.",
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
            "description": f"Fetch availability percentage and booked vs available listing counts for a market or platform. Use when user asks about occupancy rates, calendar status, or vacancy in any tracked market.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": f"Optional market filter. Currently tracked: {market_desc()}",
                    },
                    "p_platform": {
                        "type": "string",
                        "description": "Optional platform filter ('airbnb' or 'vrbo')",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "geocode_address",
            "description": "Convert a user-provided street address, neighborhood, landmark, or point of interest into precise latitude and longitude geographic coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "The street address, neighborhood, landmark, or point of interest to geocode",
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
            "description": "Find tracked property listings within a radial kilometer distance of a geographic latitude and longitude point. Always call geocode_address first if user gave a street address or neighborhood name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_latitude": {
                        "type": "number",
                        "description": "Target center latitude coordinate",
                    },
                    "p_longitude": {
                        "type": "number",
                        "description": "Target center longitude coordinate",
                    },
                    "p_radius_km": {
                        "type": "number",
                        "description": "Search radius in kilometers (default 5.0, max 20.0)",
                    },
                    "p_limit": {
                        "type": "integer",
                        "description": "Maximum properties to return (default 6, max 15)",
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
            "description": "Calculate exact straight-line distance in kilometers between two properties using their database UUIDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_a_id": {
                        "type": "string",
                        "description": "UUID of the first property",
                    },
                    "property_b_id": {
                        "type": "string",
                        "description": "UUID of the second property",
                    },
                },
                "required": ["property_a_id", "property_b_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tracked_markets",
            "description": f"Fetch a list of all active metropolitan real estate markets currently tracked by the system, with property counts per market. Currently includes: {market_desc()}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_platform": {
                        "type": "string",
                        "description": "Optional booking platform filter ('airbnb' or 'vrbo')",
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
            "description": "Fetch listings that were recently added to or removed from active monitoring tracking status within a lookback period.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_days": {
                        "type": "integer",
                        "description": "Lookback window in days (default 30, max 90)",
                    }
                },
                "required": [],
            },
        },
    },
]
