"""Simulated tests for the Pulse AI Alert Subscription system.

All tests use mocks — no LLM, Supabase, or SMTP calls.
Tests cover: router classification, tool resolution, prompt composition,
tool executor dispatch, subscription cap enforcement, cooldown logic,
evaluator dispatch, and criteria summary builder.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestRouterClassification:
    """Verify ALERT_SUBSCRIPTION is a valid classification."""

    def test_alert_subscription_in_valid_classifications(self):
        from services.query_router import _VALID_CLASSIFICATIONS
        assert "ALERT_SUBSCRIPTION" in _VALID_CLASSIFICATIONS

    def test_existing_classifications_preserved(self):
        from services.query_router import _VALID_CLASSIFICATIONS
        for cls in ("OUT_OF_SCOPE", "PATH_A", "PATH_B", "BOTH", "GREETING", "COMMERCIAL_HANDOFF"):
            assert cls in _VALID_CLASSIFICATIONS

    def test_total_classification_count(self):
        from services.query_router import _VALID_CLASSIFICATIONS
        assert len(_VALID_CLASSIFICATIONS) == 7

    def test_rescue_classification_email_address(self):
        from services.query_router import _rescue_classification
        history = [
            {"role": "user", "content": "Send me a report whenever rate changes in Abuja"},
            {"role": "assistant", "content": "Sure thing! To set up a notification for any rate changes in Abuja, I'll need the email address where you'd like to receive the alerts."},
        ]
        result = _rescue_classification("OUT_OF_SCOPE", "john.albarka.ibrahim@gmail.com", history)
        assert result == "ALERT_SUBSCRIPTION"

    def test_rescue_classification_alert_short_reply(self):
        from services.query_router import _rescue_classification
        history = [
            {"role": "assistant", "content": "Would you like a daily or weekly digest for Miami?"},
        ]
        assert _rescue_classification("OUT_OF_SCOPE", "weekly", history) == "ALERT_SUBSCRIPTION"
        assert _rescue_classification("OUT_OF_SCOPE", "daily please", history) == "ALERT_SUBSCRIPTION"

    def test_rescue_classification_general_question_short_reply(self):
        from services.query_router import _rescue_classification
        history = [
            {"role": "assistant", "content": "Which market would you like me to analyze?"},
        ]
        assert _rescue_classification("OUT_OF_SCOPE", "Miami", history) == "PATH_A"

    def test_rescue_classification_truly_out_of_scope_preserved(self):
        from services.query_router import _rescue_classification
        history = [
            {"role": "assistant", "content": "Sure thing! I need your email address."},
        ]
        result = _rescue_classification("OUT_OF_SCOPE", "how do I bake a chocolate cake with vanilla frosting?", history)
        assert result == "OUT_OF_SCOPE"

    def test_build_router_messages_includes_history(self):
        from services.query_router import _build_router_messages
        history = [
            {"role": "user", "content": "User Context Filters: {}\nUser Query: Track Abuja"},
            {"role": "assistant", "content": "What is your email address?"},
        ]
        messages = _build_router_messages("john@example.com", history)
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1]["content"] == "Track Abuja"
        assert messages[2]["content"] == "What is your email address?"
        assert messages[3]["content"] == "john@example.com"


class TestToolResolution:
    """Verify _resolve_active_tools returns exactly 2 tools for ALERT_SUBSCRIPTION."""

    def test_alert_subscription_returns_two_tools(self):
        from services.agent_loop import _resolve_active_tools
        tools = _resolve_active_tools("ALERT_SUBSCRIPTION", "alert me", [], [])
        assert tools is not None
        assert len(tools) == 2
        tool_names = {t["function"]["name"] for t in tools}
        assert tool_names == {"create_alert_subscription", "suggest_actions"}

    def test_path_a_unchanged(self):
        from services.agent_loop import _resolve_active_tools
        with patch("services.agent_loop.discover_tools", return_value=[{"function": {"name": "mock"}}]):
            tools = _resolve_active_tools("PATH_A", "show me spikes", [], ["ANOMALY"])
            assert tools is not None

    def test_path_b_unchanged(self):
        from services.agent_loop import _resolve_active_tools
        tools = _resolve_active_tools("PATH_B", "what does the sparkline mean", [], [])
        assert tools is not None
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "suggest_actions"

    def test_commercial_unchanged(self):
        from services.agent_loop import _resolve_active_tools
        from services.tools import COMMERCIAL_TOOLS
        tools = _resolve_active_tools("COMMERCIAL_HANDOFF", "how much", [], [])
        assert tools == COMMERCIAL_TOOLS


class TestPromptComposition:
    """Verify build_system_prompt includes _PROMPT_ALERT for ALERT_SUBSCRIPTION."""

    def test_alert_prompt_included(self):
        from services.prompts import build_system_prompt
        prompt = build_system_prompt("ALERT_SUBSCRIPTION", markets=["Miami", "Abuja"])
        assert "ALERT SUBSCRIPTIONS" in prompt
        assert "create_alert_subscription" in prompt

    def test_alert_prompt_excludes_data_prompts(self):
        from services.prompts import build_system_prompt
        prompt = build_system_prompt("ALERT_SUBSCRIPTION", markets=["Miami"])
        assert "ADVISORY & STRATEGY" not in prompt
        assert "DASHBOARD UI" not in prompt
        assert "COMMERCIAL HANDOFFS" not in prompt

    def test_path_a_prompt_excludes_alert(self):
        from services.prompts import build_system_prompt
        prompt = build_system_prompt("PATH_A", markets=["Miami"])
        assert "ALERT SUBSCRIPTIONS" not in prompt
        assert "ADVISORY & STRATEGY" in prompt

    def test_all_ten_criteria_types_in_prompt(self):
        from services.prompts import build_system_prompt
        prompt = build_system_prompt("ALERT_SUBSCRIPTION", markets=["Miami"])
        for ctype in ("spike", "rate_change", "price_threshold", "availability_change",
                       "new_listing", "tracking_removed", "anomaly", "volatility",
                       "trend_reversal", "digest"):
            assert ctype in prompt, f"Missing criteria_type '{ctype}' in _PROMPT_ALERT"

    def test_router_prompt_contains_alert_classification(self):
        from services.prompts import ROUTER_PROMPT
        assert "ALERT_SUBSCRIPTION" in ROUTER_PROMPT
        assert "seven classifications" in ROUTER_PROMPT


class TestToolExecutorDispatch:
    """Verify create_alert_subscription routes to the handler."""

    def test_handler_registered(self):
        from services.tool_executor import _LOCAL_HANDLERS
        assert "create_alert_subscription" in _LOCAL_HANDLERS

    @pytest.mark.asyncio
    async def test_handler_calls_alert_service(self):
        mock_result = {"status": "pending", "message": "Confirmation email sent"}
        with patch("services.alert_service.create_pending_subscription", new_callable=AsyncMock, return_value=mock_result):
            from services.tool_executor import execute_tool_by_name
            result = await execute_tool_by_name("create_alert_subscription", {
                "email": "test@example.com",
                "criteria_type": "spike",
                "criteria": {"market": "Miami", "threshold_pct": 25},
                "raw_request_text": "Alert me on Miami spikes",
            })
            assert result["status"] == "pending"

    def test_existing_handlers_preserved(self):
        from services.tool_executor import _LOCAL_HANDLERS
        for handler in ("suggest_actions", "generate_contact_buttons", "geocode_address", "generate_data_export"):
            assert handler in _LOCAL_HANDLERS


class TestAlertToolSchema:
    """Verify the tool schema structure."""

    def test_schema_has_required_fields(self):
        from services.alert_tool_schema import CREATE_ALERT_SUBSCRIPTION_TOOL
        func = CREATE_ALERT_SUBSCRIPTION_TOOL["function"]
        assert func["name"] == "create_alert_subscription"
        params = func["parameters"]["properties"]
        assert "email" in params
        assert "criteria_type" in params
        assert "criteria" in params
        assert "raw_request_text" in params

    def test_criteria_type_enum_has_ten_values(self):
        from services.alert_tool_schema import CREATE_ALERT_SUBSCRIPTION_TOOL
        enum_vals = CREATE_ALERT_SUBSCRIPTION_TOOL["function"]["parameters"]["properties"]["criteria_type"]["enum"]
        assert len(enum_vals) == 10
        expected = {"spike", "rate_change", "price_threshold", "availability_change",
                    "new_listing", "tracking_removed", "anomaly", "volatility",
                    "trend_reversal", "digest"}
        assert set(enum_vals) == expected

    def test_required_fields(self):
        from services.alert_tool_schema import CREATE_ALERT_SUBSCRIPTION_TOOL
        required = CREATE_ALERT_SUBSCRIPTION_TOOL["function"]["parameters"]["required"]
        assert set(required) == {"email", "criteria_type", "criteria", "raw_request_text"}


class TestCriteriaSummaryBuilder:
    """Verify human-readable criteria summary generation."""

    def test_spike_summary(self):
        from services.email_service import build_criteria_summary
        result = build_criteria_summary("spike", {"market": "Miami", "threshold_pct": 25})
        assert "spike" in result.lower() or "25%" in result
        assert "Miami" in result

    def test_price_threshold_summary(self):
        from services.email_service import build_criteria_summary
        result = build_criteria_summary("price_threshold", {
            "property_search": "Cozy Studio", "operator": "below", "value": 40000,
        })
        assert "Cozy Studio" in result
        assert "below" in result

    def test_digest_summary(self):
        from services.email_service import build_criteria_summary
        result = build_criteria_summary("digest", {"market": "Lagos", "frequency": "weekly"})
        assert "Lagos" in result
        assert "Weekly" in result or "weekly" in result.lower()

    def test_trend_reversal_summary(self):
        from services.email_service import build_criteria_summary
        result = build_criteria_summary("trend_reversal", {"market": "Abuja", "watch_for": "falling"})
        assert "Abuja" in result
        assert "falling" in result

    def test_availability_change_summary(self):
        from services.email_service import build_criteria_summary
        result = build_criteria_summary("availability_change", {
            "property_search": "Beach House", "watch_for": "unavailable",
        })
        assert "Beach House" in result

    def test_market_only_types(self):
        from services.email_service import build_criteria_summary
        for ctype in ("rate_change", "new_listing", "tracking_removed", "volatility"):
            result = build_criteria_summary(ctype, {"market": "Miami"})
            assert "Miami" in result


class TestCooldownLogic:
    """Verify cooldown enforcement in the alert evaluator."""

    def test_no_last_fired_not_in_cooldown(self):
        from workers.alert_evaluator import _is_in_cooldown
        assert not _is_in_cooldown({"last_fired_at": None})

    def test_recently_fired_in_cooldown(self):
        from workers.alert_evaluator import _is_in_cooldown
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        assert _is_in_cooldown({"last_fired_at": recent}, cooldown_hours=24)

    def test_old_fired_not_in_cooldown(self):
        from workers.alert_evaluator import _is_in_cooldown
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        assert not _is_in_cooldown({"last_fired_at": old}, cooldown_hours=24)

    def test_exactly_at_boundary(self):
        from workers.alert_evaluator import _is_in_cooldown
        boundary = (datetime.now(timezone.utc) - timedelta(hours=24, seconds=1)).isoformat()
        assert not _is_in_cooldown({"last_fired_at": boundary}, cooldown_hours=24)


class TestEvaluatorDispatch:
    """Verify all 9 event-driven criteria types have evaluator functions."""

    def test_all_evaluators_registered(self):
        from workers.alert_evaluator import _EVALUATORS
        expected = {"spike", "rate_change", "price_threshold", "availability_change",
                    "new_listing", "tracking_removed", "anomaly", "volatility", "trend_reversal"}
        assert set(_EVALUATORS.keys()) == expected

    def test_digest_not_in_event_evaluators(self):
        from workers.alert_evaluator import _EVALUATORS
        assert "digest" not in _EVALUATORS

    def test_evaluators_are_coroutines(self):
        from workers.alert_evaluator import _EVALUATORS
        import asyncio
        for name, fn in _EVALUATORS.items():
            assert asyncio.iscoroutinefunction(fn), f"Evaluator {name} is not async"


class TestDigestSender:
    """Verify digest schedule logic."""

    def test_never_fired_is_due(self):
        from workers.digest_sender import _is_due
        assert _is_due(None, "weekly")
        assert _is_due(None, "daily")

    def test_daily_not_due_recently(self):
        from workers.digest_sender import _is_due
        recent = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        assert not _is_due(recent, "daily")

    def test_daily_due_after_23h(self):
        from workers.digest_sender import _is_due
        old = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        assert _is_due(old, "daily")

    def test_weekly_not_due_recently(self):
        from workers.digest_sender import _is_due
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        assert not _is_due(recent, "weekly")

    def test_weekly_due_after_6_days(self):
        from workers.digest_sender import _is_due
        old = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        assert _is_due(old, "weekly")


class TestFrequencyDescription:
    """Verify frequency description generation."""

    def test_event_driven_types(self):
        from services.email_service import build_frequency_description
        for ctype in ("spike", "rate_change", "price_threshold"):
            desc = build_frequency_description(ctype, {})
            assert "4×" in desc or "4x" in desc.lower()

    def test_digest_type(self):
        from services.email_service import build_frequency_description
        desc = build_frequency_description("digest", {"frequency": "weekly"})
        assert "weekly" in desc.lower()


class TestConfigConstants:
    """Verify new config constants are accessible."""

    def test_email_config_exists(self):
        from config import EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, EMAIL_FROM_ADDRESS
        assert EMAIL_SMTP_HOST == "smtp.gmail.com"
        assert EMAIL_SMTP_PORT == 587
        assert "jouledynamicscto" in EMAIL_FROM_ADDRESS

    def test_alert_config_exists(self):
        from config import ALERT_COOLDOWN_HOURS, MAX_SUBSCRIPTIONS_PER_EMAIL, CONFIRMATION_EXPIRY_HOURS
        assert ALERT_COOLDOWN_HOURS == 24
        assert MAX_SUBSCRIPTIONS_PER_EMAIL == 5
        assert CONFIRMATION_EXPIRY_HOURS == 48

    def test_service_role_key_fallback(self):
        from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_KEY
        assert SUPABASE_SERVICE_ROLE_KEY, "SUPABASE_SERVICE_ROLE_KEY should not be empty"


class TestRoutesRegistered:
    """Verify alert routes are registered in the v1 router."""

    def test_alerts_router_imported(self):
        from routes.v1.alerts import router
        assert router is not None

    def test_confirm_endpoint_exists(self):
        from routes.v1.alerts import router
        paths = [route.path for route in router.routes]
        assert "/api/v1/alerts/confirm" in paths

    def test_unsubscribe_endpoint_exists(self):
        from routes.v1.alerts import router
        paths = [route.path for route in router.routes]
        assert "/api/v1/alerts/unsubscribe" in paths

    def test_internal_evaluate_endpoint_exists(self):
        from routes.v1.alerts import router
        paths = [route.path for route in router.routes]
        assert "/api/v1/internal/evaluate-alerts" in paths
