import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from pymongo import UpdateOne

from app.db.mongodb import db

logger = logging.getLogger(__name__)

STATE_TTL = timedelta(days=2)


def _state_to_str(state: StateType) -> Optional[str]:
    if state is None:
        return None
    return getattr(state, "state", state)


class MongoStorage(BaseStorage):
    """FSM storage backed by MongoDB with in-process fallback.

    MemoryStorage breaks on Vercel serverless: each invocation may land on a
    different instance, so multi-step wizards lose their state. This storage
    keeps state in the `fsm_states` collection and falls back to MemoryStorage
    only while MongoDB is unavailable.

    Writes are buffered in memory and flushed with a single bulk write at the
    end of the update: on serverless every MongoDB round trip costs 200-600 ms,
    and a single `/start` used to perform three separate writes.
    """

    def __init__(self) -> None:
        self._fallback = MemoryStorage()
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._flush_lock = asyncio.Lock()

    def _collection(self):
        if db is None or db.db is None:
            return None
        return db.db.fsm_states

    @staticmethod
    def _doc_id(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id}:{key.destiny}"

    def _queue(self, key: StorageKey, **fields: Any) -> None:
        doc_id = self._doc_id(key)
        payload = self._pending.setdefault(doc_id, {})
        payload.update(fields)
        payload["expires_at"] = datetime.utcnow() + STATE_TTL

    async def set_state(self, key: StorageKey, state: Optional[StateType] = None) -> None:
        collection = self._collection()
        state_str = _state_to_str(state)
        if collection is None:
            await self._fallback.set_state(key, state)
            return
        self._queue(key, state=state_str)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        pending = self._pending.get(self._doc_id(key))
        if pending is not None and "state" in pending:
            return pending.get("state")
        collection = self._collection()
        if collection is None:
            return await self._fallback.get_state(key)
        doc = await collection.find_one({"_id": self._doc_id(key)})
        return doc.get("state") if doc else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        collection = self._collection()
        if collection is None:
            await self._fallback.set_data(key, data)
            return
        self._queue(key, data=data)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        pending = self._pending.get(self._doc_id(key))
        if pending is not None and "data" in pending:
            return pending.get("data") or {}
        collection = self._collection()
        if collection is None:
            return await self._fallback.get_data(key)
        doc = await collection.find_one({"_id": self._doc_id(key)})
        return (doc or {}).get("data") or {}

    async def flush(self) -> None:
        """Persist buffered state changes with a single round trip."""
        collection = self._collection()
        if collection is None or not self._pending:
            return
        async with self._flush_lock:
            pending = self._pending
            self._pending = {}
        operations = [
            UpdateOne({"_id": doc_id}, {"$set": payload}, upsert=True)
            for doc_id, payload in pending.items()
        ]
        try:
            await collection.bulk_write(operations, ordered=False)
        except Exception as exc:
            logger.warning("Failed to flush FSM state: %s", exc)
            for doc_id, payload in pending.items():
                self._pending.setdefault(doc_id, {}).update(payload)

    async def close(self) -> None:
        await self.flush()
        await self._fallback.close()
