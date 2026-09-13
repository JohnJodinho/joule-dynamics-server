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


@router.post("/internal/evaluate-alerts")
async def trigger_alert_evaluation(request: Request):
    """Internal endpoint: evaluates all active subscriptions. Secured by X-Internal-Key header."""
    provided_key = request.headers.get("X-Internal-Key", "")
    if not INTERNAL_API_KEY or provided_key != INTERNAL_API_KEY:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": "Forbidden"},
        )

    from workers.alert_evaluator import evaluate_all_active_subscriptions
    result = await evaluate_all_active_subscriptions()
    return result
