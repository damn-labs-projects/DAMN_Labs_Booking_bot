import re
from aiogram import Router, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from datetime import datetime, timedelta

from app.config import settings
from app.db.mongodb import db
from app.bot.handlers.parent import parent_start
from app.services.booking_service import BookingRejected, BookingService
from app.services import subscription
from app.services.tenant_texts import render

booking_router = Router()

PHONE_RE = re.compile(r"^\+?\d[\d\s\-()]{5,20}$")
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


async def safe_send(message: types.Message, text: str, **kwargs):
    try:
        return await message.answer(text, **kwargs)
    except TelegramBadRequest:
        kwargs.pop("parse_mode", None)
        return await message.answer(text, **kwargs)


class BookingSG(StatesGroup):
    selecting_service = State()
    selecting_date = State()
    selecting_slot = State()
    entering_phone = State()


async def _client_tenant(token: str):
    if db.db is None:
        return None
    return await db.db.tenants.find_one({"bot_config.token": token})


def _is_owner(tenant: dict, telegram_id) -> bool:
    ids = (tenant.get("owner") or {}).get("telegram_ids") or []
    if telegram_id in ids:
        return True
    owner_info = tenant.get("owner_info") or {}
    return owner_info.get("telegram_id") == telegram_id


def _service_by_id(services: list, service_id: str):
    for s in services:
        if s.get("service_id") == service_id:
            return s
    return None


async def _booking_gate(bot) -> str | None:
    tenant = await _client_tenant(bot.token)
    if not tenant:
        return "Ошибка: Заведение не найдено."
    if subscription.is_blocked(tenant):
        return "Обслуживание заведения временно приостановлено."
    return None


