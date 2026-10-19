import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, status
from app.db.mongodb import db
from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_authorized(request: Request) -> bool:
    cron_secret = settings.CRON_SECRET or ""
    if not cron_secret:
        logger.error("CRON_SECRET is not set; refusing to run cron job")
        return False

    auth = request.headers.get("authorization") or ""
    if hmac.compare_digest(auth, f"Bearer {cron_secret}"):
        return True

    vercel = request.headers.get("x-vercel-cron-secret") or ""
    if hmac.compare_digest(vercel, cron_secret):
        return True

    return False


@router.get("/subscriptions/run")
async def run_subscriptions(request: Request):
    if not is_authorized(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if db.db is None:
        return {"status": "db_unavailable", "expired": 0}

    now = _utcnow()
    result = await db.db.tenants.update_many(
        {"$and": [
            {"subscription.current_period_end": {"$lt": now}},
            {"subscription.status": {"$in": ["trial", "active", "past_due"]}},
        ]},
        {"$set": {"subscription.status": "expired", "updated_at": _utcnow()}},
    )
    return {"status": "ok", "expired": result.modified_count}
