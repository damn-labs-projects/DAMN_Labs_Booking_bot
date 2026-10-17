from datetime import datetime, timedelta

try:
    from aiogram import F, Router, types
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.filters import CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
except ModuleNotFoundError:  # pragma: no cover - fallback for lightweight test runs
    class _FallbackTypes:
        class Message:
            pass

        class CallbackQuery:
            pass

        class ReplyKeyboardRemove:
            def __init__(self, *args, **kwargs):
                pass

    class F:
        data = None

    class Router:
        def message(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

        def callback_query(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    class _FallbackFSMContext:
        async def update_data(self, **kwargs):
            return None

        async def set_state(self, *args, **kwargs):
            return None

        async def get_data(self):
            return {}

        async def clear(self):
            return None

    class State:
        pass

    class StatesGroup:
        pass

    class _FallbackButton:
        def __init__(self, text, callback_data=None, **kwargs):
            self.text = text
            self.callback_data = callback_data

    class _FallbackMarkup:
        def __init__(self, inline_keyboard=None, **kwargs):
            self.inline_keyboard = inline_keyboard or []

    class types:
        Message = _FallbackTypes.Message
        CallbackQuery = _FallbackTypes.CallbackQuery
        ReplyKeyboardRemove = _FallbackTypes.ReplyKeyboardRemove

    class InlineKeyboardButton(_FallbackButton):
        pass

    class InlineKeyboardMarkup(_FallbackMarkup):
        pass

    class FSMContext(_FallbackFSMContext):
        pass

    class CommandStart:
        def __init__(self, *args, **kwargs):
            pass

    F = type("F", (), {"data": None})
    CommandStart = lambda *args, **kwargs: None

try:
    from app.db.mongodb import db
except ModuleNotFoundError:  # pragma: no cover
    db = None

try:
    TelegramBadRequest
except NameError:  # pragma: no cover
    class TelegramBadRequest(Exception):
        pass

parent_router = Router()


async def safe_edit_message(call: types.CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await call.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


class ParentTenantWizard(StatesGroup):
    waiting_name = State()
    waiting_category = State()
    waiting_owner_name = State()
    waiting_owner_phone = State()
    waiting_bot_token = State()
    waiting_confirmation = State()


def build_parent_demo_message(tenant_name: str = "Demo Salon") -> str:
    return (
        "🧩 Демо-режим родительского бота D.A.M.N Labs\n\n"
        f"Вы работаете в платформе D.A.M.N Booking Hub для бизнеса: {tenant_name}.\n"
        "Это родительский бот, через который администратор платформы управляет\n"
        "тенантами, подписками, настройками и запуском дочерних ботов.\n\n"
        "Доступные сценарии:\n"
        "• просмотр всех бизнесов\n"
        "• управление подписками Paddle\n"
        "• подключение новых Telegram-ботов по токену от @BotFather\n"
        "• демонстрация работы платформы без запуска отдельного клиента."
    )


def build_parent_tenant_summary(
    name: str,
    category: str,
    owner_name: str,
    owner_phone: str,
    bot_token: str,
) -> str:
    return (
        "📋 Подтверждение нового тенанта\n\n"
        f"Название: {name}\n"
        f"Категория: {category}\n"
        f"Владелец: {owner_name}\n"
        f"Телефон: {owner_phone}\n"
        f"Telegram bot token: {bot_token}\n\n"
        "После подтверждения данные будут сохранены в MongoDB и бот будет подключён в платформу."
    )


def build_parent_tenants_list(tenants: list[dict]) -> str:
    if not tenants:
        return "📋 Пока нет подключённых бизнесов. Добавьте первый тенант через кнопку \"Добавить тенант\"."

    lines = ["📋 Список бизнесов:"]
    for index, tenant in enumerate(tenants[:10], start=1):
        status = tenant.get("subscription", {}).get("status", "trial")
        bot_token = tenant.get("bot_config", {}).get("token", "—")
        lines.append(f"{index}. {tenant.get('name', 'Без названия')} | {status} | {bot_token}")
    return "\n".join(lines)


def build_parent_billing_summary(tenants: list[dict]) -> str:
    if not tenants:
        return "💳 Подписок пока нет. Первый trial будет активирован при создании нового тенанта."

    lines = ["💳 Статус подписок:"]
    for tenant in tenants[:10]:
        status = tenant.get("subscription", {}).get("status", "trial")
        deadline = tenant.get("subscription", {}).get("current_period_end")
        deadline_text = deadline.strftime("%d.%m.%Y") if deadline else "7 дней trial"
        lines.append(f"- {tenant.get('name', 'Без названия')}: {status} | до {deadline_text}")
    return "\n".join(lines)


def build_parent_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏢 Список бизнесов", callback_data="parent:tenants"),
                InlineKeyboardButton(text="💳 Подписки", callback_data="parent:billing"),
            ],
            [
                InlineKeyboardButton(text="➕ Добавить тенант", callback_data="parent:add_tenant"),
                InlineKeyboardButton(text="🧪 Демо", callback_data="parent:demo"),
            ],
        ]
    )


@parent_router.message(CommandStart())
async def parent_start(message: types.Message):
    tenant_name = "Demo Salon"
    text = build_parent_demo_message(tenant_name)
    await message.answer(text, reply_markup=build_parent_menu())


