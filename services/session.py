"""
services/session.py
────────────────────
In-memory session history store with write-time trimming and TTL eviction.

Architectural fix #6 - Session history was a plain defaultdict(list) that
grew without bound. Now:
  - Entries are trimmed at write time (never grow beyond MAX_HISTORY_MESSAGES).
  - Entries older than SESSION_TTL_SECONDS are evicted on next access.
"""

import time
from services.observability import setup_logger

logger = setup_logger(__name__)

MAX_HISTORY_MESSAGES = 10   # stored per session (user + assistant turns)
SESSION_TTL_SECONDS  = 3600 # 1 hour of inactivity = session cleared

# Internal store: {session_id: {"messages": [...], "ts": float}}
_sessions: dict[str, dict] = {}


def get_history(session_id: str) -> list[dict]:
    """Return mutable message list for this session, creating if needed."""
    now = time.time()
    entry = _sessions.get(session_id)
    if entry is None or (now - entry["ts"]) > SESSION_TTL_SECONDS:
        _sessions[session_id] = {"messages": [], "ts": now}
    else:
        _sessions[session_id]["ts"] = now
    return _sessions[session_id]["messages"]


def append_message(session_id: str, message: dict) -> None:
    """Append a message and immediately trim if over limit (fix #6)."""
    history = get_history(session_id)
    history.append(message)
    if len(history) > MAX_HISTORY_MESSAGES:
        _sessions[session_id]["messages"] = history[-MAX_HISTORY_MESSAGES:]


def get_context_window(session_id: str, limit: int = 6) -> list[dict]:
    """Return the last `limit` messages for injection into an LLM context."""
    return list(get_history(session_id)[-limit:])


def clear_session(session_id: str) -> None:
    """Explicitly clear a session (e.g. user logout)."""
    _sessions.pop(session_id, None)


def active_session_count() -> int:
    """Returns number of active (non-expired) sessions, for monitoring."""
    now = time.time()
    return sum(
        1 for e in _sessions.values()
        if (now - e["ts"]) <= SESSION_TTL_SECONDS
    )
