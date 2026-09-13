"""Background worker: sends periodic digest emails for digest-type subscriptions."""

from datetime import datetime, timezone

from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from services.email_service import build_criteria_summary, send_alert_email
from services.observability import setup_logger
from services.supabase_service import execute_tool_rpc

logger = setup_logger(__name__)


def _get_service_client():
    """Lazy-import Supabase service client to avoid circular imports at module level."""
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


async def _compile_market_digest(market: str) -> str:
    """Compile a market digest summary using existing RPCs."""
    snapshot = await execute_tool_rpc("get_market_snapshot", {
        "p_market": market,
        "p_start_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "p_end_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })
    averages = await execute_tool_rpc("get_market_averages", {"market_param": market})
    spikes = await execute_tool_rpc("get_spike_alerts", {
        "p_market": market,
        "threshold_param": 25,
        "days_param": 7,
        "p_limit": 3,
    })

    body = f"<h3>📊 Market Digest — {market}</h3>"
    body += "<h4>Snapshot</h4>"

    snap_data = snapshot.get("data", snapshot)
    if isinstance(snap_data, list) and snap_data:
        snap_data = snap_data[0]
    if isinstance(snap_data, dict):
        body += f"<p>Active Properties: {snap_data.get('active_count', '?')}, "
        body += f"Avg Rate: {snap_data.get('avg_rate', '?')}/night, "
        body += f"Availability: {snap_data.get('availability_pct', '?')}%</p>"

    avg_data = averages.get("data", averages)
    if isinstance(avg_data, list) and avg_data:
        body += "<h4>Market Averages</h4><ul>"
        for item in avg_data[:5]:
            body += f"<li>{item.get('property_name', '?')}: {item.get('avg_rate', '?')}/night</li>"
        body += "</ul>"

    spike_data = spikes.get("data", [])
    if spike_data:
        body += f"<h4>Notable Spikes (≥25%)</h4><ul>"
        for s in spike_data[:3]:
            body += f"<li>{s.get('property_name', '?')}: {s.get('pct_change', '?')}%</li>"
        body += "</ul>"
    else:
        body += "<p>No significant spikes in the last 7 days.</p>"

    return body


async def send_pending_digests() -> dict:
    """Fetch digest subscriptions due for send, compile market summary, send email."""
    client = _get_service_client()
    try:
        result = (
            client.table("alert_subscriptions")
            .select("id, email, criteria, unsubscribe_token, last_fired_at")
            .eq("status", "active")
            .eq("confirmed", True)
            .eq("criteria_type", "digest")
            .execute()
        )
    except Exception as exc:
        logger.error(f"[digest] failed to fetch digest subs: {exc}")
        return {"status": "error", "message": str(exc)}

    subs = result.data or []
    sent_count = 0

    for sub in subs:
        criteria = sub["criteria"]
        frequency = criteria.get("frequency", "weekly")

        if not _is_due(sub.get("last_fired_at"), frequency):
            continue

        market = criteria.get("market", "All Markets")
        digest_body = await _compile_market_digest(market)
        summary = build_criteria_summary("digest", criteria)
        subject = f"📊 {summary}"

        await send_alert_email(sub["email"], subject, digest_body, sub["unsubscribe_token"])

        client.table("alert_subscriptions").update({
            "last_fired_at": datetime.now(timezone.utc).isoformat(),
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", sub["id"]).execute()

        sent_count += 1
        logger.info(f"[digest] sent digest for sub {sub['id']}")

    return {"status": "success", "sent": sent_count}


def _is_due(last_fired_at: str | None, frequency: str) -> bool:
    """Check if a digest subscription is due for sending."""
    if not last_fired_at:
        return True

    fired_dt = datetime.fromisoformat(last_fired_at.replace("Z", "+00:00"))
    elapsed = datetime.now(timezone.utc) - fired_dt

    if frequency == "daily":
        return elapsed.total_seconds() >= 23 * 3600
    return elapsed.days >= 6
