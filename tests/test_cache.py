import time
import unittest
from types import SimpleNamespace

from app.services.cache import TTLCache
from app.services import service_cache, tenant_cache


class TTLCacheTests(unittest.TestCase):
    def test_returns_stored_value(self):
        cache = TTLCache(ttl=5)
        cache.set("a", 1)
        self.assertEqual(cache.get("a"), 1)
        self.assertIsNone(cache.get("missing"))

    def test_expired_value_is_dropped(self):
        cache = TTLCache(ttl=0.01)
        cache.set("a", 1)
        time.sleep(0.05)
        self.assertIsNone(cache.get("a"))

    def test_delete_and_clear(self):
        cache = TTLCache(ttl=5)
        cache.set("a", 1)
        cache.delete("a")
        self.assertIsNone(cache.get("a"))
        cache.set("b", 2)
        cache.clear()
        self.assertIsNone(cache.get("b"))

    def test_eviction_keeps_cache_bounded(self):
        cache = TTLCache(ttl=5, max_items=3)
        for index in range(10):
            cache.set(index, index)
        self.assertLessEqual(len(cache._items), 3)


class FakeTenants:
    def __init__(self):
        self.calls = 0

    async def find_one(self, query):
        self.calls += 1
        return {"tenant_id": "t1", "name": "Bistro", "bot_config": {"token": "TOK"}}


class TenantCacheTests(unittest.TestCase):
    def setUp(self):
        from app.db import mongodb

        self.old_db = mongodb.db
        self.tenants = FakeTenants()
        mongodb.db = SimpleNamespace(db=SimpleNamespace(tenants=self.tenants))
        tenant_cache.clear_cache()

    def tearDown(self):
        from app.db import mongodb

        mongodb.db = self.old_db
        tenant_cache.clear_cache()

    def test_tenant_is_cached_between_calls(self):
        import asyncio

        first = asyncio.run(tenant_cache.get_tenant_by_token("TOK"))
        second = asyncio.run(tenant_cache.get_tenant_by_token("TOK"))

        self.assertEqual(first["tenant_id"], "t1")
        self.assertEqual(second["tenant_id"], "t1")
        self.assertEqual(self.tenants.calls, 1)

    def test_invalidate_forces_reload(self):
        import asyncio

        asyncio.run(tenant_cache.get_tenant_by_token("TOK"))
        tenant_cache.invalidate_tenant({"bot_config": {"token": "TOK"}})
        asyncio.run(tenant_cache.get_tenant_by_token("TOK"))

        self.assertEqual(self.tenants.calls, 2)


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return self._docs[:length] if length else self._docs


class FakeServices:
    def __init__(self):
        self.calls = 0

    def find(self, query):
        self.calls += 1
        docs = [{"service_id": "s1", "name": "Стрижка", "is_active": True}]
        return FakeCursor(docs)


class ServiceCacheTests(unittest.TestCase):
    def setUp(self):
        from app.db import mongodb

        self.old_db = mongodb.db
        self.services = FakeServices()
        mongodb.db = SimpleNamespace(db=SimpleNamespace(services=self.services))
        service_cache.clear_cache()

    def tearDown(self):
        from app.db import mongodb

        mongodb.db = self.old_db
        service_cache.clear_cache()

    def test_services_are_cached_and_invalidated(self):
        import asyncio

        first = asyncio.run(service_cache.get_active_services("t1"))
        second = asyncio.run(service_cache.get_active_services("t1"))
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(self.services.calls, 1)

        service_cache.invalidate("t1")
        asyncio.run(service_cache.get_active_services("t1"))
        self.assertEqual(self.services.calls, 2)


if __name__ == "__main__":
    unittest.main()
