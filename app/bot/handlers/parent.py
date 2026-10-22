from datetime import datetime, timedelta

from app.services import service_profiles as profiles

try:
    from aiogram import F, Router, types
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.filters import Command, CommandStart
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
    Command = lambda *args, **kwargs: None
    CommandStart = lambda *args, **kwargs: None

try:
    from app.db.mongodb import db
except ModuleNotFoundError:  # pragma: no cover
    db = None

try:
    from app.config import settings
except ModuleNotFoundError:  # pragma: no cover
    settings = None

try:
    from app.api.v1.pages import build_pay_link, payment_is_configured
except (ModuleNotFoundError, ImportError):  # pragma: no cover
    def payment_is_configured() -> bool:
        return False

    def build_pay_link(tenant_id: str) -> str:
        return f"/pay?tenant_id={tenant_id}"

try:
    TelegramBadRequest
except NameError:  # pragma: no cover
    class TelegramBadRequest(Exception):
        pass

parent_router = Router()

try:  # pragma: no cover - fallback router has no filter API
    from app.bot.filters import is_parent_bot

    parent_router.message.filter(is_parent_bot)
    parent_router.callback_query.filter(is_parent_bot)
except (AttributeError, ImportError):
    pass


def _is_inline_markup(reply_markup) -> bool:
    return isinstance(reply_markup, InlineKeyboardMarkup)