@booking_router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    if message.bot.token == settings.PARENT_BOT_TOKEN:
        await parent_start(message, state)
        return

    await state.clear()

    tenant = await _client_tenant(message.bot.token)
    if not tenant:
        await message.answer("Ошибка: Заведение не найдено.")
        return

    if subscription.is_blocked(tenant):
        await message.answer("Обслуживание заведения временно приостановлено.")
        return

    await state.update_data(tenant_id=tenant["tenant_id"])

    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if _is_owner(tenant, user_id):
        await message.answer(
            f"👋 Вы владелец бизнеса «{tenant.get('name', '')}».\n"
            "Панель управления — командой /owner или кнопкой ниже.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⚙️ Панель владельца", callback_data="own:menu")]]
            ),
        )

    services_cursor = db.db.services.find({"tenant_id": tenant["tenant_id"], "is_active": True})
    services = await services_cursor.to_list(length=50)

    welcome = render(tenant, "welcome")
    services_prompt = render(tenant, "services_prompt")

    if not services:
        await safe_send(message, f"{welcome}\n\nНа данный момент услуг нет.")
        return

    kb = [[InlineKeyboardButton(
        text=f"{s['name']} — {s['price']} {s['currency']} ({s['duration_minutes']} мин)",
        callback_data=f"srv:{s['service_id']}"
    )] for s in services]

    await safe_send(
        message,
        f"{welcome}\n{services_prompt}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )
    await state.set_state(BookingSG.selecting_service)


@booking_router.message(Command(commands=["help", "menu", "startup"]))
async def cmd_help(message: types.Message, state: FSMContext):
    if message.bot.token == settings.PARENT_BOT_TOKEN:
        await parent_start(message, state)
        return

    await message.answer(
        "📌 Доступные команды:\n"
        "/start — начать запись\n"
        "/my — мои записи\n"
        "/cancel — отменить текущее действие\n"
        "/help — помощь",
        parse_mode="Markdown"
    )


@booking_router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено. Чтобы начать заново — /start.")


@booking_router.message(Command("my"))
async def cmd_my(message: types.Message, state: FSMContext):
    if message.bot.token == settings.PARENT_BOT_TOKEN:
        return
    await state.clear()
    tenant = await _client_tenant(message.bot.token)
    if not tenant:
        await message.answer("Ошибка: Заведение не найдено.")
        return
    text, kb = await _my_appointments_view(tenant["tenant_id"], message.from_user.id)
    await message.answer(text, reply_markup=kb)


async def _my_appointments_view(tenant_id: str, telegram_id: int):
    appts = []
    services = {}
    if db.db is not None:
        for s in await db.db.services.find({"tenant_id": tenant_id}).to_list(length=200):
            services[s.get("service_id")] = s.get("name", "—")
        cursor = db.db.appointments.find({
            "tenant_id": tenant_id,
            "client_info.telegram_id": telegram_id,
            "status": {"$ne": "cancelled"},
            "start_time": {"$gte": datetime.now()},
        }).sort("start_time", 1)
        appts = await cursor.to_list(length=30)

    if not appts:
        return "📅 У вас нет предстоящих записей.", None

    lines = ["📅 Ваши записи:"]
    rows = []
    for a in appts:
        start = a.get("start_time")
        when = start.strftime("%d.%m %H:%M") if hasattr(start, "strftime") else str(start)
        lines.append(f"• {when} — {services.get(a.get('service_id'), '—')}")
        if a.get("appointment_id"):
            rows.append([InlineKeyboardButton(
                text=f"❌ Отменить {when}",
                callback_data=f"myc:{a['appointment_id']}",
            )])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@booking_router.callback_query(F.data.startswith("myc:"))
async def process_my_cancel(call: types.CallbackQuery):
    if call.bot.token == settings.PARENT_BOT_TOKEN:
        return
    await call.answer()
    appointment_id = call.data.split(":", 1)[1]
    tenant = await _client_tenant(call.bot.token)
    if not tenant or db.db is None:
        return
    appt = await db.db.appointments.find_one({
        "tenant_id": tenant["tenant_id"],
        "appointment_id": appointment_id,
        "client_info.telegram_id": call.from_user.id,
    })
    if not appt:
        await call.answer("Запись не найдена.", show_alert=True)
        return
    await db.db.appointments.update_one(
        {"tenant_id": tenant["tenant_id"], "appointment_id": appointment_id},
        {"$set": {"status": "cancelled"}},
    )
    text, kb = await _my_appointments_view(tenant["tenant_id"], call.from_user.id)
    await safe_edit(call, text, kb)


async def safe_edit(call: types.CallbackQuery, text: str, reply_markup=None):
    try:
        await call.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=reply_markup)


@booking_router.callback_query(BookingSG.selecting_service, F.data.startswith("srv:"))
async def process_service_choice(call: types.CallbackQuery, state: FSMContext):
    blocked = await _booking_gate(call.bot)
    if blocked:
        await call.answer(blocked, show_alert=True)
        return

    service_id = call.data.split(":", 1)[1]
    await state.update_data(service_id=service_id)

    today = datetime.now()
    kb = []
    for i in range(14):
        day = today + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        if i == 0:
            label = "Сегодня"
        elif i == 1:
            label = "Завтра"
        else:
            label = f"{WEEKDAYS[day.weekday()]} {day.strftime('%d.%m')}"
        kb.append([InlineKeyboardButton(text=f"📅 {label}", callback_data=f"date:{date_str}")])

    await call.message.edit_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(BookingSG.selecting_date)
    await call.answer()


