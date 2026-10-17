import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

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
    """

    def __init__(self) -> None:
        self._fallback = MemoryStorage()

    def _collection(self):
        if db is None or db.db is None:
            return None
        return db.db.fsm_states

    @staticmethod
    def _doc_id(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id}:{key.destiny}"

    async def set_state(self, key: StorageKey, state: Optional[StateType] = None) -> None:
        collection = self._collection()
        state_str = _state_to_str(state)
        if collection is None:
            await self._fallback.set_state(key, state)
            return
        await collection.update_one(
            {"_id": self._doc_id(key)},
            {"$set": {"state": state_str, "expires_at": datetime.utcnow() + STATE_TTL}},
            upsert=True,
        )

    async def get_state(self, key: StorageKey) -> Optional[str]:
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
        await collection.update_one(
            {"_id": self._doc_id(key)},
            {"$set": {"data": data, "expires_at": datetime.utcnow() + STATE_TTL}},
            upsert=True,
        )

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        collection = self._collection()
        if collection is None:
            return await self._fallback.get_data(key)
        doc = await collection.find_one({"_id": self._doc_id(key)})
        return (doc or {}).get("data") or {}

    async def close(self) -> None:
        await self._fallback.close()