async def safe_edit_message(call: types.CallbackQuery, text: str, reply_markup=None) -> None:
    if not _is_inline_markup(reply_markup):
        reply_markup = None
    try:
        await call.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if "message is not modified" in message:
            return
        if "message to edit not found" in message or "query is too old" in message:
            try:
                await call.message.answer(text, reply_markup=reply_markup)
            except TelegramBadRequest:
                return
            return
        try:
            await call.message.answer(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            return


async def safe_answer(call: types.CallbackQuery, *args, **kwargs) -> None:
    try:
        await call.answer(*args, **kwargs)
    except TelegramBadRequest:
        return


async def is_admin(user_id) -> bool:
    if not user_id:
        return False
    if db is not None and db.db is not None:
        doc = await db.db.admins.find_one({"telegram_id": int(user_id)})
        if doc:
            return True
    return False


async def mark_admin(user_id) -> None:
    if user_id is None:
        return
    if db is not None and db.db is not None:
        await db.db.admins.update_one(
            {"telegram_id": int(user_id)},
            {"$set": {"telegram_id": int(user_id), "updated_at": datetime.utcnow()}},
            upsert=True,
        )


class AdminLoginSG(StatesGroup):
    waiting_password = State()


class ParentTenantWizard(StatesGroup):
    waiting_name = State()
    waiting_category = State()
    waiting_owner_name = State()
    waiting_owner_phone = State()
    waiting_owner_telegram_id = State()
    waiting_bot_token = State()
    waiting_confirmation = State()


class ParentDemo(StatesGroup):
    selecting_service = State()
    selecting_date = State()
    selecting_slot = State()


DEMO_SERVICES = [
    {"id": "s1", "name": "Стрижка", "price": 1500, "currency": "KGS", "duration": 60},
    {"id": "s2", "name": "Маникюр", "price": 1200, "currency": "KGS", "duration": 90},
    {"id": "s3", "name": "Массаж", "price": 2500, "currency": "KGS", "duration": 60},
]

DEMO_SLOTS = ["10:00", "11:30", "13:00", "15:30", "17:00", "18:30"]

CATEGORIES = [
    ("beauty", "💇 Beauty / красота"),
    ("medical", "🏥 Medical / медицина"),
    ("education", "🎓 Education / обучение"),
    ("auto", "🚗 Auto / авто"),
    ("other", "🏢 Другое"),
]


def build_parent_demo_message(tenant_name: str = "Demo Salon") -> str:
    return (
        "🧩 Демо-режим родительского бота D.A.M.N Labs\n\n"
        f"Вы работаете в платформе D.A.M.N Booking Hub для бизнеса: {tenant_name}.\n"
        "Это родительский бот, через который администратор платформы управляет\n"
        "бизнесами, подписками, настройками и запуском дочерних ботов.\n\n"
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
    owner_telegram_id: str,
    bot_token: str,
) -> str:
    return (
        "📋 Подтверждение нового бизнеса\n\n"
        f"Название: {name}\n"
        f"Категория: {category}\n"
        f"Владелец: {owner_name}\n"
        f"Телефон: {owner_phone}\n"
        f"Telegram ID владельца: {owner_telegram_id}\n"
        f"Telegram bot token: {bot_token}\n\n"
        "После подтверждения данные будут сохранены в MongoDB и бот будет подключён в платформу."
    )


def _mask_token(token: str) -> str:
    if not token or token == "—":
        return "—"
    if len(token) <= 8:
        return "…"
    return f"…{token[-4:]}"


def build_parent_tenants_list(tenants: list[dict]) -> str:
    if not tenants:
        return '📋 Список бизнесов: пока пусто. Добавьте первый бизнес через кнопку «Добавить бизнес».'

    lines = ["📋 Список бизнесов:"]
    for index, tenant in enumerate(tenants[:10], start=1):
        status = tenant.get("subscription", {}).get("status", "trial")
        bot_token = tenant.get("bot_config", {}).get("token", "—")
        lines.append(f"{index}. {tenant.get('name', 'Без названия')} | {status} | {_mask_token(bot_token)}")
    return "\n".join(lines)


def build_tenants_keyboard(tenants: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for tenant in tenants[:10]:
        name = tenant.get("name", "Без названия")
        rows.append([InlineKeyboardButton(
            text=f"🏢 {name}",
            callback_data=f"tadm:open:{tenant.get('tenant_id')}",
        )])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="tadm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_parent_billing_summary(tenants: list[dict]) -> str:
    if not tenants:
        return "💳 Подписок пока нет. Первый trial будет активирован при создании нового бизнеса."

    lines = ["💳 Статус подписок:"]
    from app.config import settings
    for tenant in tenants[:10]:
        status = tenant.get("subscription", {}).get("status", "trial")
        deadline = tenant.get("subscription", {}).get("current_period_end")
        deadline_text = deadline.strftime("%d.%m.%Y") if hasattr(deadline, "strftime") else "7 дней trial"
        lines.append(f"- {tenant.get('name', 'Без названия')}: {status} | до {deadline_text}")
    mode = getattr(settings, "PAYMENT_MODE", "manual") if settings else "manual"
    if mode == "paddle":
        lines.append("\nОплата онлайн через Paddle.")
    else:
        lines.append("\nОнлайн-оплата выключена. Нажмите на бизнес, чтобы продлить подписку вручную.")
    return "\n".join(lines)


def build_billing_keyboard(tenants: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for tenant in tenants[:10]:
        tenant_id = tenant.get("tenant_id") or ""
        if not tenant_id:
            continue
        name = tenant.get("name", "Без названия")[:24]
        status = tenant.get("subscription", {}).get("status", "trial")
        if status in ("active", "trial"):
            label = "💳" if status == "trial" else "✅"
        else:
            label = "⛔"
        rows.append([InlineKeyboardButton(
            text=f"{label} {name}",
            callback_data=f"parent:pay:{tenant_id}",
        )])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="parent:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_parent_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏢 Список бизнесов", callback_data="parent:tenants"),
                InlineKeyboardButton(text="💳 Подписки", callback_data="parent:billing"),
            ],
            [
                InlineKeyboardButton(text="➕ Добавить бизнес", callback_data="parent:add_tenant"),
                InlineKeyboardButton(text="🧪 Демо", callback_data="parent:demo"),
            ],
            [InlineKeyboardButton(text="📨 Запросы на оплату", callback_data="tadm:reqs")],
        ]
    )


