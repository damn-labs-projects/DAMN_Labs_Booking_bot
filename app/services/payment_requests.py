import uuid
from datetime import datetime, timezone
from typing import Optional

from app.db.mongodb import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_request(
    tenant_id: str,
    tenant_name: str,
    contact: str,
    source: str = "owner",
) -> Optional[str]:
    if db is None or db.db is None:
        return None
    request_id = str(uuid.uuid4())[:8]
    await db.db.payment_requests.insert_one({
        "_id": request_id,
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "contact": contact,
        "source": source,
        "status": "new",
        "created_at": utcnow(),
    })
    return request_id


async def list_requests(status: str = "new") -> list:
    if db is None or db.db is None:
        return []
    cursor = db.db.payment_requests.find({"status": status}).sort("created_at", -1)
    return await cursor.to_list(length=50)


async def close_request(request_id: str, note: str = "") -> bool:
    if db is None or db.db is None:
        return False
    await db.db.payment_requests.update_one(
        {"_id": request_id},
        {"$set": {"status": "closed", "closed_at": utcnow(), "note": note}},
    )
    return True


async def admin_chat_ids() -> list:
    if db is None or db.db is None:
        return []
    cursor = db.db.admins.find({})
    docs = await cursor.to_list(length=50)
    ids = []
    for doc in docs:
        telegram_id = doc.get("telegram_id")
        if telegram_id:
            ids.append(int(telegram_id))
    return ids


async def notify_admins(bot, text: str) -> int:
    if bot is None:
        return 0
    sent = 0
    for chat_id in await admin_chat_ids():
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            sent += 1
        except Exception:
            continue
    return sent
