"""Tool schema for the create_alert_subscription LLM tool."""

CREATE_ALERT_SUBSCRIPTION_TOOL = {
    "type": "function",
    "function": {
        "name": "create_alert_subscription",
        "description": (
            "Create a PENDING alert subscription. Never call until you have: "
            "condition type, target (market or property), and email address. "
            "Does not activate the alert — confirmation happens via email link."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "User's email address for receiving alerts",
                },
                "criteria_type": {
                    "type": "string",
                    "enum": [
                        "spike", "rate_change", "price_threshold",
                        "availability_change", "new_listing", "tracking_removed",
                        "anomaly", "volatility", "trend_reversal", "digest",
                    ],
                    "description": "Type of condition to monitor",
                },
                "criteria": {
                    "type": "object",
                    "description": (
                        "Type-specific parameters. Examples: "
                        "spike: {market, threshold_pct}; "
                        "rate_change: {market, direction: both|up|down}; "
                        "price_threshold: {property_search, operator: above|below, value}; "
                        "availability_change: {property_search, watch_for: available|unavailable|any}; "
                        "new_listing/tracking_removed: {market}; "
                        "anomaly: {property_search, deviation_threshold}; "
                        "volatility: {market, top_n}; "
                        "trend_reversal: {market, watch_for: rising|falling}; "
                        "digest: {market, frequency: daily|weekly}"
                    ),
                },
                "raw_request_text": {
                    "type": "string",
                    "description": "Original user request verbatim",
                },
            },
            "required": ["email", "criteria_type", "criteria", "raw_request_text"],
        },
    },
}