def build_category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💇 Beauty / красота", callback_data="wiz:cat:beauty"),
         InlineKeyboardButton(text="🏥 Medical / медицина", callback_data="wiz:cat:medical")],
        [InlineKeyboardButton(text="🎓 Education / обучение", callback_data="wiz:cat:education"),
         InlineKeyboardButton(text="🚗 Auto / авто", callback_data="wiz:cat:auto")],
        [InlineKeyboardButton(text="☕ Cafe / кафе и рестораны", callback_data="wiz:cat:cafe")],
        [InlineKeyboardButton(text="🏢 Другое", callback_data="wiz:cat:other")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="wiz:back"),
         InlineKeyboardButton(text="❌ Отмена", callback_data="wiz:cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_wizard_keyboard(back: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if back:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="wiz:back")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="wiz:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


WIZARD_PROMPTS = [
    (ParentTenantWizard.waiting_name, "📝 Шаг 1/6. Введите название бизнеса:"),
    (ParentTenantWizard.waiting_category, "🏷 Шаг 2/6. Выберите категорию бизнеса:"),
    (ParentTenantWizard.waiting_owner_name, "👤 Шаг 3/6. Введите имя владельца:"),
    (ParentTenantWizard.waiting_owner_phone, "📞 Шаг 4/6. Введите телефон владельца:"),
    (
        ParentTenantWizard.waiting_owner_telegram_id,
        "🆔 Шаг 5/6. Введите Telegram ID владельца бизнеса.\n\n"
        "Владелец сможет войти в панель управления дочерним ботом командой /owner.\n"
        "Узнать ID можно через бота @userinfobot.",
    ),
    (ParentTenantWizard.waiting_bot_token, "🔑 Шаг 6/6. Введите токен Telegram-бота от @BotFather:"),
]


def build_demo_intro() -> str:
    return (
        "🧪 Демо-режим\n\n"
        "Здесь показано, как клиент записывается через дочернего бота вашего бизнеса.\n"
        "Пройдите шаги до конца — вы увидите тот же путь, что и реальный клиент.\n\n"
        "Шаг 1 из 3. Выберите услугу:"
    )


def build_demo_dates() -> InlineKeyboardMarkup:
    today = datetime.now()
    rows = []
    for i in range(3):
        day = today + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        label = "Сегодня" if i == 0 else ("Завтра" if i == 1 else day.strftime("%d.%m"))
        rows.append([InlineKeyboardButton(
            text=f"📅 {label} ({date_str})",
            callback_data=f"demo:date:{date_str}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="demo:back:services")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_demo_slots(date_str: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for slot in DEMO_SLOTS:
        row.append(InlineKeyboardButton(text=slot, callback_data=f"demo:time:{slot}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="demo:back:dates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_demo_confirm(date_str: str, slot: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Пройти заново", callback_data="parent:demo")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="demo:menu")],
        ]
    )


@parent_router.message(CommandStart())
async def parent_start(message: types.Message, state: FSMContext = None):
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if await is_admin(user_id):
        if state is not None:
            await state.clear()
        tenant_name = "Demo Salon"
        text = build_parent_demo_message(tenant_name)
        await message.answer(text, reply_markup=build_parent_menu())
        return

    if state is not None:
        await state.set_state(AdminLoginSG.waiting_password)
    await message.answer(
        "🔒 Это панель администратора D.A.M.N Booking Hub.\n\n"
        "Введите пароль администратора сообщением или командой /login <пароль>.\n"
        "Отмена — /cancel."
    )


@parent_router.message(Command(commands=["login", "admin", "admin_booking_bot"]))
async def parent_login_command(message: types.Message, state: FSMContext):
    import hmac

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        await _try_login(message, state, parts[1].strip())
        return

    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if await is_admin(user_id):
        await state.clear()
        await message.answer(build_parent_demo_message("Demo Salon"), reply_markup=build_parent_menu())
        return

    await state.set_state(AdminLoginSG.waiting_password)
    await message.answer("🔒 Введите пароль администратора (отмена — /cancel):")


@parent_router.message(Command("cancel"))
async def parent_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if await is_admin(user_id):
        await message.answer("🏠 Главное меню:", reply_markup=build_parent_menu())
    else:
        await message.answer("Отменено. Для входа отправьте /login.")


async def _try_login(message: types.Message, state: FSMContext, password: str) -> bool:
    import hmac

    expected = getattr(settings, "ADMIN_PASSWORD", "") or ""
    if not expected:
        await message.answer(
            "⚠️ Пароль администратора не настроен (ADMIN_PASSWORD). "
            "Задайте его в переменных окружения и попробуйте снова."
        )
        return False
    if hmac.compare_digest(password.encode(), expected.encode()):
        await mark_admin(getattr(message.from_user, "id", None))
        await state.clear()
        await message.answer(build_parent_demo_message("Demo Salon"), reply_markup=build_parent_menu())
        return True
    await message.answer("❌ Неверный пароль. Попробуйте ещё раз или /cancel.")
    return False


@parent_router.message(AdminLoginSG.waiting_password)
async def parent_login_password(message: types.Message, state: FSMContext):
    await _try_login(message, state, (message.text or "").strip())


@parent_router.callback_query(
    F.data.startswith("parent:"),
    ~F.data.in_({"parent:confirm_tenant", "parent:cancel_tenant"}),
)
async def parent_action(call: types.CallbackQuery, state: FSMContext):
    action = call.data.split(":", 1)[1]

    if not await is_admin(getattr(getattr(call, "from_user", None), "id", None)):
        await safe_answer(call, "🔒 Требуется вход в панель администратора.", show_alert=True)
        await state.set_state(AdminLoginSG.waiting_password)
        try:
            await call.message.answer("🔒 Введите пароль администратора (отмена — /cancel):")
        except TelegramBadRequest:
            pass
        return

    if action == "tenants":
        tenants = await _load_tenants()
        await safe_edit_message(call, build_parent_tenants_list(tenants), build_tenants_keyboard(tenants))
        await safe_answer(call)
        return

    if action == "billing":
        tenants = await _load_tenants()
        await safe_edit_message(call, build_parent_billing_summary(tenants), build_billing_keyboard(tenants))
        await safe_answer(call)
        return

    if action == "menu":
        await safe_edit_message(call, "🏠 Главное меню", build_parent_menu())
        await safe_answer(call)
        return

    if action.startswith("pay:"):
        tenant_id = action.split(":", 1)[1].strip()
        if not tenant_id:
            await safe_edit_message(call, "Не указан бизнес для оплаты.", build_parent_menu())
            await safe_answer(call)
            return

        payment_mode = getattr(settings, "PAYMENT_MODE", "manual") if settings else "manual"
        if payment_mode != "paddle":
            from app.bot.handlers.tenant_admin import (
                build_subscription_kb,
                build_subscription_screen,
            )

            tenant = None
            if db is not None and db.db is not None:
                tenant = await db.db.tenants.find_one({"tenant_id": tenant_id})
            if not tenant:
                await safe_edit_message(call, "Бизнес не найден.", build_parent_menu())
                await safe_answer(call)
                return
            await safe_edit_message(
                call,
                build_subscription_screen(tenant),
                build_subscription_kb(tenant_id),
            )
            await safe_answer(call)
            return

        if not payment_is_configured():
            support = settings.SUPPORT_CONTACT if settings else ""
            await safe_edit_message(
                call,
                "💳 Оплата пока не подключена.\n"
                "Напишите в поддержку — подключим оплату вручную."
                + (f"\n\n{support}" if support else ""),
                build_parent_menu(),
            )
            await safe_answer(call)
            return
        link = build_pay_link(tenant_id)
        if not link.startswith("http"):
            await safe_edit_message(
                call,
                "💳 Не задан BASE_WEBHOOK_URL — ссылка на оплату недоступна.",
                build_parent_menu(),
            )
            await safe_answer(call)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить подписку", url=link)],
            [InlineKeyboardButton(text="⬅️ К подпискам", callback_data="parent:billing")],
        ])
        await safe_edit_message(
            call,
            f"💳 Оплата подписки\nБизнес: {tenant_id}\n\n"
            "Оплата проходит на защищённой странице. После оплаты подписка "
            "активируется в течение минуты.",
            kb,
        )
        await safe_answer(call)
        return

    if action == "demo":
        await state.set_state(ParentDemo.selecting_service)
        await safe_edit_message(call, build_demo_intro(), build_demo_services_keyboard())
        await safe_answer(call)
        return

    if action == "add_tenant":
        await state.clear()
        await state.set_state(ParentTenantWizard.waiting_name)
        await safe_edit_message(
            call,
            WIZARD_PROMPTS[0][1],
            build_wizard_keyboard(back=False),
        )
        await safe_answer(call)
        return

    await safe_edit_message(call, "Команда не найдена.", build_parent_menu())
    await safe_answer(call)


async def _load_tenants() -> list[dict]:
    if db is None or db.db is None:
        return []
    cursor = db.db.tenants.find({}).sort("created_at", -1)
    return await cursor.to_list(length=20)


def build_demo_services_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{service['name']} — {service['price']} {service['currency']} ({service['duration']} мин)",
            callback_data=f"demo:srv:{service['id']}",
        )]
        for service in DEMO_SERVICES
    ]
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="demo:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@parent_router.callback_query(ParentDemo.selecting_service, F.data.startswith("demo:srv:"))
async def parent_demo_service(call: types.CallbackQuery, state: FSMContext):
    service_id = call.data.split(":")[2]
    await state.update_data(service_id=service_id)
    await state.set_state(ParentDemo.selecting_date)
    service = next((s for s in DEMO_SERVICES if s["id"] == service_id), None)
    name = service["name"] if service else "услуга"
    await safe_edit_message(
        call,
        f"✅ Услуга: {name}\n\nШаг 2 из 3. Выберите дату:",
        build_demo_dates(),
    )
    await safe_answer(call)


