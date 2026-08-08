REAL_ESTATE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_kpis",
            "description": "Fetch overall Real Estate Rate Monitor KPIs including tracked properties count, 7D rate changes, 25%+ spikes, and scrape health.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_averages",
            "description": "Get average, minimum, and maximum nightly rates aggregated by regional market (e.g., 'NYC/NJ Metro' or 'Miami').",
            "parameters": {
                "type": "object",
                "properties": {
                    "market": {"type": "string", "description": "Market region name, e.g. 'Miami' or 'NYC/NJ Metro'"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_spike_alerts",
            "description": "Fetch properties experiencing rate volatility spikes or drops equal to or exceeding a percentage deviation threshold from their 7-day average.",
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold": {"type": "number", "default": 25.0, "description": "Minimum absolute percentage deviation from 7-day trailing average"},
                    "days": {"type": "integer", "default": 7, "description": "Lookback window in days"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_property_rate_history",
            "description": "Retrieve historical nightly rate time series and 7-day trailing average benchmark for a specific property.",
            "parameters": {
                "type": "object",
                "properties": {
                    "property_search": {"type": "string", "description": "Property name or UUID"},
                    "days": {"type": "integer", "default": 30}
                },
                "required": ["property_search"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_properties_by_filter",
            "description": "Search and filter the property rate snapshot table by market, platform, availability, or bedroom count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "market": {"type": "string", "description": "e.g., 'Miami' or 'NYC/NJ Metro'"},
                    "platform": {"type": "string", "description": "e.g., 'airbnb'"},
                    "available": {"type": "boolean", "description": "true for available now, false for unavailable"},
                    "bedrooms": {"type": "integer", "description": "Number of bedrooms"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_data_export",
            "description": "Generate a downloadable report or data export (CSV or Markdown) based on data and metrics the user wants to keep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {"type": "string", "description": "Format of the export: 'csv' or 'md'"},
                    "content": {"type": "string", "description": "The full text content or CSV data to be exported into the file"}
                },
                "required": ["format", "content"]
            }
        }
    }
]
