"""
services/groq_service.py
─────────────────────────
Public facade — re-exports the two main entry points that external code imports.

This file is intentionally thin. All implementation lives in the split modules:
  groq_client.py     - Groq SDK + retry wrapper
  prompts.py         - Prompt strings + composition by classification
  session.py         - Session history with TTL eviction
  tool_compressor.py - compress_tool_output + geocode_address_handler
  tool_executor.py   - Tool dispatch + message normalization
  agent_loop.py      - Agentic loop, router, orchestration, SSE streaming

Imports from external code should use this facade for stability:
  from services.groq_service import process_chat_message, stream_chat_message
"""

from services.agent_loop import process_chat_message, stream_chat_message

__all__ = ["process_chat_message", "stream_chat_message"]
