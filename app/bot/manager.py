import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BotCommand
from aiogram.types.error_event import ErrorEvent

from app.config import settings
from app.db.mongodb import db
from app.bot.storage import MongoStorage
from app.bot.handlers.booking import booking_router
from app.bot.handlers.parent import parent_router

logger = logging.getLogger(__name__)

class BotManager:
    def __init__(self):
        self.dp = Dispatcher(storage=MongoStorage())
        self.dp.include_router(booking_router)
        if parent_router is not None:
            self.dp.include_router(parent_router)
        self.dp.errors.register(self._on_error)
        self.bots: dict[str, Bot] = {}

    async def _on_error(self, event: ErrorEvent) -> None:
        logger.error(
            "Error while processing update %s: %s",
            event.update.update_id if event.update else "?",
            event.exception,
            exc_info=event.exception,
        )

    def get_bot_by_token(self, token: str) -> Bot:
        if token not in self.bots:
            self.bots[token] = Bot(token=token)
        return self.bots[token]

    async def register_tenant_webhook(self, token: str):
        bot = self.get_bot_by_token(token)
        webhook_url = f"{settings.BASE_WEBHOOK_URL}/api/v1/webhooks/telegram/{token}"
        try:
            current_webhook = await bot.get_webhook_info()
            if current_webhook.url != webhook_url:
                await bot.set_webhook(url=webhook_url)
                logger.info(f"Webhook set for bot token: ...{token[-6:]}")
        except TelegramRetryAfter as exc:
            logger.warning("Telegram flood control: waiting %s seconds before retrying webhook for %s", exc.retry_after, token)
            await asyncio.sleep(max(exc.retry_after, 2))
            await bot.set_webhook(url=webhook_url)
            logger.info(f"Webhook retried for bot token: ...{token[-6:]}")

    async def init_parent_bot(self):
        if not settings.PARENT_BOT_TOKEN:
            logger.warning("Parent bot token is not configured. Skipping parent bot webhook registration.")
            return

        try:
            bot = self.get_bot_by_token(settings.PARENT_BOT_TOKEN)
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Открыть панель / меню"),
                    BotCommand(command="login", description="Вход администратора"),
                    BotCommand(command="cancel", description="Отменить текущее действие"),
                ]
            )
        except Exception as e:
            logger.warning("Failed to set parent bot commands: %s", e)

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