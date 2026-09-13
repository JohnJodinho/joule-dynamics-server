"""Universal and commercial tool schemas (actions, export, contact buttons)."""

SUGGEST_ACTIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "suggest_actions",
        "description": (
            "Suggest 0 to 4 relevant interactive action buttons or clarifying options for the user based on the conversation. "
            "Return an empty array actions: [] if no follow-up action is genuinely useful or if the conversation has concluded naturally - do not force suggestions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of 0 to 4 short, action-oriented button labels (max 35 chars each). Empty list [] if no follow-up is needed."
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
        "description": "Generate a downloadable file export (Markdown document or CSV report) containing real estate analysis or property tables requested by the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The complete markdown or CSV text content to export into a downloadable file."
                }
            },
            "required": ["content"]
        }
    }
}

GENERATE_CONTACT_BUTTONS_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_contact_buttons",
        "description": "Generate interactive contact buttons (Email and WhatsApp) for custom engineering inquiries, bespoke scraping needs, or system requests.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "A personalized, plain English greeting message to pre-fill in the WhatsApp chat based on the user's inquiry."
                }
            },
            "required": ["message"],
        },
    }
}

COMMERCIAL_TOOLS = [
    GENERATE_CONTACT_BUTTONS_TOOL,
    SUGGEST_ACTIONS_TOOL,
]

__all__ = [
    "SUGGEST_ACTIONS_TOOL",
    "GENERATE_DATA_EXPORT_TOOL",
    "GENERATE_CONTACT_BUTTONS_TOOL",
    "COMMERCIAL_TOOLS",
]
