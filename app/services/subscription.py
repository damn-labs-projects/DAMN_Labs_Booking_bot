from datetime import datetime, timedelta, timezone
from typing import Optional

BLOCKED_SUBSCRIPTION_STATUSES = frozenset({"expired", "cancelled", "past_due"})
GRANT_OPTIONS = (7, 30, 90, 365)

STATUS_LABELS = {
    "trial": "пробный период",
    "active": "активна",
    "past_due": "просрочена оплата",
    "expired": "истекла",
    "cancelled": "отменена",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_blocked(tenant: dict | None) -> bool:
    if not isinstance(tenant, dict):
        return False
    subscription = tenant.get("subscription") or {}
    if not isinstance(subscription, dict):
        return False
    return subscription.get("status") in BLOCKED_SUBSCRIPTION_STATUSES


def status_of(tenant: dict | None) -> str:
    if not isinstance(tenant, dict):
        return "unknown"
    subscription = tenant.get("subscription") or {}
    if not isinstance(subscription, dict):
        return "unknown"
    return subscription.get("status") or "trial"


def status_label(tenant: dict | None) -> str:
    status = status_of(tenant)
    return STATUS_LABELS.get(status, status)


def period_end_of(tenant: dict | None):
    if not isinstance(tenant, dict):
        return None
    subscription = tenant.get("subscription") or {}
    if not isinstance(subscription, dict):
        return None
    return subscription.get("current_period_end")


def period_end_text(tenant: dict | None) -> str:
    end = period_end_of(tenant)
    if isinstance(end, datetime):
        return end.strftime("%d.%m.%Y")
    return "—"


def extended_period_end(tenant: dict | None, days: int, now: datetime | None = None) -> datetime:
    moment = now or utcnow()
    base = period_end_of(tenant)
    if not isinstance(base, datetime) or base <= moment:
        base = moment
    return base + timedelta(days=days)


async def grant_days(tenant_id: str, days: int, now: datetime | None = None) -> Optional[datetime]:
    from app.db.mongodb import db

    if db is None or db.db is None:
        return None
    tenant = await db.db.tenants.find_one({"tenant_id": tenant_id})
    if not tenant:
        return None

    moment = now or utcnow()
    new_end = extended_period_end(tenant, days, moment)
    await db.db.tenants.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": {
                "subscription.status": "active",
                "subscription.current_period_end": new_end,
                "subscription.updated_at": moment,
                "updated_at": moment,
            },
            "$push": {
                "subscription.grants": {
                    "days": days,
                    "at": moment,
                    "new_period_end": new_end,
                }
            },
        },
    )
    return new_end


async def set_status(tenant_id: str, status: str, now: datetime | None = None) -> bool:
    from app.db.mongodb import db

    if db is None or db.db is None:
        return False
    moment = now or utcnow()
    result = await db.db.tenants.update_one(
        {"tenant_id": tenant_id},
        {
            "$set": {
                "subscription.status": status,
                "subscription.updated_at": moment,
                "updated_at": moment,
            }
        },
    )
    return bool(getattr(result, "modified_count", 0))