@parent_router.callback_query(ParentDemo.selecting_date, F.data.startswith("demo:date:"))
async def parent_demo_date(call: types.CallbackQuery, state: FSMContext):
    date_str = call.data.split(":", 2)[2]
    await state.update_data(selected_date=date_str)
    await state.set_state(ParentDemo.selecting_slot)
    await safe_edit_message(
        call,
        f"📅 Дата: {date_str}\n\nШаг 3 из 3. Выберите время:",
        build_demo_slots(date_str),
    )
    await safe_answer(call)


@parent_router.callback_query(ParentDemo.selecting_slot, F.data.startswith("demo:time:"))
async def parent_demo_slot(call: types.CallbackQuery, state: FSMContext):
    slot = call.data.split(":", 2)[2]
    data = await state.get_data()
    service_id = data.get("service_id")
    date_str = data.get("selected_date", "")
    service = next((s for s in DEMO_SERVICES if s["id"] == service_id), None)

    service_name = service["name"] if service else "Услуга"
    price = f"{service['price']} {service['currency']}" if service else "—"

    await state.clear()
    await safe_edit_message(
        call,
        "🎉 Демо-запись создана!\n\n"
        f"📍 Заведение: Demo Salon\n"
        f"💇 Услуга: {service_name}\n"
        f"📅 Дата и время: {date_str} {slot}\n"
        f"💰 Стоимость: {price}\n\n"
        "Именно так клиент подтверждает запись в дочернем боте: данные сохраняются в базе, "
        "а владелец получает уведомление в своём Telegram.",
        build_demo_confirm(date_str, slot),
    )
    await safe_answer(call, "Запись оформлена")


