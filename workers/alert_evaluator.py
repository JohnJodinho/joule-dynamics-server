"""Background worker: evaluates active alert subscriptions against fresh data.

Reuses existing Supabase RPCs via execute_tool_rpc() — zero duplicated query logic.
Each evaluator function is a small unit (CC <= 4) dispatched by criteria_type.
"""

from datetime import datetime, timezone, timedelta

from config import ALERT_COOLDOWN_HOURS, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from services.email_service import build_criteria_summary, send_alert_email
from services.observability import setup_logger
from services.supabase_service import execute_tool_rpc

logger = setup_logger(__name__)


def _get_service_client():
    """Lazy-import Supabase service client to avoid circular imports at module level."""
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _is_in_cooldown(sub: dict, cooldown_hours: int = ALERT_COOLDOWN_HOURS) -> bool:
    """Check if subscription is in cooldown window."""
    last_fired = sub.get("last_fired_at")
    if not last_fired:
        return False
    fired_dt = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - fired_dt < timedelta(hours=cooldown_hours)


async def _evaluate_spike(criteria: dict) -> tuple[bool, str]:
    """Check spike condition using get_spike_alerts RPC."""
    market = criteria.get("market")
    threshold = criteria.get("threshold_pct", 25)
    result = await execute_tool_rpc("get_spike_alerts", {
        "threshold_param": threshold,
        "p_market": market,
        "p_limit": 5,
        "days_param": 1,
    })
    data = result.get("data", [])
    if not data:
        return False, ""
    names = [f"{r.get('property_name', '?')} ({r.get('pct_change', '?')}%)" for r in data[:5]]
    body = f"<h3>🔔 Price Spike Alert — {market or 'All Markets'}</h3>"
    body += f"<p>{len(data)} properties spiked ≥{threshold}%:</p><ul>"
    body += "".join(f"<li>{n}</li>" for n in names) + "</ul>"
    return True, body


async def _evaluate_rate_change(criteria: dict) -> tuple[bool, str]:
    """Check rate change condition using get_market_rate_changes RPC."""
    market = criteria.get("market")
    direction = criteria.get("direction", "both")
    result = await execute_tool_rpc("get_market_rate_changes", {
        "p_market": market,
        "p_days": 1,
        "p_limit": 5,
    })
    data = result.get("data", result)
    total = data.get("total_changes", 0) if isinstance(data, dict) else 0
    if total == 0:
        return False, ""

    if direction == "up" and not data.get("increases", 0):
        return False, ""
    if direction == "down" and not data.get("decreases", 0):
        return False, ""

    body = f"<h3>🔔 Rate Change Alert — {market or 'All Markets'}</h3>"
    body += f"<p>{total} properties had rate changes in the last scrape cycle.</p>"
    examples = data.get("examples", [])
    if examples:
        body += "<ul>"
        body += "".join(f"<li>{e.get('name', '?')}: {e.get('change', '?')}</li>" for e in examples[:5])
        body += "</ul>"
    return True, body


async def _evaluate_price_threshold(criteria: dict) -> tuple[bool, str]:
    """Check price threshold using get_property_snapshot RPC."""
    prop = criteria.get("property_search", "")
    operator = criteria.get("operator", "below")
    value = criteria.get("value", 0)
    result = await execute_tool_rpc("get_property_snapshot", {"p_property_search": prop})
    data = result.get("data", result)

    if isinstance(data, list) and data:
        data = data[0]
    rate = data.get("nightly_rate") or data.get("current_rate")
    if rate is None:
        return False, ""

    rate = float(rate)
    condition_met = (operator == "below" and rate < value) or (operator == "above" and rate > value)
    if not condition_met:
        return False, ""

    name = data.get("property_name", prop)
    body = f"<h3>🔔 Price Threshold Alert</h3>"
    body += f"<p><strong>{name}</strong> is now at <strong>{rate}/night</strong> "
    body += f"({operator} your threshold of {value}).</p>"
    return True, body


