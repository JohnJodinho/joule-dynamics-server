"""
services/tool_executor.py
──────────────────────────
Tool dispatch layer — executes tool calls and normalizes assistant messages.

Architectural fixes:
  #2 - Universal tool recovery: any tool in failed_generation is executed,
       not just generate_data_export.
  #5 - normalize_assistant_message() strips the internal `reasoning` field
       and converts ChatCompletionMessage objects to clean dicts so they
       never contaminate the conversation context.
"""

import json
import urllib.parse
from services.observability import setup_logger
from services.supabase_service import execute_tool_rpc
from services.tool_compressor import compress_tool_output, geocode_address_handler
from config import CONTACT_EMAIL, CONTACT_WHATSAPP

logger = setup_logger(__name__)


# ─── MESSAGE NORMALIZER ───────────────────────────────────────────────────────

def normalize_assistant_message(msg) -> dict:
    """
    Convert a ChatCompletionMessage SDK object to a clean dict.

    Strips internal fields: `reasoning`, `logprobs`, `function_call`, etc.
    These fields must NEVER appear in the messages[] sent to subsequent
    LLM calls — they inflate token usage and contaminate context.

    Fix #5: reasoning field was leaking into history via Python __repr__.
    """
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


# ─── TOOL EXECUTOR ────────────────────────────────────────────────────────────

async def execute_tool_by_name(func_name: str, func_args: dict) -> dict:
    """
    Dispatch a tool call by name. Returns a result dict with {"status": ...}.

    Python-side tools (generate_contact_buttons, geocode_address,
    generate_data_export) are handled here. All other tool names are forwarded
    to the Supabase RPC layer.
    """
    if func_name == "suggest_actions":
        actions = func_args.get("actions") or func_args.get("options") or []
        return {
            "status": "success",
            "message": "Interactive action buttons registered for user.",
            "actions": actions,
        }

    if func_name == "generate_contact_buttons":
        raw_message = func_args.get("message", "Hi, I'd like to discuss a custom build.")
        encoded_message = urllib.parse.quote(raw_message)
        return {
            "status": "success",
            "email_button_markdown": f"[Get in touch via Email](mailto:{CONTACT_EMAIL})",
            "whatsapp_button_markdown": f"[Chat on WhatsApp](https://wa.me/{CONTACT_WHATSAPP}?text={encoded_message})",
        }

    if func_name == "geocode_address":
        return geocode_address_handler(func_args.get("address", ""))

    if func_name == "generate_data_export":
        from services.appwrite_service import upload_document_to_appwrite
        content = func_args.get("content", "")
        return await upload_document_to_appwrite(content, "md")

    # Default: Supabase RPC
    return await execute_tool_rpc(func_name, func_args)


def parse_tool_args(raw_arguments) -> dict:
    """Safely parse tool call arguments — handles both str and dict."""
    if isinstance(raw_arguments, str):
        try:
            return json.loads(raw_arguments)
        except Exception:
            return {}
    return raw_arguments or {}


def parse_failed_generation(failed_gen: str) -> tuple[str | None, dict | None]:
    """
    Extract tool name and arguments from a Groq 400 failed_generation string.

    The model may format the failed call in two ways:
      - {"name": "func", "arguments": {...}}  (JSON dict)
      - <function=func_name>{...}             (legacy template format)

    Fix #2: Previously only generate_data_export was handled. Now all tools
    recovered from failed_generation are dispatched through execute_tool_by_name.
    """
    import re

    if not failed_gen:
        return None, None

    # Strategy 1: Direct JSON parse
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

    # Strategy 2: Regex for {"name": "...", "arguments": {...}}
    match = re.search(
        r'\{\s*"name"\s*:\s*"([a-zA-Z0-9_]+)"\s*,\s*"arguments"\s*:\s*(\{.*?\})\s*\}',
        failed_gen,
        re.DOTALL,
    )
    if match:
        try:
            return match.group(1), json.loads(match.group(2))
        except Exception:
            pass

    # Strategy 3: Regex for <function=name>{args}
    match = re.search(r"<function=([a-zA-Z0-9_]+)[>\s]*(\{.*?\})", failed_gen, re.DOTALL)
    if match:
        try:
            return match.group(1), json.loads(match.group(2))
        except Exception:
            pass

    return None, None