@parent_router.callback_query(F.data == "demo:menu")
async def parent_demo_menu(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit_message(call, build_parent_demo_message("Demo Salon"), build_parent_menu())
    await safe_answer(call)


@parent_router.callback_query(F.data.startswith("demo:back:"))
async def parent_demo_back(call: types.CallbackQuery, state: FSMContext):
    target = call.data.split(":", 2)[2]
    if target == "services":
        await state.set_state(ParentDemo.selecting_service)
        await safe_edit_message(call, build_demo_intro(), build_demo_services_keyboard())
    elif target == "dates":
        await state.set_state(ParentDemo.selecting_date)
        await safe_edit_message(call, "Шаг 2 из 3. Выберите дату:", build_demo_dates())
    await safe_answer(call)


@parent_router.message(ParentTenantWizard.waiting_name)
async def parent_waiting_name(message: types.Message, state: FSMContext):
    if not await is_admin(getattr(getattr(message, "from_user", None), "id", None)):
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым.", reply_markup=build_wizard_keyboard(back=False))
        return
    await state.update_data(name=name)
    await state.set_state(ParentTenantWizard.waiting_category)
    await message.answer(WIZARD_PROMPTS[1][1], reply_markup=build_category_keyboard())


@parent_router.callback_query(F.data.startswith("wiz:cat:"))
async def parent_wizard_category(call: types.CallbackQuery, state: FSMContext):
    if not await is_admin(getattr(getattr(call, "from_user", None), "id", None)):
        await safe_answer(call)
        return
    category = call.data.split(":", 2)[2]
    await state.update_data(category=category)
    await state.set_state(ParentTenantWizard.waiting_owner_name)
    await safe_edit_message(call, WIZARD_PROMPTS[2][1], build_wizard_keyboard())
    await safe_answer(call)


@parent_router.message(ParentTenantWizard.waiting_category)
async def parent_waiting_category(message: types.Message, state: FSMContext):
    if not await is_admin(getattr(getattr(message, "from_user", None), "id", None)):
        await state.clear()
        return
    category = (message.text or "").strip().lower()
    if not category:
        await message.answer("Выберите категорию кнопкой ниже.", reply_markup=build_category_keyboard())
        return
    await state.update_data(category=category)
    await state.set_state(ParentTenantWizard.waiting_owner_name)
    await message.answer(WIZARD_PROMPTS[2][1], reply_markup=build_wizard_keyboard())


@parent_router.message(ParentTenantWizard.waiting_owner_name)
async def parent_waiting_owner_name(message: types.Message, state: FSMContext):
    if not await is_admin(getattr(getattr(message, "from_user", None), "id", None)):
        await state.clear()
        return
    owner_name = (message.text or "").strip()
    if not owner_name:
        await message.answer("Имя не может быть пустым.", reply_markup=build_wizard_keyboard())
        return
    await state.update_data(owner_name=owner_name)
    await message.answer(WIZARD_PROMPTS[3][1], reply_markup=build_wizard_keyboard())
    await state.set_state(ParentTenantWizard.waiting_owner_phone)


@parent_router.message(ParentTenantWizard.waiting_owner_phone)
async def parent_waiting_owner_phone(message: types.Message, state: FSMContext):
    if not await is_admin(getattr(getattr(message, "from_user", None), "id", None)):
        await state.clear()
        return
    phone = (message.text or "").strip()
    if not phone:
        await message.answer("Телефон не может быть пустым.", reply_markup=build_wizard_keyboard())
        return
    await state.update_data(owner_phone=phone)
    await message.answer(WIZARD_PROMPTS[4][1], reply_markup=build_wizard_keyboard())
    await state.set_state(ParentTenantWizard.waiting_owner_telegram_id)


@parent_router.message(ParentTenantWizard.waiting_owner_telegram_id)
async def parent_waiting_owner_telegram_id(message: types.Message, state: FSMContext):
    if not await is_admin(getattr(getattr(message, "from_user", None), "id", None)):
        await state.clear()
        return
    raw = (message.text or "").strip()
    normalized = raw[1:] if raw.startswith("-") else raw
    if not normalized.isdigit():
        await message.answer(
            "Telegram ID должен состоять из цифр. Например: 123456789.\n"
            "Узнать свой ID можно у @userinfobot.",
            reply_markup=build_wizard_keyboard(),
        )
        return
    await state.update_data(owner_telegram_id=int(normalized))
    await message.answer(WIZARD_PROMPTS[5][1], reply_markup=build_wizard_keyboard())
    await state.set_state(ParentTenantWizard.waiting_bot_token)


@parent_router.message(ParentTenantWizard.waiting_bot_token)
async def parent_waiting_bot_token(message: types.Message, state: FSMContext):
    if not await is_admin(getattr(getattr(message, "from_user", None), "id", None)):
        await state.clear()
        return
    bot_token = (message.text or "").strip()
    data = await state.get_data()
    required = ("name", "category", "owner_name", "owner_phone", "owner_telegram_id")
    if any(not data.get(key) for key in required):
        await state.clear()
        await message.answer(
            "⏳ Сессия создания бизнеса устарела. Начните заново.",
            reply_markup=build_parent_menu(),
        )
        return
    if not bot_token or ":" not in bot_token:
        await message.answer(
            "Токен выглядит неверно. Формат: 123456789:AA... (от @BotFather).",
            reply_markup=build_wizard_keyboard(),
        )
        return
    summary = build_parent_tenant_summary(
        name=data["name"],
        category=data["category"],
        owner_name=data["owner_name"],
        owner_phone=data["owner_phone"],
        owner_telegram_id=str(data["owner_telegram_id"]),
        bot_token=bot_token,
    )

    await state.update_data(bot_token=bot_token)
    await message.answer(
        summary,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Подтвердить", callback_data="parent:confirm_tenant"),
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="wiz:back"),
                ],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="wiz:cancel")],
            ]
        ),
    )
    await state.set_state(ParentTenantWizard.waiting_confirmation)


