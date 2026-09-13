"""
Comprehensive test suite testing all 22 LLM tool calls through run_agent_loop
and run_agent_loop_streaming with simulated LLM retrieval to consume zero tokens.
"""

import sys
import os
import json
import asyncio
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.agent_loop import run_agent_loop, run_agent_loop_streaming
from services.tool_schemas import REAL_ESTATE_TOOLS, SUGGEST_ACTIONS_TOOL

TEST_TOOL_PAYLOADS = [
    ("get_real_estate_kpis", {"p_market": "Abuja"}),
    ("get_market_averages", {"market_param": "Abuja"}),
    ("get_market_snapshot", {"p_market": "Abuja", "p_start_date": "2026-03-01", "p_end_date": "2026-03-07"}),
    ("get_market_trend", {"p_market": "Abuja", "p_days": 14}),
    ("get_market_rate_changes", {"p_market": "Abuja", "p_days": 7, "p_limit": 5}),
    ("get_spike_alerts", {"p_market": "Abuja", "days_param": 7, "threshold_param": 25.0}),
    ("get_rate_anomaly_report", {"p_property_search": "c37b5e7f-fe43-4306-b28a-34f0f12cff74", "p_days": 30}),
    ("get_most_volatile_properties", {"p_market": "Abuja", "p_days": 14, "p_limit": 5}),
    ("get_property_snapshot", {"p_property_search": "c37b5e7f-fe43-4306-b28a-34f0f12cff74"}),
    ("get_property_detail", {"p_property_search": "c37b5e7f-fe43-4306-b28a-34f0f12cff74", "p_history_days": 14}),
    ("get_property_rate_changes", {"property_search": "c37b5e7f-fe43-4306-b28a-34f0f12cff74", "days_param": 14}),
    ("compare_properties", {"p_property_ids": ["c37b5e7f-fe43-4306-b28a-34f0f12cff74", "6d161e69-9fbd-4772-ac48-e7f1ff9388aa"]}),
    ("search_properties", {"p_market": "Abuja", "p_limit": 5}),
    ("get_availability_rate", {"p_market": "Abuja"}),
    ("geocode_address", {"address": "Maitama, Abuja"}),
    ("get_nearby_properties", {"p_latitude": 9.0765, "p_longitude": 7.3986, "p_radius_km": 5.0}),
    ("get_distance_km", {"property_a_id": "c37b5e7f-fe43-4306-b28a-34f0f12cff74", "property_b_id": "6d161e69-9fbd-4772-ac48-e7f1ff9388aa"}),
    ("get_tracked_markets", {}),
    ("get_recently_changed_tracking", {"p_days": 30}),
    ("generate_data_export", {"content": "# Test Market Report\n\nAbuja Rate Analysis"}),
    ("suggest_actions", {"actions": ["Explore Abuja", "Compare Listings"]}),
    ("generate_contact_buttons", {"message": "I would like to enquire about bespoke tracking."}),
]


class MockFunction:
    """Mock container for function name and serialized arguments."""

    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class MockToolCall:
    """Mock container for a tool call matching the Groq ChatCompletionMessage structure."""

    def __init__(self, call_id: str, name: str, arguments: dict):
        self.id = call_id
        self.function = MockFunction(name, json.dumps(arguments))


class MockAssistantMessage:
    """Mock assistant message returning specified tool calls."""

    def __init__(self, tool_calls: list):
        self.role = "assistant"
        self.content = ""
        self.tool_calls = tool_calls


async def mock_stream_synthesis(synth_messages: list, loop):
    """Simulated async token stream replacing LLM synthesis without network calls."""
    yield {"type": "token", "token": "Simulated token stream."}


def _assert_tool_message(messages: list, tool_name: str) -> str:
    """Validates that a tool message was added and returned non-error content."""
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1, f"Expected 1 tool message in conversation, got {len(tool_msgs)}"
    content = tool_msgs[0].get("content", "")
    assert content, "Tool response content was empty"
    assert not content.startswith(f"Tool '{tool_name}' error:"), f"Tool execution failed: {content}"
    return content


def _assert_stream_events(events: list, tool_name: str):
    """Validates presence of tool_call, token, and done events in the stream."""
    event_types = {e.get("type") for e in events}
    assert "tool_call" in event_types, f"Missing tool_call event in {event_types}"
    assert "token" in event_types, f"Missing token event in {event_types}"
    assert "done" in event_types, f"Missing done event in {event_types}"

    tool_call_events = [e for e in events if e.get("type") == "tool_call"]
    assert tool_call_events[0]["tool"] == tool_name, f"Expected {tool_name}, got {tool_call_events[0]['tool']}"


