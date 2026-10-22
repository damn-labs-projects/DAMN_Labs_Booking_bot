"""Cached tenant lookups by bot token.

Every Telegram update needs the tenant behind the bot token. On serverless
runtimes each MongoDB round trip costs 200-600 ms, so the tenant is kept in
memory for a short while and invalidated whenever the admin changes it.
"""

from typing import Optional

from app.db import mongodb
from app.services.cache import TTLCache

TENANT_TTL_SECONDS = 30.0

_tenant_cache = TTLCache(ttl=TENANT_TTL_SECONDS)


async def get_tenant_by_token(token: str) -> Optional[dict]:
    if not token:
        return None
    cached = _tenant_cache.get(token)
    if cached is not None:
        return cached
    if mongodb.db is None or mongodb.db.db is None:
        return None
    tenant = await mongodb.db.db.tenants.find_one({"bot_config.token": token})
    if tenant:
        _tenant_cache.set(token, tenant)
    return tenant


def invalidate_token(token: str) -> None:
    if token:
        _tenant_cache.delete(token)


def invalidate_tenant(tenant: Optional[dict]) -> None:
    if not tenant:
        return
    token = (tenant.get("bot_config") or {}).get("token")
    invalidate_token(token)


def clear_cache() -> None:
    _tenant_cache.clear()