async def _evaluate_availability(criteria: dict) -> tuple[bool, str]:
    """Check availability change using get_property_snapshot RPC."""
    prop = criteria.get("property_search", "")
    watch_for = criteria.get("watch_for", "any")
    result = await execute_tool_rpc("get_property_snapshot", {"p_property_search": prop})
    data = result.get("data", result)

    if isinstance(data, list) and data:
        data = data[0]
    is_available = data.get("is_available")
    if is_available is None:
        return False, ""

    if watch_for == "available" and not is_available:
        return False, ""
    if watch_for == "unavailable" and is_available:
        return False, ""

    status_text = "available" if is_available else "unavailable"
    name = data.get("property_name", prop)
    body = f"<h3>🔔 Availability Alert</h3>"
    body += f"<p><strong>{name}</strong> is now <strong>{status_text}</strong>.</p>"
    return True, body


async def _evaluate_new_listing(criteria: dict) -> tuple[bool, str]:
    """Check for new listings using get_recently_changed_tracking RPC."""
    market = criteria.get("market")
    result = await execute_tool_rpc("get_recently_changed_tracking", {"p_days": 1})
    data = result.get("data", [])
    added = [r for r in data if r.get("change_type") == "added"]
    if market:
        added = [r for r in added if market.lower() in (r.get("market", "") or "").lower()]
    if not added:
        return False, ""

    body = f"<h3>🔔 New Listing Alert — {market or 'All Markets'}</h3>"
    body += f"<p>{len(added)} new properties added to tracking:</p><ul>"
    body += "".join(f"<li>{r.get('property_name', '?')}</li>" for r in added[:5]) + "</ul>"
    return True, body


async def _evaluate_tracking_removed(criteria: dict) -> tuple[bool, str]:
    """Check for removed listings using get_recently_changed_tracking RPC."""
    market = criteria.get("market")
    result = await execute_tool_rpc("get_recently_changed_tracking", {"p_days": 1})
    data = result.get("data", [])
    removed = [r for r in data if r.get("change_type") == "removed"]
    if market:
        removed = [r for r in removed if market.lower() in (r.get("market", "") or "").lower()]
    if not removed:
        return False, ""

    body = f"<h3>🔔 Tracking Removed Alert — {market or 'All Markets'}</h3>"
    body += f"<p>{len(removed)} properties removed from tracking:</p><ul>"
    body += "".join(f"<li>{r.get('property_name', '?')}</li>" for r in removed[:5]) + "</ul>"
    return True, body


async def _evaluate_anomaly(criteria: dict) -> tuple[bool, str]:
    """Check for pricing anomalies using get_rate_anomaly_report RPC."""
    prop = criteria.get("property_search", "")
    threshold = criteria.get("deviation_threshold", 25)
    result = await execute_tool_rpc("get_rate_anomaly_report", {
        "p_property_search": prop,
        "p_days": 7,
        "p_deviation_threshold": threshold,
    })
    data = result.get("data", result)
    if isinstance(data, list) and data:
        data = data[0]
    anomaly_count = data.get("anomaly_count", 0) if isinstance(data, dict) else 0
    if not anomaly_count:
        return False, ""

    name = data.get("property_name", prop)
    body = f"<h3>🔔 Pricing Anomaly Alert</h3>"
    body += f"<p><strong>{name}</strong> has {anomaly_count} anomalous pricing events "
    body += f"(≥{threshold}% deviation from baseline).</p>"
    return True, body


async def _evaluate_volatility(criteria: dict) -> tuple[bool, str]:
    """Check volatility ranking using get_most_volatile_properties RPC."""
    market = criteria.get("market")
    top_n = criteria.get("top_n", 5)
    result = await execute_tool_rpc("get_most_volatile_properties", {
        "p_market": market,
        "p_limit": top_n,
        "p_days": 7,
    })
    data = result.get("data", [])
    if not data:
        return False, ""

    body = f"<h3>🔔 Volatility Alert — {market or 'All Markets'}</h3>"
    body += f"<p>Top {len(data)} most volatile properties:</p><ul>"
    for r in data[:top_n]:
        name = r.get("property_name", "?")
        changes = r.get("change_count", r.get("rate_change_count", "?"))
        body += f"<li>{name} — {changes} rate changes</li>"
    body += "</ul>"
    return True, body