async def test_tool_non_streaming(tool_name: str, tool_args: dict) -> dict:
    """Tests a single tool execution within run_agent_loop with mocked LLM calls."""
    call_count = 0

    async def mock_retrieval(messages, active_tools, round_num, tool_results):
        nonlocal call_count
        call_count += 1
        return MockAssistantMessage([MockToolCall("call_test_123", tool_name, tool_args)]) if call_count == 1 else MockAssistantMessage([])

    messages = [{"role": "user", "content": f"Test prompt for {tool_name}"}]
    tool_results = []
    suggested_actions = []

    with patch("services.agent_loop._call_retrieval_model", side_effect=mock_retrieval), \
         patch("services.agent_loop._synthesize_markdown", new_callable=AsyncMock) as mock_synth, \
         patch("services.agent_loop.resolve_suggested_actions", new_callable=AsyncMock) as mock_actions:

        mock_synth.return_value = "Simulated synthesis response."
        mock_actions.return_value = ["Action 1", "Action 2"]

        reply = await run_agent_loop(
            messages=messages,
            active_tools=REAL_ESTATE_TOOLS + [SUGGEST_ACTIONS_TOOL],
            tool_results=tool_results,
            user_query=f"Test {tool_name}",
            suggested_actions_out=suggested_actions,
        )

        assert reply == "Simulated synthesis response.", f"Unexpected reply: {reply}"
        assert len(tool_results) == 1, f"Expected 1 tool result, got {len(tool_results)}"
        assert tool_results[0]["tool"] == tool_name, f"Expected {tool_name}, got {tool_results[0]['tool']}"
        content = _assert_tool_message(messages, tool_name)

        return {
            "status": "PASS",
            "mode": "non-streaming",
            "tool": tool_name,
            "preview": content[:60].replace("\n", " "),
        }


async def test_tool_streaming(tool_name: str, tool_args: dict) -> dict:
    """Tests a single tool execution within run_agent_loop_streaming with mocked LLM calls."""
    call_count = 0

    async def mock_retrieval(messages, active_tools, round_num, tool_results):
        nonlocal call_count
        call_count += 1
        return MockAssistantMessage([MockToolCall("call_stream_123", tool_name, tool_args)]) if call_count == 1 else MockAssistantMessage([])

    messages = [{"role": "user", "content": f"Test stream prompt for {tool_name}"}]
    tool_results = []
    events = []

    with patch("services.agent_loop._call_retrieval_model", side_effect=mock_retrieval), \
         patch("services.agent_loop._stream_synthesis", side_effect=mock_stream_synthesis), \
         patch("services.agent_loop.resolve_suggested_actions", new_callable=AsyncMock) as mock_actions:

        mock_actions.return_value = ["Action 1", "Action 2"]

        async for event in run_agent_loop_streaming(
            messages=messages,
            active_tools=REAL_ESTATE_TOOLS + [SUGGEST_ACTIONS_TOOL],
            tool_results=tool_results,
            user_query=f"Test stream {tool_name}",
        ):
            events.append(event)

        _assert_stream_events(events, tool_name)
        _assert_tool_message(messages, tool_name)

        return {
            "status": "PASS",
            "mode": "streaming",
            "tool": tool_name,
            "events_count": len(events),
        }


async def _run_single_case(idx: int, tool_name: str, tool_args: dict) -> tuple[int, list]:
    """Runs non-streaming and streaming test for one tool payload."""
    passes = 0
    failures = []

    try:
        res_ns = await test_tool_non_streaming(tool_name, tool_args)
        print(f"[{idx:02d}/22] [SYNC]  PASS: {tool_name.ljust(32)} -> {res_ns['preview']}")
        passes += 1
    except Exception as exc:
        print(f"[{idx:02d}/22] [SYNC]  FAIL: {tool_name.ljust(32)} -> {exc}")
        failures.append((tool_name, "non-streaming", str(exc)))

    try:
        res_s = await test_tool_streaming(tool_name, tool_args)
        print(f"[{idx:02d}/22] [STREAM] PASS: {tool_name.ljust(32)} -> {res_s['events_count']} events yielded")
        passes += 1
    except Exception as exc:
        print(f"[{idx:02d}/22] [STREAM] FAIL: {tool_name.ljust(32)} -> {exc}")
        failures.append((tool_name, "streaming", str(exc)))

    return passes, failures


async def run_all_tests():
    """Runs tests for all 22 tools across non-streaming and streaming loops."""
    print("=" * 80)
    print("RUNNING SIMULATED LLM TOOL CALL TEST SUITE (0 API TOKENS CONSUMED)")
    print(f"Total tools to test: {len(TEST_TOOL_PAYLOADS)} (x 2 modes = {len(TEST_TOOL_PAYLOADS) * 2} executions)")
    print("=" * 80)

    total_passed = 0
    all_failures = []

    for idx, (tool_name, tool_args) in enumerate(TEST_TOOL_PAYLOADS, 1):
        p, f = await _run_single_case(idx, tool_name, tool_args)
        total_passed += p
        all_failures.extend(f)

    print("=" * 80)
    print(f"RESULTS: {total_passed} PASSED, {len(all_failures)} FAILED")
    print("=" * 80)

    if all_failures:
        for fn, mode, err in all_failures:
            print(f"  - {fn} ({mode}): {err}")
        sys.exit(1)

    print("ALL 22 TOOLS PASSED PERFECTLY IN BOTH NON-STREAMING AND STREAMING MODES!")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
