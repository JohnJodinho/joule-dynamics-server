"""Stream token filtering, markdown sanitization, and history compression utilities."""

from __future__ import annotations

import json
import re
from typing import Iterator

from services.observability import setup_logger

logger = setup_logger(__name__)

MAX_HISTORY_CHARS = 400

_SUPPRESS_TAGS = [
    ("<think>", "</think>"),
    ("<tool_call>", "</tool_call>"),
    ("<|constrain|>", None),
]


def _find_start_tag(buf: str) -> tuple[bool, str, str, str | None]:
    """Finds the earliest occurrence of an internal start tag in the buffer."""
    for start_tag, end_tag in _SUPPRESS_TAGS:
        idx = buf.find(start_tag)
        if idx != -1:
            before = buf[:idx]
            after = buf[idx + len(start_tag):]
            return True, before, after, end_tag
    return False, "", buf, None


def _consume_suppressed(buf: str, suppress_end: str | None) -> tuple[str, bool]:
    """Discards suppressed content until the closing delimiter is encountered."""
    if suppress_end is None:
        return "", True
    end_idx = buf.find(suppress_end)
    if end_idx != -1:
        return buf[end_idx + len(suppress_end):].lstrip("\n\r "), False
    return "", True


def _handle_unsuppressed_buf(buf: str) -> tuple[str, bool, str | None, list[str]]:
    """Evaluates unsuppressed buffer, returning updated buffer and emitted tokens."""
    found, before, after, end_tag = _find_start_tag(buf)
    if found:
        tokens = [before] if before else []
        return after, True, end_tag, tokens

    last_lt = buf.rfind("<")
    if last_lt != -1 and last_lt > len(buf) - 15:
        return buf[last_lt:], False, None, [buf[:last_lt]]
    return "", False, None, [buf]


def _step_buffer(buf: str, suppressing: bool, suppress_end: str | None) -> tuple[str, bool, str | None, list[str], bool]:
    """Processes a single cycle of the streaming buffer."""
    if not suppressing:
        new_buf, new_supp, new_end, tokens = _handle_unsuppressed_buf(buf)
        stop = bool(not new_supp and new_buf)
        return new_buf, new_supp, new_end, tokens, stop

    new_buf, new_supp = _consume_suppressed(buf, suppress_end)
    return new_buf, new_supp, suppress_end, [], new_supp


def strip_internal_tokens(delta_iter: Iterator[str]) -> Iterator[str]:
    """Generator filter consuming raw delta strings and yielding clean user-facing text."""
    buf = ""
    suppressing = False
    suppress_end: str | None = ""

    for delta in delta_iter:
        buf += delta
        while buf:
            buf, suppressing, suppress_end, tokens, stop = _step_buffer(buf, suppressing, suppress_end)
            for token in tokens:
                yield token
            if stop:
                break

    if buf and not suppressing:
        yield buf


def split_tokens(text: str, chunk_size: int = 4) -> Iterator[str]:
    """Split text into small word chunks to simulate streaming for non-stream calls."""
    words = text.split(" ")
    buf: list[str] = []
    for word in words:
        buf.append(word)
        if len(buf) >= chunk_size:
            yield " ".join(buf) + " "
            buf = []
    if buf:
        yield " ".join(buf)


def extract_clarification_options(text: str) -> list[str]:
    """Extract interactive button chip options if the model offered numbered choices."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    options: list[str] = []
    for line in lines:
        match = re.match(r"^(?:(?:\d+\.|\*|-)\s+)?\*{0,2}([\w\s]{3,40}?)\*{0,2}(?::|\s-\s|\?|$)", line)
        if match:
            candidate = match.group(1).strip()
            if candidate and 2 < len(candidate) <= 35:
                if not candidate.lower().startswith(("here are", "would you", "option", "i can")):
                    options.append(candidate)
    if 2 <= len(options) <= 4:
        return options
    return []


def compress_for_history(text: str) -> str:
    """Trim assistant response to first 400 chars to save tokens in multi-turn history."""
    clean = re.sub(r"\[.*?\]\(.*?\)", "", text)
    clean = re.sub(r"[*_`#]", "", clean)
    clean = " ".join(clean.split())
    if len(clean) > MAX_HISTORY_CHARS:
        return clean[:MAX_HISTORY_CHARS].rsplit(" ", 1)[0] + "..."
    return clean


def clean_reply_text(text: str) -> str:
    """Removes leaked action chips and trailing whitespace from the final response text."""
    clean = re.sub(
        r"\[(ACTION_CHIPS|QUICK_ACTIONS|SUGGESTED_ACTIONS):.*?\]",
        "",
        text,
        flags=re.DOTALL,
    )
    return clean.strip()


def strip_internal_model_markers(text: str) -> str:
    """Strips internal model reasoning and tool-call markers from final text."""
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    clean = re.sub(r"<tool_call>.*?</tool_call>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<\|.*?\|>", "", clean)
    return clean.strip()
