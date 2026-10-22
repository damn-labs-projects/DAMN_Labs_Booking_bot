"""Cached service lists per tenant.

The client menu reads the tenant services on every `/start`, and each MongoDB
round trip costs 200-600 ms from serverless runtimes. Services change rarely,
so they are kept in memory for a minute and invalidated on every edit.
"""

from typing import List

from app.db import mongodb
from app.services.cache import TTLCache

SERVICES_TTL_SECONDS = 60.0

_cache = TTLCache(ttl=SERVICES_TTL_SECONDS)


async def get_active_services(tenant_id: str) -> List[dict]:
    if not tenant_id:
        return []
    cached = _cache.get(tenant_id)
    if cached is not None:
        return cached
    if mongodb.db is None or mongodb.db.db is None:
        return []
    cursor = mongodb.db.db.services.find({"tenant_id": tenant_id, "is_active": True})
    services = await cursor.to_list(length=50)
    _cache.set(tenant_id, services)
    return services


def invalidate(tenant_id: str) -> None:
    if tenant_id:
        _cache.delete(tenant_id)


def clear_cache() -> None:
    _cache.clear()
