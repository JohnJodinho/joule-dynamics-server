"""
Real Estate Intelligence Layer — Tool Definitions
Covers all registered Supabase RPCs plus Python-side handlers.
"""

REAL_ESTATE_TOOLS = [
    # ─── DASHBOARD & MARKET OVERVIEW ─────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_kpis",
            "description": "Fetch overall Real Estate Rate Monitor KPIs: tracked property count, 7-day rate changes, spike count, and scrape health status per platform. Use for general 'how is the system doing?' or market health questions.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_averages",
            "description": "Get current average, minimum, and maximum nightly rates aggregated by market (NYC/NJ Metro or Miami). Use for 'what is the average rate in X?' questions about the present moment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "market_param": {
                        "type": "string",
                        "description": "Market region name, e.g. 'Miami' or 'NYC/NJ Metro'. Omit to get all markets.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_snapshot",
            "description": "Get aggregate market statistics (avg/min/max rate, spike count, availability %) for a SPECIFIC HISTORICAL date range. Use when the user gives explicit start/end dates or asks about a past period (e.g. 'how was Miami during World Cup week', 'what was the market like in late July'). NOT for current/live data — use get_market_averages for that.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Market region name, e.g. 'Miami' or 'NYC/NJ Metro'",
                    },
                    "p_start_date": {
                        "type": "string",
                        "description": "Start date, YYYY-MM-DD",
                    },
                    "p_end_date": {
                        "type": "string",
                        "description": "End date, YYYY-MM-DD",
                    },
                },
                "required": ["p_market", "p_start_date", "p_end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_trend",
            "description": "Determine whether a market's average nightly rate has been trending UP, DOWN, or STABLE over a period, and by what percentage. Use for 'is X getting more expensive?', 'is X trending up or down?' style questions. Returns a pre-computed direction — do NOT use get_market_averages for trend questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Market region name, e.g. 'Miami' or 'NYC/NJ Metro'",
                    },
                    "p_days": {
                        "type": "integer",
                        "default": 14,
                        "description": "Period to analyse in days (7–90)",
                    },
                },
                "required": ["market"],
            },
        },
    },
    # ─── SPIKE & ANOMALY DETECTION ───────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_spike_alerts",
            "description": "Fetch properties experiencing market-wide rate spikes or crashes (deviation from 7-day trailing average at or above the threshold). Use for 'which properties spiked this week?', 'any unusual drops?', 'show me volatility alerts' questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "number",
                        "default": 25.0,
                        "description": "Minimum absolute % deviation from 7-day trailing average",
                    },
                    "days": {
                        "type": "integer",
                        "default": 7,
                        "description": "Lookback window in days",
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
            "description": "Investigate a SPECIFIC property's pricing anomalies. Returns pre-computed anomaly events only (significant spikes or crashes) plus a normal rate range summary. Use when the user wants to INVESTIGATE why a single property has a suspicious price, a sudden crash, or an unexplained rate. Do NOT use for general history — use get_property_rate_changes for that.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_property_search": {
                        "type": "string",
                        "description": "Property name or UUID",
                    },
                    "p_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Lookback window in days (max 90)",
                    },
                    "p_deviation_threshold": {
                        "type": "number",
                        "default": 25.0,
                        "description": "Minimum absolute % deviation to qualify as anomaly",
                    },
                },
                "required": ["property_search"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_most_volatile_properties",
            "description": "Rank tracked properties by pricing volatility (frequency and magnitude of rate swings) over a period, optionally filtered to one market. Use for 'which property is most erratic/unstable/unpredictable?' questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "market": {
                        "type": "string",
                        "description": "Optional market filter ('Miami' or 'NYC/NJ Metro')",
                    },
                    "days": {
                        "type": "integer",
                        "default": 14,
                        "description": "Period to analyse in days",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 5,
                        "description": "Number of top results to return (max 10)",
                    },
                },
                "required": [],
            },
        },
    },
    # ─── PROPERTY-SPECIFIC QUERIES ───────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_property_snapshot",
            "description": "Get a single property's CURRENT rate, 7-day trailing average, availability status, and last scrape time in one compact response. Use for 'what is X charging right now?', 'is X available?', 'what is X's current trailing average?' questions. Much lighter than get_property_rate_changes — prefer this for current-state questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_search": {
                        "type": "string",
                        "description": "Property name or UUID",
                    }
                },
                "required": ["property_search"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_property_rate_changes",
            "description": "Show a specific property's historical rate time-series with trailing averages. Use when the user wants to SEE how rates changed over time (exploration/history) — NOT for investigating a suspicious price (use get_rate_anomaly_report) and NOT for current rate (use get_property_snapshot). Supports either a rolling lookback window OR explicit start/end dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_search": {
                        "type": "string",
                        "description": "Property name or UUID (required)",
                    },
                    "days_param": {
                        "type": "integer",
                        "default": 14,
                        "description": "Rolling lookback window in days (max 30). Ignored if start_date/end_date are provided.",
                    },
                    "compare_window_days": {
                        "type": "integer",
                        "default": 1,
                        "description": "Days to compare against for pct_change_vs_prev (max 14)",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Optional explicit start date YYYY-MM-DD. Use instead of days_param when user gives a specific date range.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Optional explicit end date YYYY-MM-DD. Must be provided together with start_date.",
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
            "description": "Compare current rate, trailing average, availability, and volatility side-by-side for 2–5 specific properties. Use for 'compare X and Y', 'how does X stack up against Y and Z?', 'which of these properties is cheapest?' questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2–5 property names or UUIDs to compare side-by-side",
                    }
                },
                "required": ["property_ids"],
            },
        },
    },
    # ─── SEARCH & FILTERING ───────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "search_properties",
            "description": "Search and filter tracked properties by text, market, platform, bedrooms, or availability. Returns compact snapshot rows. Use for 'show me 2-bedroom properties in Miami', 'find available Airbnb listings in NYC', 'search for Brickell properties'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_search": {
                        "type": "string",
                        "description": "Search text (property name or UUID)",
                    },
                    "p_market": {
                        "type": "string",
                        "description": "Market region name (e.g. 'Miami' or 'NYC/NJ Metro')",
                    },
                    "p_platform": {
                        "type": "string",
                        "description": "Platform filter (e.g. 'airbnb' or 'vrbo')",
                    },
                    "p_bedrooms": {
                        "type": "integer",
                        "description": "Number of bedrooms",
                    },
                    "p_available": {
                        "type": "boolean",
                        "description": "true = available only, false = unavailable only",
                    },
                    "p_limit": {
                        "type": "integer",
                        "default": 20,
                        "description": "Results to return (max 50)",
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
            "description": "Get the percentage of tracked listings currently available vs unavailable within the system's 2-night check-in window, optionally filtered by market or platform. IMPORTANT: always caveat that this reflects the 2-night window only, not full-calendar occupancy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_market": {
                        "type": "string",
                        "description": "Optional market filter ('Miami' or 'NYC/NJ Metro')",
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
    # ─── GEO / LOCATION ──────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "geocode_address",
            "description": "Convert a user-supplied street address or general location text into latitude/longitude coordinates. Call this FIRST whenever the user gives an address rather than coordinates, BEFORE calling get_nearby_properties.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Street address, city, or general location text supplied by the user (US locations only)",
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
            "description": "Find tracked properties within a given radius of a latitude/longitude point. Use for 'what properties are near [address]?', 'show me listings around [location]'. If the user gave an address, call geocode_address first to get coordinates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_latitude": {
                        "type": "number",
                        "description": "Latitude of search center",
                    },
                    "p_longitude": {
                        "type": "number",
                        "description": "Longitude of search center",
                    },
                    "p_radius_km": {
                        "type": "number",
                        "default": 5.0,
                        "description": "Search radius in kilometres (max 20)",
                    },
                    "p_limit": {
                        "type": "integer",
                        "default": 10,
                        "description": "Max results to return (max 20)",
                    },
                },
                "required": ["latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_distance_km",
            "description": "Calculate the geographic distance in kilometres between two specific tracked properties using their UUIDs. Use for 'how far apart are property A and property B?' questions when UUIDs are known.",
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
    # ─── TRACKING & SYSTEM META ───────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_tracked_markets",
            "description": "Fetch which markets are actively tracked, optionally filtered by platform. Use for 'what markets do you cover?', 'how many Airbnb properties in each market?' questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_platform": {
                        "type": "string",
                        "description": "Optional platform filter (e.g. 'Airbnb' or 'Vrbo')",
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
            "description": "Get properties recently added to or removed from active tracking. Use for 'has anything changed in what you track recently?', 'what properties were added or dropped?'. Note: removal detection is approximate based on system activity records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "p_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Lookback window in days",
                    }
                },
                "required": [],
            },
        },
    },
    # ─── DATA EXPORT ─────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "generate_data_export",
            "description": "Generate a downloadable report or data export (CSV or Markdown) based on data the user wants to save. Use when the user says 'export', 'download', 'give me a file', or 'save this as a report'. The content should be the full formatted data from prior tool results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "description": "Export format: 'csv' or 'md'",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content or CSV data to export",
                    },
                },
                "required": ["format", "content"],
            },
        },
    },
]