async def _evaluate_trend_reversal(criteria: dict) -> tuple[bool, str]:
    """Check market trend direction using get_market_trend RPC."""
    market = criteria.get("market")
    watch_for = criteria.get("watch_for", "falling")
    result = await execute_tool_rpc("get_market_trend", {
        "p_market": market,
        "p_days": 14,
    })
    data = result.get("data", [])
    if not data or len(data) < 7:
        return False, ""

    recent_half = data[len(data) // 2:]
    rates = [float(d.get("avg_rate", 0)) for d in recent_half if d.get("avg_rate")]
    if len(rates) < 2:
        return False, ""

    slope = rates[-1] - rates[0]
    direction = "falling" if slope < 0 else "rising"
    if direction != watch_for:
        return False, ""

    body = f"<h3>🔔 Trend Reversal Alert — {market}</h3>"
    body += f"<p>Market rates are now <strong>{direction}</strong> — "
    body += f"average moved from {rates[0]:.0f} to {rates[-1]:.0f} over the last 7 days.</p>"
    return True, body


_EVALUATORS = {
    "spike": _evaluate_spike,
    "rate_change": _evaluate_rate_change,
    "price_threshold": _evaluate_price_threshold,
    "availability_change": _evaluate_availability,
    "new_listing": _evaluate_new_listing,
    "tracking_removed": _evaluate_tracking_removed,
    "anomaly": _evaluate_anomaly,
    "volatility": _evaluate_volatility,
    "trend_reversal": _evaluate_trend_reversal,
}


async def _evaluate_subscription(sub: dict) -> bool:
    """Evaluate a single subscription against live data. Returns True if alert was fired."""
    criteria_type = sub["criteria_type"]
    if criteria_type == "digest":
        return False

    evaluator = _EVALUATORS.get(criteria_type)
    if not evaluator:
        logger.warning(f"[evaluator] unknown criteria_type: {criteria_type}")
        return False

    if _is_in_cooldown(sub):
        logger.info(f"[evaluator] sub {sub['id']} in cooldown, skipping")
        return False

    condition_met, alert_body = await evaluator(sub["criteria"])
    if not condition_met:
        return False

    summary = build_criteria_summary(criteria_type, sub["criteria"])
    subject = f"🔔 {summary}"
    await send_alert_email(sub["email"], subject, alert_body, sub["unsubscribe_token"])

    client = _get_service_client()
    client.table("alert_subscriptions").update({
        "last_fired_at": datetime.now(timezone.utc).isoformat(),
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", sub["id"]).execute()

    logger.info(f"[evaluator] fired alert for sub {sub['id']}: {criteria_type}")
    return True


async def evaluate_all_active_subscriptions() -> dict:
    """Fetch all active subscriptions, evaluate each, fire if condition met."""
    client = _get_service_client()
    try:
        result = (
            client.table("alert_subscriptions")
            .select("id, email, criteria_type, criteria, unsubscribe_token, last_fired_at")
            .eq("status", "active")
            .eq("confirmed", True)
            .execute()
        )
    except Exception as exc:
        logger.error(f"[evaluator] failed to fetch subscriptions: {exc}")
        return {"status": "error", "message": str(exc)}

    subs = result.data or []
    logger.info(f"[evaluator] evaluating {len(subs)} active subscriptions")

    fired_count = 0
    checked_count = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for sub in subs:
        checked_count += 1
        try:
            fired = await _evaluate_subscription(sub)
            if fired:
                fired_count += 1
            else:
                client.table("alert_subscriptions").update({
                    "last_checked_at": now_iso,
                }).eq("id", sub["id"]).execute()
        except Exception as exc:
            logger.error(f"[evaluator] error evaluating sub {sub['id']}: {exc}")

    logger.info(f"[evaluator] done: checked={checked_count}, fired={fired_count}")
    return {
        "status": "success",
        "checked": checked_count,
        "fired": fired_count,
    }