WIZARD_STATE_INDEX = {
    ParentTenantWizard.waiting_name.state: 0,
    ParentTenantWizard.waiting_category.state: 1,
    ParentTenantWizard.waiting_owner_name.state: 2,
    ParentTenantWizard.waiting_owner_phone.state: 3,
    ParentTenantWizard.waiting_owner_telegram_id.state: 4,
    ParentTenantWizard.waiting_bot_token.state: 5,
    ParentTenantWizard.waiting_confirmation.state: 6,
}


async def _ask_wizard_step(call: types.CallbackQuery, state: FSMContext, index: int) -> None:
    if index < 0:
        await state.clear()
        await safe_edit_message(call, build_parent_demo_message("Demo Salon"), build_parent_menu())
        return
    if index == 1:
        await state.set_state(ParentTenantWizard.waiting_category)
        await safe_edit_message(call, WIZARD_PROMPTS[1][1], build_category_keyboard())
        return
    target_state, prompt = WIZARD_PROMPTS[index]
    await state.set_state(target_state)
    await safe_edit_message(call, prompt, build_wizard_keyboard())


@parent_router.callback_query(F.data == "wiz:back")
async def parent_wizard_back(call: types.CallbackQuery, state: FSMContext):
    current = await state.get_state()
    index = WIZARD_STATE_INDEX.get(current, 0)
    await _ask_wizard_step(call, state, index - 1)
    await safe_answer(call)


