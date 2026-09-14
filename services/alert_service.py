"""Alert subscription lifecycle: create (pending), confirm, unsubscribe."""

import secrets
from datetime import datetime, timezone

from supabase import create_client

from config import (
    MAX_SUBSCRIPTIONS_PER_EMAIL,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)
from services.email_service import (
    build_criteria_summary,
    send_confirmation_email,
    send_subscription_active_email,
)
from services.observability import setup_logger

logger = setup_logger(__name__)

_service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


async def create_pending_subscription(
    email: str, criteria_type: str, criteria: dict, raw_text: str
) -> dict:
    """INSERT pending subscription and send confirmation email.

    Enforces per-email subscription cap. Returns a natural language
    rejection message if the cap is exceeded so the LLM can relay it.
    """
    if not email or not criteria_type:
        return {
            "status": "error",
            "message": "Email address and alert type are both required to create a subscription.",
        }

    active_count = await _count_active_subscriptions(email)
    if active_count >= MAX_SUBSCRIPTIONS_PER_EMAIL:
        return {
            "status": "limit_reached",
            "message": (
                f"You already have {active_count} active alert subscriptions on this email address. "
                f"The maximum is {MAX_SUBSCRIPTIONS_PER_EMAIL}. "
                "To add a new one, you'd need to unsubscribe from an existing alert first. "
                "Check your email for unsubscribe links in any previous alert emails."
            ),
        }

    confirmation_token = secrets.token_urlsafe(32)
    unsubscribe_token = secrets.token_urlsafe(32)

    try:
        _service_client.table("alert_subscriptions").insert({
            "email": email,
            "criteria_type": criteria_type,
            "criteria": criteria,
            "raw_request_text": raw_text,
            "confirmation_token": confirmation_token,
            "unsubscribe_token": unsubscribe_token,
            "confirmed": False,
            "status": "pending",
        }).execute()
    except Exception as exc:
        logger.error(f"[alert_service] insert failed: {exc}")
        return {"status": "error", "message": "Failed to create subscription. Please try again."}

    summary = build_criteria_summary(criteria_type, criteria)
    await send_confirmation_email(email, confirmation_token, summary)

    logger.info(f"[alert_service] pending subscription created for {email}: {criteria_type}")
    return {
        "status": "pending",
        "message": (
            f"Alert subscription created! A confirmation email has been sent to {email}. "
            "Please click the link in that email to activate the alert — "
            "it won't start monitoring until you confirm."
        ),
    }


async def confirm_subscription(token: str) -> dict:
    """Confirm a pending subscription via confirmation token.

    Sets confirmed=true, status='active', sends subscription-active email.
    """
    try:
        result = (
            _service_client.table("alert_subscriptions")
            .select("id, email, criteria_type, criteria, unsubscribe_token, status")
            .eq("confirmation_token", token)
            .execute()
        )
    except Exception as exc:
        logger.error(f"[alert_service] confirm lookup failed: {exc}")
        return {"status": "error", "message": "Unable to process confirmation."}

    if not result.data:
        return {"status": "error", "message": "Invalid or expired confirmation link."}

    sub = result.data[0]
    if sub["status"] == "active":
        return {"status": "already_active", "message": "This subscription is already active."}

    if sub["status"] != "pending":
        return {"status": "error", "message": "This subscription cannot be confirmed."}

    try:
        _service_client.table("alert_subscriptions").update({
            "confirmed": True,
            "status": "active",
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", sub["id"]).execute()
    except Exception as exc:
        logger.error(f"[alert_service] confirm update failed: {exc}")
        return {"status": "error", "message": "Unable to activate subscription."}

    await send_subscription_active_email(
        to=sub["email"],
        criteria_type=sub["criteria_type"],
        criteria=sub["criteria"],
        unsubscribe_token=sub["unsubscribe_token"],
    )

    logger.info(f"[alert_service] subscription confirmed: {sub['id']}")
    return {"status": "active", "message": "Your alert subscription is now active!"}


async def unsubscribe(token: str) -> dict:
    """Unsubscribe from an alert subscription via unsubscribe token."""
    try:
        result = (
            _service_client.table("alert_subscriptions")
            .select("id, status")
            .eq("unsubscribe_token", token)
            .execute()
        )
    except Exception as exc:
        logger.error(f"[alert_service] unsubscribe lookup failed: {exc}")
        return {"status": "error", "message": "Unable to process unsubscribe request."}

    if not result.data:
        return {"status": "error", "message": "Invalid unsubscribe link."}

    sub = result.data[0]
    if sub["status"] == "unsubscribed":
        return {"status": "already_unsubscribed", "message": "You've already unsubscribed from this alert."}

    try:
        _service_client.table("alert_subscriptions").update({
            "status": "unsubscribed",
            "unsubscribe_token": None,
        }).eq("id", sub["id"]).execute()
    except Exception as exc:
        logger.error(f"[alert_service] unsubscribe update failed: {exc}")
        return {"status": "error", "message": "Unable to process unsubscribe request."}

    logger.info(f"[alert_service] unsubscribed: {sub['id']}")
    return {"status": "unsubscribed", "message": "You've been unsubscribed. You won't receive any more alerts for this subscription."}


async def _count_active_subscriptions(email: str) -> int:
    """Count active + pending subscriptions for rate-limiting."""
    try:
        result = (
            _service_client.table("alert_subscriptions")
            .select("id", count="exact")
            .eq("email", email)
            .in_("status", ["active", "pending"])
            .execute()
        )
        return result.count or 0
    except Exception as exc:
        logger.error(f"[alert_service] count failed: {exc}")
        return 0
