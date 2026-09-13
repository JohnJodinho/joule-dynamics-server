"""Cleanup job: deletes unconfirmed subscriptions older than the expiry window."""

from datetime import datetime, timezone, timedelta

from config import CONFIRMATION_EXPIRY_HOURS, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from services.observability import setup_logger

logger = setup_logger(__name__)


def _get_service_client():
    """Lazy-import Supabase service client to avoid circular imports at module level."""
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


async def cleanup_stale_subscriptions() -> int:
    """Delete pending subscriptions where created_at < now() - expiry window.

    Returns count of deleted subscriptions.
    """
    client = _get_service_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=CONFIRMATION_EXPIRY_HOURS)).isoformat()

    try:
        result = (
            client.table("alert_subscriptions")
            .delete()
            .eq("status", "pending")
            .lt("created_at", cutoff)
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.info(f"[cleanup] deleted {count} stale pending subscriptions")
        return count
    except Exception as exc:
        logger.error(f"[cleanup] failed: {exc}")
        return 0