@parent_router.callback_query(F.data == "wiz:cancel")
async def parent_wizard_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit_message(call, "❌ Создание бизнеса отменено.", build_parent_menu())
    await safe_answer(call)


@parent_router.callback_query(ParentTenantWizard.waiting_confirmation, F.data == "parent:confirm_tenant")
async def parent_confirm_tenant(call: types.CallbackQuery, state: FSMContext):
    if not await is_admin(getattr(getattr(call, "from_user", None), "id", None)):
        await state.clear()
        await safe_answer(call)
        return

    data = await state.get_data()
    required = ("name", "category", "owner_name", "owner_phone", "owner_telegram_id", "bot_token")
    if any(not data.get(key) for key in required):
        await state.clear()
        await safe_edit_message(call, "⏳ Сессия создания бизнеса устарела. Начните заново.", build_parent_menu())
        await safe_answer(call)
        return

    timestamp = int(datetime.utcnow().timestamp())
    tenant_id = f"tenant_{data['name'].lower().replace(' ', '_')}_{timestamp}"
    trial_end = datetime.utcnow() + timedelta(days=7)
    owner_telegram_id = int(data["owner_telegram_id"])

    tenant_document = {
        "tenant_id": tenant_id,
        "name": data["name"],
        "category": profiles.normalize_category(data["category"]),
        "owner_info": {
            "name": data["owner_name"],
            "phone": data["owner_phone"],
            "telegram_id": owner_telegram_id,
        },
        "owner": {
            "telegram_ids": [owner_telegram_id],
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
        "notification_chat_id": str(owner_telegram_id),
        "subscription": {
            "status": "trial",
            "paddle_subscription_id": None,
            "plan_id": "starter_5usd_month",
            "current_period_end": trial_end,
        },
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    saved = False
    if db is not None and db.db is not None:
        await db.db.tenants.insert_one(tenant_document)
        saved = True

    webhook_note = ""
    bot_link = ""
    if saved:
        try:
            from app.bot.manager import bot_manager

            bot = bot_manager.get_bot_by_token(data["bot_token"])
            await bot_manager.register_tenant_webhook(data["bot_token"])
            webhook_note = "\n🔗 Webhook дочернего бота зарегистрирован."
            try:
                me = await bot.get_me()
                if me.username:
                    bot_link = f"\n🤖 Дочерний бот: https://t.me/{me.username}"
                    await db.db.tenants.update_one(
                        {"tenant_id": tenant_id},
                        {"$set": {"bot_config.username": me.username}},
                    )
                    try:
                        await bot.send_message(
                            owner_telegram_id,
                            f"👋 Ваш бизнес «{data['name']}» подключён к D.A.M.N Booking Hub.\n"
                            "Откройте это меню-кнопку ниже или отправьте /owner, чтобы управлять "
                            "услугами, часами и записями.",
                            reply_markup=InlineKeyboardMarkup(
                                inline_keyboard=[[InlineKeyboardButton(text="⚙️ Панель владельца", callback_data="own:menu")]]
                            ),
                        )
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            webhook_note = "\n⚠️ Webhook будет зарегистрирован при следующем старте сервера."
    else:
        webhook_note = "\n⚠️ База данных недоступна — бизнес не сохранён. Попробуйте позже."

    await safe_edit_message(
        call,
        f"✅ Бизнес создан.\n\nID: {tenant_id}\nНазвание: {data['name']}\n"
        f"Категория: {profiles.category_label(data['category'])}\n"
        f"Trial активен до: {trial_end.strftime('%d.%m.%Y %H:%M')}\nТариф: $5/мес{bot_link}{webhook_note}",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛠 Настроить бизнес", callback_data=f"tadm:open:{tenant_id}")],
                [InlineKeyboardButton(text="🏠 В меню", callback_data="tadm:menu")],
            ]
        ),
    )
    await safe_answer(call)
    await state.clear()


@parent_router.callback_query(F.data == "parent:confirm_tenant")
async def parent_confirm_tenant_fallback(call: types.CallbackQuery, state: FSMContext):
    await safe_answer(call, "Сессия устарела. Откройте /start и начните заново.", show_alert=True)


@parent_router.callback_query(F.data == "parent:cancel_tenant")
async def parent_cancel_tenant_fallback(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit_message(call, "❌ Создание бизнеса отменено.", build_parent_menu())
    await safe_answer(call)