@booking_router.callback_query(BookingSG.selecting_date, F.data.startswith("date:"))
async def process_date_choice(call: types.CallbackQuery, state: FSMContext):
    blocked = await _booking_gate(call.bot)
    if blocked:
        await call.answer(blocked, show_alert=True)
        return

    selected_date_str = call.data.split(":", 1)[1]
    await state.update_data(selected_date=selected_date_str)

    data = await state.get_data()
    slots = await BookingService.get_available_slots(data["tenant_id"], data["service_id"], selected_date_str)

    if selected_date_str == datetime.now().strftime("%Y-%m-%d"):
        now_hm = datetime.now().strftime("%H:%M")
        slots = [s for s in slots if s > now_hm]

    if not slots:
        await call.answer("На эту дату нет свободных слотов.", show_alert=True)
        return

    kb = []
    row = []
    for slot in slots:
        row.append(InlineKeyboardButton(text=slot, callback_data=f"time:{slot}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    await call.message.edit_text("Выберите время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(BookingSG.selecting_slot)
    await call.answer()


@booking_router.callback_query(BookingSG.selecting_slot, F.data.startswith("time:"))
async def process_slot_choice(call: types.CallbackQuery, state: FSMContext):
    tenant = await _client_tenant(call.bot.token)
    if not tenant:
        await call.answer("Ошибка: Заведение не найдено.", show_alert=True)
        return
    if subscription.is_blocked(tenant):
        await call.answer("Обслуживание заведения временно приостановлено.", show_alert=True)
        return

    slot_time = call.data.split(":", 1)[1]
    await state.update_data(selected_time=slot_time)

    btn = KeyboardButton(text="📱 Поделиться контактом", request_contact=True)
    kb = ReplyKeyboardMarkup(keyboard=[[btn]], resize_keyboard=True, one_time_keyboard=True)

    phone_prompt = render(tenant, "phone_prompt")

    await call.message.delete()
    await call.message.answer(phone_prompt, reply_markup=kb)
    await state.set_state(BookingSG.entering_phone)
    await call.answer()


@booking_router.message(BookingSG.entering_phone, F.contact)
async def process_phone_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    await _finalize_booking(message, state, phone)


@booking_router.message(BookingSG.entering_phone)
async def process_phone_text(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        await message.answer("Отправьте номер телефона или нажмите «📱 Поделиться контактом».")
        return
    if not PHONE_RE.match(raw):
        await message.answer(
            "Похоже, это не номер телефона. Отправьте номер, например +996700123456, "
            "или нажмите «📱 Поделиться контактом»."
        )
        return
    await _finalize_booking(message, state, raw)


async def _finalize_booking(message: types.Message, state: FSMContext, phone: str):
    full_name = message.from_user.full_name or message.from_user.first_name
    data = await state.get_data()
    tenant_id = data.get("tenant_id")
    service_id = data.get("service_id")
    selected_date = data.get("selected_date")
    selected_time = data.get("selected_time")

    if not all([tenant_id, service_id, selected_date, selected_time]):
        await state.clear()
        await message.answer("Сессия записи устарела. Начните заново — /start.")
        return

    try:
        appointment, tenant, service = await BookingService.create_appointment(
            tenant_id=tenant_id,
            service_id=service_id,
            telegram_id=message.from_user.id,
            full_name=full_name,
            phone=phone,
            date_str=selected_date,
            time_str=selected_time,
        )
    except BookingRejected as rejected:
        await state.clear()
        await safe_send(message, rejected.message)
        return

    confirmation_title = render(tenant, "confirmation_title")
    await safe_send(
        message,
        f"{confirmation_title}\n\n"
        f"📍 **Заведение:** {tenant['name']}\n"
        f"💇‍♂️ **Услуга:** {service['name']}\n"
        f"📅 **Дата и время:** {selected_date} {selected_time}\n"
        f"💰 **Стоимость:** {service['price']} {service['currency']}\n\n"
        f"Мои записи — /my.",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown",
    )

    notif_chat_id = tenant.get("notification_chat_id")
    if notif_chat_id:
        notify_title = render(tenant, "notify_title")
        try:
            await message.bot.send_message(
                chat_id=notif_chat_id,
                text=f"**{notify_title}**\n\n"
                     f"👤 **Клиент:** {full_name} ({phone})\n"
                     f"🛠 **Услуга:** {service['name']}\n"
                     f"🕒 **Время:** {selected_date} {selected_time}",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    await state.clear()
