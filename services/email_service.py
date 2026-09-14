"""Pluggable email dispatch interface with Gmail SMTP implementation."""

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (
    ALERT_BASE_URL,
    EMAIL_FROM_ADDRESS,
    EMAIL_SMTP_HOST,
    EMAIL_SMTP_PASSWORD,
    EMAIL_SMTP_PORT,
    RESEND_API_KEY,
    RESEND_FROM_ADDRESS,
)
from services.observability import setup_logger

logger = setup_logger(__name__)

_CRITERIA_LABELS = {
    "spike": "Price spike",
    "rate_change": "Rate change",
    "price_threshold": "Price threshold",
    "availability_change": "Availability change",
    "new_listing": "New listing tracked",
    "tracking_removed": "Property delisted",
    "anomaly": "Pricing anomaly",
    "volatility": "Volatility alert",
    "trend_reversal": "Market trend reversal",
    "digest": "Market digest",
}

_FREQUENCY_DESCRIPTIONS = {
    "spike": "Checked 4× daily after each data scrape. Max 1 alert per 24 hours.",
    "rate_change": "Checked 4× daily after each data scrape. Max 1 alert per 24 hours.",
    "price_threshold": "Checked 4× daily after each data scrape. Max 1 alert per 24 hours.",
    "availability_change": "Checked 4× daily after each data scrape. Max 1 alert per 24 hours.",
    "new_listing": "Checked 4× daily after each data scrape. Max 1 alert per 24 hours.",
    "tracking_removed": "Checked 4× daily after each data scrape. Max 1 alert per 24 hours.",
    "anomaly": "Checked 4× daily after each data scrape. Max 1 alert per 24 hours.",
    "volatility": "Checked 4× daily after each data scrape. Max 1 alert per 24 hours.",
    "trend_reversal": "Checked 4× daily after each data scrape. Max 1 alert per 24 hours.",
    "digest": "Sent on your chosen schedule (daily or weekly).",
}


def build_criteria_summary(criteria_type: str, criteria: dict) -> str:
    """Convert structured criteria into a human-readable summary string."""
    label = _CRITERIA_LABELS.get(criteria_type, criteria_type)
    market = criteria.get("market", "")
    prop = criteria.get("property_search", "")

    if criteria_type == "spike":
        threshold = criteria.get("threshold_pct", 25)
        target = f" in {market}" if market else ""
        return f"{label} ≥{threshold}%{target}"

    if criteria_type == "price_threshold":
        op = criteria.get("operator", "below")
        val = criteria.get("value", "?")
        return f"{label}: {prop} {op} {val}/night"

    if criteria_type in ("availability_change",):
        watch = criteria.get("watch_for", "any change")
        return f"{label}: {prop} becomes {watch}"

    if criteria_type == "digest":
        freq = criteria.get("frequency", "weekly")
        return f"{freq.capitalize()} {label} for {market}"

    if criteria_type == "trend_reversal":
        watch = criteria.get("watch_for", "any direction")
        return f"{label}: {market} rates start {watch}"

    if market:
        return f"{label} in {market}"

    if prop:
        return f"{label} for {prop}"

    return label


def build_frequency_description(criteria_type: str, criteria: dict) -> str:
    """Describe expected alert frequency based on criteria type."""
    if criteria_type == "digest":
        freq = criteria.get("frequency", "weekly")
        return f"Sent {freq}."
    return _FREQUENCY_DESCRIPTIONS.get(criteria_type, "Checked periodically.")


def _send_resend(to: str, subject: str, body_html: str) -> None:
    """Send email via Resend HTTP REST API over outbound HTTPS port 443."""
    import json
    import urllib.request

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "PulseAI/1.0",
    }
    payload = {
        "from": RESEND_FROM_ADDRESS,
        "to": [to],
        "subject": subject,
        "html": body_html,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"Resend HTTP error: {resp.status}")


async def send_email(to: str, subject: str, body_html: str) -> bool:
    """Send email via Resend (HTTPS 443) if configured, else fallback to SMTP."""
    loop = asyncio.get_event_loop()
    try:
        if RESEND_API_KEY:
            await loop.run_in_executor(None, _send_resend, to, subject, body_html)
            logger.info(f"[email-resend] sent to={to} subject={subject[:50]}")
            return True
        await loop.run_in_executor(None, _send_smtp, to, subject, body_html)
        logger.info(f"[email-smtp] sent to={to} subject={subject[:50]}")
        return True
    except Exception as exc:
        logger.error(f"[email] failed to={to}: {exc}")
        return False