@parent_router.callback_query(F.data.startswith("parent:"))
async def parent_action(call: types.CallbackQuery, state: FSMContext):
    action = call.data.split(":", 1)[1]

    if action == "tenants":
        tenants = []
        if db is not None and hasattr(db, "db"):
            cursor = db.db.tenants.find({}).sort("created_at", -1)
            tenants = await cursor.to_list(length=20)
        await safe_edit_message(call, build_parent_tenants_list(tenants), build_parent_menu())
        await call.answer()
        return

    if action == "billing":
        tenants = []
        if db is not None and hasattr(db, "db"):
            cursor = db.db.tenants.find({}).sort("created_at", -1)
            tenants = await cursor.to_list(length=20)
        await safe_edit_message(call, build_parent_billing_summary(tenants), build_parent_menu())
        await call.answer()
        return

    if action == "demo":
        await safe_edit_message(
            call,
            "🧪 Демонстрация: родительский бот управляет платформой D.A.M.N Booking Hub и запускает tenant-боты для бизнеса без разработки и хостинга.",
            build_parent_menu(),
        )
        await call.answer()
        return

    if action == "add_tenant":
        await safe_edit_message(
            call,
            "📝 Создание нового тенанта. Введите название бизнеса:",
            types.ReplyKeyboardRemove(),
        )
        await state.set_state(ParentTenantWizard.waiting_name)
        await call.answer()
        return

    await safe_edit_message(call, "Команда не найдена.", build_parent_menu())
    await call.answer()


@parent_router.message(ParentTenantWizard.waiting_name)
async def parent_waiting_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("Введите категорию бизнеса (beauty / medical / education / auto):")
    await state.set_state(ParentTenantWizard.waiting_category)


@parent_router.message(ParentTenantWizard.waiting_category)
async def parent_waiting_category(message: types.Message, state: FSMContext):
    category = message.text.strip().lower()
    await state.update_data(category=category)
    await message.answer("Введите имя владельца:")
    await state.set_state(ParentTenantWizard.waiting_owner_name)


@parent_router.message(ParentTenantWizard.waiting_owner_name)
async def parent_waiting_owner_name(message: types.Message, state: FSMContext):
    await state.update_data(owner_name=message.text.strip())
    await message.answer("Введите телефон владельца:")
    await state.set_state(ParentTenantWizard.waiting_owner_phone)


@parent_router.message(ParentTenantWizard.waiting_owner_phone)
async def parent_waiting_owner_phone(message: types.Message, state: FSMContext):
    await state.update_data(owner_phone=message.text.strip())
    await message.answer("Введите токен Telegram-бота от @BotFather:")
    await state.set_state(ParentTenantWizard.waiting_bot_token)


@parent_router.message(ParentTenantWizard.waiting_bot_token)
async def parent_waiting_bot_token(message: types.Message, state: FSMContext):
    bot_token = message.text.strip()
    data = await state.get_data()
    summary = build_parent_tenant_summary(
        name=data["name"],
        category=data["category"],
        owner_name=data["owner_name"],
        owner_phone=data["owner_phone"],
        bot_token=bot_token,
    )

    await state.update_data(bot_token=bot_token)
    await message.answer(
        summary,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Подтвердить", callback_data="parent:confirm_tenant"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="parent:cancel_tenant"),
                ]
            ]
        ),
    )
    await state.set_state(ParentTenantWizard.waiting_confirmation)


@parent_router.callback_query(ParentTenantWizard.waiting_confirmation, F.data == "parent:confirm_tenant")
async def parent_confirm_tenant(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    timestamp = int(datetime.utcnow().timestamp())
    tenant_id = f"tenant_{data['name'].lower().replace(' ', '_')}_{timestamp}"
    trial_end = datetime.utcnow() + timedelta(days=7)

    tenant_document = {
        "tenant_id": tenant_id,
        "name": data["name"],
        "category": data["category"],
        "owner_info": {
            "name": data["owner_name"],
            "phone": data["owner_phone"],
            "telegram_id": getattr(call.from_user, "id", 0),
        },
        "bot_config": {
            "token": data["bot_token"],
            "username": "",
            "is_active": True,
        },
        "working_hours": {
            "start": "09:00",
            "end": "20:00",
            "slot_duration_minutes": 60,
        },
        "notification_chat_id": str(getattr(call.from_user, "id", 0)),
        "subscription": {
            "status": "trial",
            "paddle_subscription_id": None,
            "plan_id": "starter_5usd_month",
            "current_period_end": trial_end,
        },
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    if db is not None and hasattr(db, "db"):
        await db.db.tenants.insert_one(tenant_document)
    await call.message.edit_text(
        f"✅ Тенант создан.\n\nID: {tenant_id}\nНазвание: {data['name']}\nКатегория: {data['category']}\nTrial активен до: {trial_end.strftime('%d.%m.%Y %H:%M')}\nТокен: {data['bot_token']}"
    )
    await state.clear()


@parent_router.callback_query(ParentTenantWizard.waiting_confirmation, F.data == "parent:cancel_tenant")
async def parent_cancel_tenant(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text("❌ Создание тенанта отменено.", reply_markup=build_parent_menu())
    await state.clear()
