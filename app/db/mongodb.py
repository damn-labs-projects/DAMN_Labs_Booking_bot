import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import settings

logger = logging.getLogger(__name__)

class MongoDB:
    client: AsyncIOMotorClient = None
    db: AsyncIOMotorDatabase = None

db = MongoDB()

async def connect_to_mongo():
    try:
        db.client = AsyncIOMotorClient(settings.MONGODB_URL)
        db.db = db.client[settings.DATABASE_NAME]

        await db.db.tenants.create_index("tenant_id", unique=True)
        await db.db.tenants.create_index("bot_config.token")
        await db.db.services.create_index([("tenant_id", 1), ("service_id", 1)], unique=True)
        await db.db.staff.create_index("tenant_id")
        await db.db.appointments.create_index([("tenant_id", 1), ("start_time", 1)])
        await db.db.appointments.create_index([("tenant_id", 1), ("client_info.telegram_id", 1)])
        return True
    except Exception as exc:
        db.client = None
        db.db = None
        logger.warning(
            "MongoDB unavailable; continuing without database connection. "
            "Set a valid MONGODB_URL in production env to enable tenant and appointment storage. Error: %s",
            exc,
        )
        return False

async def close_mongo_connection():
    if db.client:
        db.client.close()
        db.client = None
        db.db = None