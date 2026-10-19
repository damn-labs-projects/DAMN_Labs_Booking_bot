import logging
import hmac
import hashlib

from fastapi import APIRouter, Request
from aiogram.types import Update
from app.bot.manager import bot_manager
from app.config import settings
from app.db.mongodb import db
from datetime import datetime, timedelta, timezone

router = APIRouter()
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _verify_paddle_signature(request: Request, body: bytes) -> bool:
    signature = request.headers.get("paddle-signature")
    if not signature:
        return False
    try:
        parts = signature.split(";")
        ts = None
        h1 = None
        for p in parts:
            if p.startswith("ts="):
                ts = p.split("=", 1)[1]
            if p.startswith("h1="):
                h1 = p.split("=", 1)[1]
        if not ts or not h1:
            return False
        secret = settings.PADDLE_WEBHOOK_SECRET_KEY or ""
        if not secret:
            logger.error(
                "PADDLE_WEBHOOK_SECRET_KEY is not set; rejecting webhook"
            )
            return False
        payload = f"{ts}:{body.decode('utf-8', errors='ignore')}"
        computed = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, h1)
    except Exception:
        return False

def _extract_tenant_id(data: dict) -> str:
    custom = data.get("custom_data") or {}
    if not isinstance(custom, dict):
        return ""
    for key, value in custom.items():
        if str(key).lower() in {"tenant_id", "tenantid"} and value:
            return str(value).strip()
    return ""


def _parse_deadline(value) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            logger.warning("Unparseable Paddle deadline %r; falling back to +30 days", value)
    return _utcnow() + timedelta(days=30)


@router.post("/telegram/{bot_token}")
async def telegram_webhook(bot_token: str, request: Request):
    try:
        bot = bot_manager.get_bot_by_token(bot_token)
        data = await request.json()
        update = Update(**data)
        await bot_manager.dp.feed_update(bot=bot, update=update)
    except Exception:
        logger.exception("Unhandled error while processing Telegram update for bot ...%s", bot_token[-6:])
    return {"status": "ok"}

@router.post("/paddle")
async def paddle_webhook(request: Request):
    body = await request.body()
    if not _verify_paddle_signature(request, body):
        logger.warning("Invalid Paddle webhook signature")
        return {"status": "invalid_signature"}

    if db.db is None:
        return {"status": "db_unavailable"}

    try:
        payload = await request.json()
    except Exception:
        return {"status": "bad_json"}

    event_type = payload.get("event_type")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return {"status": "bad_data"}

    tenant_id = _extract_tenant_id(data)
    if not tenant_id:
        logger.warning(
            "Paddle event %s has no custom_data.tenant_id; event ignored",
            event_type,
        )
        return {"status": "ignored"}

    if event_type in ["subscription.created", "subscription.updated", "transaction.paid"]:
        next_billed_at = (data.get("current_billing_period") or {}).get("ends_at") or data.get("next_billed_at")
        end_date = _parse_deadline(next_billed_at)

        status = "active"
        if event_type == "transaction.paid" and data.get("status") == "draft":
            status = "past_due"
        await db.db.tenants.update_one(
            {"tenant_id": tenant_id},
            {"$set": {
                "subscription.status": status,
                "subscription.paddle_subscription_id": data.get("id") or data.get("subscription_id"),
                "subscription.current_period_end": end_date,
                "updated_at": _utcnow()
            }},
        )
    elif event_type in ["subscription.canceled", "subscription.paused"]:
        await db.db.tenants.update_one(
            {"tenant_id": tenant_id},
            {"$set": {"subscription.status": "cancelled", "updated_at": _utcnow()}}
        )
    elif event_type in ["subscription.past_due"]:
        await db.db.tenants.update_one(
            {"tenant_id": tenant_id},
            {"$set": {"subscription.status": "past_due", "updated_at": _utcnow()}}
        )

    return {"status": "success"}