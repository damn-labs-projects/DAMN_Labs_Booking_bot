import logging

from fastapi import APIRouter, Request
from aiogram.types import Update
from app.bot.manager import bot_manager
from app.db.mongodb import db
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
router = APIRouter()

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
    if db.db is None:
        return {"status": "db_unavailable"}

    payload = await request.json()
    event_type = payload.get("event_type")
    data = payload.get("data", {})

    tenant_id = data.get("custom_data", {}).get("tenant_id")
    if not tenant_id:
        return {"status": "ignored"}

    if event_type in ["subscription.created", "subscription.updated"]:
        next_billed_at = data.get("current_billing_period", {}).get("ends_at")
        end_date = datetime.fromisoformat(next_billed_at.replace("Z", "+00:00")) if next_billed_at else datetime.utcnow() + timedelta(days=30)

        await db.db.tenants.update_one(
            {"tenant_id": tenant_id},
            {"$set": {
                "subscription.status": "active",
                "subscription.paddle_subscription_id": data.get("id"),
                "subscription.current_period_end": end_date,
                "updated_at": datetime.utcnow()
            }}
        )
    elif event_type == "subscription.canceled":
        await db.db.tenants.update_one(
            {"tenant_id": tenant_id},
            {"$set": {"subscription.status": "cancelled", "updated_at": datetime.utcnow()}}
        )

    return {"status": "success"}