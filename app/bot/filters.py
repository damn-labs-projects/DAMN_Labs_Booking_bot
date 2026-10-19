from app.config import settings


def is_parent_bot(event) -> bool:
    """True only for updates delivered to the platform parent bot.

    The dispatcher is shared by the parent bot and every tenant bot, so
    platform-admin handlers must verify which bot received the update.
    """
    bot = getattr(event, "bot", None)
    token = getattr(bot, "token", None)
    return bool(token) and token == settings.PARENT_BOT_TOKEN


def is_child_bot(event) -> bool:
    """True for tenant bots (every bot except the platform parent bot)."""
    bot = getattr(event, "bot", None)
    token = getattr(bot, "token", None)
    return bool(token) and token != settings.PARENT_BOT_TOKEN
