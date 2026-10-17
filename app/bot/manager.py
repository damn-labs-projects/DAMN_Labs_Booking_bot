from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
import logging

from app.config import settings
from app.db.mongodb import db
from app.bot.handlers.booking import booking_router
from app.bot.handlers.parent import parent_router

logger = logging.getLogger(__name__)

class BotManager:
    def __init__(self):
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(booking_router)
        if parent_router is not None:
            self.dp.include_router(parent_router)
        self.bots: dict[str, Bot] = {}

    def get_bot_by_token(self, token: str) -> Bot:
        if token not in self.bots:
            self.bots[token] = Bot(token=token)
        return self.bots[token]

    async def register_tenant_webhook(self, token: str):
        bot = self.get_bot_by_token(token)
        webhook_url = f"{settings.BASE_WEBHOOK_URL}/api/v1/webhooks/telegram/{token}"
        current_webhook = await bot.get_webhook_info()
        if current_webhook.url != webhook_url:
            await bot.set_webhook(url=webhook_url)
            logger.info(f"Webhook set for bot token: ...{token[-6:]}")

    async def init_parent_bot(self):
        if not settings.PARENT_BOT_TOKEN:
            logger.warning("Parent bot token is not configured. Skipping parent bot webhook registration.")
            return

        try:
            await self.register_tenant_webhook(settings.PARENT_BOT_TOKEN)
            logger.info("Parent bot webhook registered successfully.")
        except Exception as e:
            logger.error(f"Failed to register parent bot webhook: {e}")

    async def init_active_tenants(self):
        if db.db is None:
            logger.warning("MongoDB is unavailable; skipping active tenant webhook registration.")
            return

        cursor = db.db.tenants.find({"bot_config.is_active": True})
        async for tenant in cursor:
            token = tenant.get("bot_config", {}).get("token")
            if token:
                try:
                    await self.register_tenant_webhook(token)
                except Exception as e:
                    logger.error(f"Failed to register webhook: {e}")

bot_manager = BotManager()