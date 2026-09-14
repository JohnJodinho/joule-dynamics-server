"""Alert subscription management endpoints (confirmation, unsubscribe, worker trigger)."""

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from config import INTERNAL_API_KEY
from services.alert_service import confirm_subscription, unsubscribe
from services.observability import setup_logger

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["Alert Subscriptions"],
)

_SUCCESS_HTML = """
<html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 80px auto; text-align: center;">
<h2 style="color: #1a1a2e;">{title}</h2>
<p>{message}</p>
<p style="color: #999; font-size: 12px; margin-top: 40px;">Pulse AI — Real Estate Intelligence by Joule Dynamics</p>
</body></html>
"""

_ERROR_HTML = """
<html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 80px auto; text-align: center;">
<h2 style="color: #c0392b;">{title}</h2>
<p>{message}</p>
<p style="color: #999; font-size: 12px; margin-top: 40px;">Pulse AI — Real Estate Intelligence by Joule Dynamics</p>
</body></html>
"""


@router.get("/alerts/confirm", response_class=HTMLResponse)
async def confirm_alert(token: str):
    """Confirms a pending alert subscription via token link click."""
    if not token:
        return HTMLResponse(
            _ERROR_HTML.format(title="Invalid Link", message="No confirmation token provided."),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    result = await confirm_subscription(token)

    if result["status"] in ("active", "already_active"):
        return HTMLResponse(
            _SUCCESS_HTML.format(title="✅ Subscription Confirmed!", message=result["message"]),
            status_code=status.HTTP_200_OK,
        )

    return HTMLResponse(
        _ERROR_HTML.format(title="Confirmation Failed", message=result["message"]),
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@router.get("/alerts/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_alert(token: str):
    """Unsubscribes from an active alert subscription."""
    if not token:
        return HTMLResponse(
            _ERROR_HTML.format(title="Invalid Link", message="No unsubscribe token provided."),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    result = await unsubscribe(token)

    if result["status"] in ("unsubscribed", "already_unsubscribed"):
        return HTMLResponse(
            _SUCCESS_HTML.format(title="Unsubscribed", message=result["message"]),
            status_code=status.HTTP_200_OK,
        )

    return HTMLResponse(
        _ERROR_HTML.format(title="Unsubscribe Failed", message=result["message"]),
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _is_internal_authorized(request: Request) -> bool:
    """Verify internal API key header."""
    provided_key = request.headers.get("X-Internal-Key", "")
    return bool(INTERNAL_API_KEY and provided_key == INTERNAL_API_KEY)


@router.post("/internal/evaluate-alerts")
async def trigger_alert_evaluation(request: Request):
    """Internal endpoint: evaluates all active subscriptions. Secured by X-Internal-Key header."""
    if not _is_internal_authorized(request):
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"error": "Forbidden"})

    from workers.alert_evaluator import evaluate_all_active_subscriptions
    return await evaluate_all_active_subscriptions()


@router.post("/internal/send-digests")
async def trigger_send_digests(request: Request):
    """Internal endpoint: sends pending market digests. Secured by X-Internal-Key header."""
    if not _is_internal_authorized(request):
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"error": "Forbidden"})

    from workers.digest_sender import send_pending_digests
    return await send_pending_digests()


@router.post("/internal/cleanup-alerts")
async def trigger_cleanup_alerts(request: Request):
    """Internal endpoint: deletes unconfirmed stale subscriptions. Secured by X-Internal-Key header."""
    if not _is_internal_authorized(request):
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"error": "Forbidden"})

    from workers.alert_cleanup import cleanup_stale_subscriptions
    count = await cleanup_stale_subscriptions()
    return {"status": "success", "deleted_count": count}


@router.post("/internal/run-all-workers")
async def trigger_all_workers(request: Request):
    """Internal endpoint: sequentially runs alert evaluation, digests, and cleanup."""
    if not _is_internal_authorized(request):
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"error": "Forbidden"})

    from workers.alert_cleanup import cleanup_stale_subscriptions
    from workers.alert_evaluator import evaluate_all_active_subscriptions
    from workers.digest_sender import send_pending_digests

    eval_result = await evaluate_all_active_subscriptions()
    digest_result = await send_pending_digests()
    cleanup_count = await cleanup_stale_subscriptions()

    return {
        "status": "success",
        "evaluator": eval_result,
        "digests": digest_result,
        "cleaned_subscriptions": cleanup_count,
    }


@router.get("/internal/diagnose-smtp")
async def diagnose_smtp(to: str = "john.albarka.ibrahim@gmail.com"):
    """Diagnostics endpoint: returns exact network and credential state for SMTP."""
    import smtplib
    import socket
    from email.mime.text import MIMEText
    from config import EMAIL_FROM_ADDRESS, EMAIL_SMTP_HOST, EMAIL_SMTP_PASSWORD, EMAIL_SMTP_PORT

    steps: dict[str, object] = {
        "password_set": bool(EMAIL_SMTP_PASSWORD),
        "password_len": len(EMAIL_SMTP_PASSWORD) if EMAIL_SMTP_PASSWORD else 0,
    }

    # Test 587
    try:
        s587 = socket.create_connection((EMAIL_SMTP_HOST, 587), timeout=5)
        s587.close()
        steps["port_587"] = "CONNECTED"
    except Exception as exc:
        steps["port_587"] = f"FAILED: {exc}"

    # Test 465 (SSL)
    try:
        s465 = socket.create_connection((EMAIL_SMTP_HOST, 465), timeout=5)
        s465.close()
        steps["port_465"] = "CONNECTED"
    except Exception as exc:
        steps["port_465"] = f"FAILED: {exc}"

    # Test 25
    try:
        s25 = socket.create_connection((EMAIL_SMTP_HOST, 25), timeout=5)
        s25.close()
        steps["port_25"] = "CONNECTED"
    except Exception as exc:
        steps["port_25"] = f"FAILED: {exc}"

    # If 465 connected, try SSL send
    if steps.get("port_465") == "CONNECTED":
        try:
            with smtplib.SMTP_SSL(EMAIL_SMTP_HOST, 465, timeout=10) as server:
                server.login(EMAIL_FROM_ADDRESS, EMAIL_SMTP_PASSWORD)
                msg = MIMEText("Pulse AI SSL Diagnostic Test")
                msg["From"] = EMAIL_FROM_ADDRESS
                msg["To"] = to
                msg["Subject"] = "Pulse AI SSL Diagnostic"
                server.sendmail(EMAIL_FROM_ADDRESS, to, msg.as_string())
            steps["ssl_send_465"] = "SUCCESS"
            return JSONResponse(content={"status": "success", "steps": steps})
        except Exception as exc:
            steps["ssl_send_465"] = f"FAILED: {exc}"

    return JSONResponse(content={"status": "error", "steps": steps})

