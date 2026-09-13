"""
services/tool_executor.py
──────────────────────────
Tool dispatch layer — executes tool calls and normalizes assistant messages.
"""

import asyncio
import json
import re
import urllib.parse
from services.observability import setup_logger
from services.supabase_service import execute_tool_rpc
from services.tool_compressor import compress_tool_output, geocode_address_handler
from config import CONTACT_EMAIL, CONTACT_WHATSAPP

logger = setup_logger(__name__)


def normalize_assistant_message(msg) -> dict:
    """Converts a ChatCompletionMessage SDK object to a clean dict, stripping internal fields."""
    if getattr(msg, "tool_calls", None):
        return {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        }
    return {
        "role": "assistant",
        "content": getattr(msg, "content", "") or "",
    }


def parse_tool_args(raw_arguments) -> dict:
    """Safely parse tool call arguments — handles both str and dict."""
    if isinstance(raw_arguments, str):
        try:
            return json.loads(raw_arguments)
        except Exception:
            return {}
    return raw_arguments or {}


def _suggest_actions_handler(func_args: dict) -> dict:
    """Handles suggest_actions tool by registering action chips."""
    actions = func_args.get("actions") or func_args.get("options") or []
    return {
        "status": "success",
        "message": (
            "Interactive action button chips have been registered and will be rendered automatically "
            "by the UI below the message. Do NOT write markdown links, dummy URLs, or repetitive link lists in your text response."
        ),
        "actions": actions,
    }


def _contact_buttons_handler(func_args: dict) -> dict:
    """Generates markdown contact buttons for email and WhatsApp."""
    raw_message = func_args.get("message", "Hi, I'd like to discuss a custom build.")
    encoded_message = urllib.parse.quote(raw_message)
    return {
        "status": "success",
        "email_button_markdown": f"[Get in touch via Email](mailto:{CONTACT_EMAIL})",
        "whatsapp_button_markdown": f"[Chat on WhatsApp](https://wa.me/{CONTACT_WHATSAPP}?text={encoded_message})",
    }


def _geocode_handler(func_args: dict) -> dict:
    """Forwards geocode requests to the geocode_address_handler."""
    return geocode_address_handler(func_args.get("address", ""))


async def _data_export_handler(func_args: dict) -> dict:
    """Uploads export markdown to Appwrite storage."""
    from services.appwrite_service import upload_document_to_appwrite
    content = func_args.get("content", "")
    return await upload_document_to_appwrite(content, "md")


_LOCAL_HANDLERS = {
    "suggest_actions": _suggest_actions_handler,
    "generate_contact_buttons": _contact_buttons_handler,
    "geocode_address": _geocode_handler,
    "generate_data_export": _data_export_handler,
}


async def execute_tool_by_name(func_name: str, func_args: dict) -> dict:
    """Dispatches a tool call by name, routing to local handlers or Supabase RPC."""
    handler = _LOCAL_HANDLERS.get(func_name)
    if handler:
        if asyncio.iscoroutinefunction(handler):
            return await handler(func_args)
        return handler(func_args)
    return await execute_tool_rpc(func_name, func_args)


def _parse_json_direct(failed_gen: str) -> tuple[str | None, dict | None]:
    """Attempts direct JSON parse of failed generation payload."""
    try:
        data = json.loads(failed_gen)
        if isinstance(data, dict) and "name" in data:
            args = data.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    pass
            return data["name"], args if isinstance(args, dict) else {}
    except Exception:
        pass
    return None, None


def _parse_json_regex(failed_gen: str) -> tuple[str | None, dict | None]:
    """Attempts regex extraction of JSON name/arguments dictionary."""
    pattern = r'\{\s*"name"\s*:\s*"([a-zA-Z0-9_]+)"\s*,\s*"arguments"\s*:\s*(\{.*?\})\s*\}'
    match = re.search(pattern, failed_gen, re.DOTALL)
    if match:
        try:
            return match.group(1), json.loads(match.group(2))
        except Exception:
            pass
    return None, None


def _parse_function_tag(failed_gen: str) -> tuple[str | None, dict | None]:
    """Attempts regex extraction of legacy <function=name>{args} tag format."""
    pattern = r"<function=([a-zA-Z0-9_]+)[>\s]*(\{.*?\})"
    match = re.search(pattern, failed_gen, re.DOTALL)
    if match:
        try:
            return match.group(1), json.loads(match.group(2))
        except Exception:
            pass
    return None, None


def parse_failed_generation(failed_gen: str) -> tuple[str | None, dict | None]:
    """Extracts tool name and arguments from a Groq 400 failed_generation string."""
    if not failed_gen:
        return None, None

    for parser in (_parse_json_direct, _parse_json_regex, _parse_function_tag):
        name, args = parser(failed_gen)
        if name is not None:
            return name, args

    return None, None
