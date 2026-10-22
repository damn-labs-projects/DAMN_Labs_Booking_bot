import unittest
from types import SimpleNamespace

from aiogram.fsm.storage.base import StorageKey

from app.bot.storage import MongoStorage


class FakeFsmCollection:
    def __init__(self):
        self.docs = {}
        self.bulk_calls = 0
        self.find_calls = 0

    async def find_one(self, query):
        self.find_calls += 1
        return self.docs.get(query.get("_id"))

    async def bulk_write(self, operations, ordered=False):
        self.bulk_calls += 1
        for operation in operations:
            filter_ = operation._filter
            update = operation._doc
            doc = self.docs.setdefault(filter_["_id"], {})
            for field, value in update["$set"].items():
                doc[field] = value
        return SimpleNamespace(bulk_api_result={"nModified": len(operations)})


def _key() -> StorageKey:
    return StorageKey(bot_id=1, chat_id=2, user_id=3, thread_id=None, destiny="default")


class MongoStorageTests(unittest.TestCase):
    def setUp(self):
        from app.bot import storage as storage_module

        self.collection = FakeFsmCollection()
        self.old_db = storage_module.db
        storage_module.db = SimpleNamespace(db=SimpleNamespace(fsm_states=self.collection))
        self.storage = MongoStorage()

    def tearDown(self):
        from app.bot import storage as storage_module

        storage_module.db = self.old_db

    def test_writes_are_buffered_and_flushed_in_one_batch(self):
        import asyncio

        asyncio.run(self.storage.set_state(_key(), "BookingSG:selecting_service"))
        asyncio.run(self.storage.set_data(_key(), {"tenant_id": "t1"}))

        self.assertEqual(self.collection.bulk_calls, 0)

        asyncio.run(self.storage.flush())

        self.assertEqual(self.collection.bulk_calls, 1)
        doc = self.collection.docs["1:2:3:None:default"]
        self.assertEqual(doc["state"], "BookingSG:selecting_service")
        self.assertEqual(doc["data"], {"tenant_id": "t1"})

    def test_pending_state_is_read_without_database(self):
        import asyncio

        asyncio.run(self.storage.set_state(_key(), "BookingSG:entering_phone"))

        self.assertEqual(asyncio.run(self.storage.get_state(_key())), "BookingSG:entering_phone")
        self.assertEqual(self.collection.find_calls, 0)

    def test_flush_without_pending_writes_does_nothing(self):
        import asyncio

        asyncio.run(self.storage.flush())

        self.assertEqual(self.collection.bulk_calls, 0)

    def test_update_data_merges_pending_values(self):
        import asyncio

        asyncio.run(self.storage.set_data(_key(), {"tenant_id": "t1"}))
        merged = asyncio.run(self.storage.update_data(_key(), {"service_id": "s1"}))

        self.assertEqual(merged["tenant_id"], "t1")
        self.assertEqual(merged["service_id"], "s1")
        self.assertEqual(self.collection.find_calls, 0)


if __name__ == "__main__":
    unittest.main()