def _send_smtp(to: str, subject: str, body_html: str) -> None:
    """Blocking SMTP send — runs in executor."""
    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_FROM_ADDRESS
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_FROM_ADDRESS, EMAIL_SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM_ADDRESS, to, msg.as_string())


async def send_confirmation_email(to: str, token: str, criteria_summary: str) -> bool:
    """Send double-opt-in confirmation email with token link."""
    confirm_url = f"{ALERT_BASE_URL}/api/v1/alerts/confirm?token={token}"
    subject = "Confirm Your Pulse AI Alert Subscription"
    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px;">
        <h2 style="color: #1a1a2e;">Confirm Your Alert Subscription</h2>
        <p>You requested a Pulse AI alert for:</p>
        <div style="background: #f0f4ff; border-left: 4px solid #4361ee; padding: 12px 16px; margin: 16px 0; border-radius: 4px;">
            <strong>{criteria_summary}</strong>
        </div>
        <p>Click the button below to activate this alert:</p>
        <a href="{confirm_url}" style="display: inline-block; background: #4361ee; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 16px 0;">
            Confirm Subscription
        </a>
        <p style="color: #666; font-size: 13px;">This link expires in 48 hours. If you didn't request this, ignore this email.</p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
        <p style="color: #999; font-size: 12px;">Pulse AI — Real Estate Intelligence by Joule Dynamics</p>
    </div>
    """
    return await send_email(to, subject, body)


async def send_subscription_active_email(
    to: str,
    criteria_type: str,
    criteria: dict,
    unsubscribe_token: str,
) -> bool:
    """Send 'subscription active' email with alert template preview and frequency."""
    summary = build_criteria_summary(criteria_type, criteria)
    frequency = build_frequency_description(criteria_type, criteria)
    unsub_url = f"{ALERT_BASE_URL}/api/v1/alerts/unsubscribe?token={unsubscribe_token}"
    subject = "✅ Your Pulse AI Alert Is Now Active"
    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px;">
        <h2 style="color: #1a1a2e;">Your Alert Is Active!</h2>
        <p>Your Pulse AI alert subscription has been confirmed and is now live.</p>

        <h3 style="color: #4361ee;">What You Subscribed To</h3>
        <div style="background: #f0f4ff; border-left: 4px solid #4361ee; padding: 12px 16px; margin: 8px 0; border-radius: 4px;">
            <strong>{summary}</strong>
        </div>

        <h3 style="color: #4361ee;">How Often You'll Hear From Us</h3>
        <p>{frequency}</p>

        <h3 style="color: #4361ee;">What Alert Emails Look Like</h3>
        <div style="background: #fff8e1; border: 1px solid #ffe082; padding: 16px; margin: 8px 0; border-radius: 4px;">
            <p style="font-size: 13px; color: #666;"><em>Sample alert preview:</em></p>
            <p><strong>🔔 {summary}</strong></p>
            <p>Example: "2 properties triggered this alert — Property A rate changed from $120 to $155/night (+29.2%), Property B..."</p>
            <p style="font-size: 12px;"><a href="#">View in Dashboard</a> · <a href="#">Unsubscribe</a></p>
        </div>

        <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
        <p style="color: #999; font-size: 12px;">
            <a href="{unsub_url}" style="color: #999;">Unsubscribe from this alert</a> ·
            Pulse AI — Real Estate Intelligence by Joule Dynamics
        </p>
    </div>
    """
    return await send_email(to, subject, body)


async def send_alert_email(
    to: str, subject: str, alert_body: str, unsubscribe_token: str
) -> bool:
    """Send triggered alert email with mandatory unsubscribe link."""
    unsub_url = f"{ALERT_BASE_URL}/api/v1/alerts/unsubscribe?token={unsubscribe_token}"
    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px;">
        {alert_body}
        <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;">
        <p style="color: #999; font-size: 12px;">
            <a href="{unsub_url}" style="color: #999;">Unsubscribe from this alert</a> ·
            Pulse AI — Real Estate Intelligence by Joule Dynamics
        </p>
    </div>
    """
    return await send_email(to, subject, body)
