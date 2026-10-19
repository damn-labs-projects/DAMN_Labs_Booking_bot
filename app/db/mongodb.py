import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import settings

logger = logging.getLogger(__name__)

class MongoDB:
    client: AsyncIOMotorClient = None
    db: AsyncIOMotorDatabase = None

db = MongoDB()


async def _create_index(collection_name: str, keys, **kwargs) -> None:
    try:
        await getattr(db.db, collection_name).create_index(keys, **kwargs)
    except Exception as exc:
        logger.error(
            "Failed to create index %s on %s: %s",
            keys,
            collection_name,
            exc,
        )


async def _ensure_indexes() -> None:
    await _create_index("tenants", "tenant_id", unique=True)
    await _create_index("tenants", "bot_config.token")
    await _create_index("services", [("tenant_id", 1), ("service_id", 1)], unique=True)
    await _create_index("staff", "tenant_id")
    await _create_index("appointments", [("tenant_id", 1), ("start_time", 1)], unique=True)
    await _create_index("appointments", [("tenant_id", 1), ("client_info.telegram_id", 1)])
    await _create_index("admins", "telegram_id", unique=True)
    await _create_index("fsm_states", "expires_at", expireAfterSeconds=0)


async def connect_to_mongo():
    try:
        db.client = AsyncIOMotorClient(settings.MONGODB_URL)
        db.db = db.client[settings.DATABASE_NAME]
    except Exception as exc:
        db.client = None
        db.db = None
        logger.warning(
            "MongoDB unavailable; continuing without database connection. "
            "Set a valid MONGODB_URL in production env to enable tenant and appointment storage. Error: %s",
            exc,
        )
        return False

    await _ensure_indexes()
    return True

async def close_mongo_connection():
    if db.client:
        db.client.close()
        db.client = None
        db.db = None