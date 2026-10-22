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
from app.services import service_cache
from app.services import tenant_cache
from app.services import service_profiles as profiles
from app.services.tenant_texts import render
from app import i18n

booking_router = Router()

PHONE_RE = re.compile(r"^\+?\d[\d\s\-()]{5,20}$")


async def safe_send(message: types.Message, text: str, **kwargs):
    try:
        return await message.answer(text, **kwargs)
    except TelegramBadRequest:
        kwargs.pop("parse_mode", None)
        return await message.answer(text, **kwargs)


class BookingSG(StatesGroup):
    selecting_language = State()
    selecting_service = State()
    selecting_date = State()
    selecting_slot = State()
    entering_phone = State()


async def _client_tenant(token: str):
    return await tenant_cache.get_tenant_by_token(token)


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


async def _user_lang(message_or_call) -> str:
    user = getattr(message_or_call, "from_user", None)
    telegram_id = getattr(user, "id", None)
    lang = await i18n.get_user_lang(telegram_id)
    return lang or i18n.DEFAULT_LANG


def _language_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"lang:{code}")]
        for code, label in i18n.language_buttons()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_language(message: types.Message, state: FSMContext | None = None):
    await message.answer(i18n.PICKER_TEXT, reply_markup=_language_keyboard())
    if state is not None:
        await state.set_state(BookingSG.selecting_language)


async def _owner_lang(tenant: dict) -> str:
    ids = (tenant.get("owner") or {}).get("telegram_ids") or []
    owner_info = tenant.get("owner_info") or {}
    candidates = list(ids)
    if owner_info.get("telegram_id"):
        candidates.append(owner_info["telegram_id"])
    for candidate in candidates:
        lang = await i18n.get_user_lang(candidate)
        if lang:
            return lang
    return i18n.DEFAULT_LANG


@booking_router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    if message.bot.token == settings.PARENT_BOT_TOKEN:
        await parent_start(message, state)
        return

    await state.clear()

    tenant = await _client_tenant(message.bot.token)
    stored_lang = await i18n.get_user_lang(getattr(message.from_user, "id", None))
    lang = stored_lang or i18n.DEFAULT_LANG

    if not tenant:
        await message.answer(i18n.t(lang, "error.tenant_not_found"))
        return

    if subscription.is_blocked(tenant):
        await message.answer(i18n.t(lang, "error.paused"))
        return

    await state.update_data(tenant_id=tenant["tenant_id"])

    if not stored_lang:
        await _ask_language(message, state)
        return

    await _show_start_menu(message, tenant, lang, state)


async def _show_start_menu(message: types.Message, tenant: dict, lang: str, state: FSMContext):
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if _is_owner(tenant, user_id):
        await message.answer(
            i18n.t(lang, "start.owner", name=tenant.get("name", "")),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(
                    text=i18n.t(lang, "start.owner_button"),
                    callback_data="own:menu",
                )]]
            ),
        )

    services = await service_cache.get_active_services(tenant["tenant_id"])

    welcome = render(tenant, "welcome", lang=lang)
    services_prompt = render(tenant, "services_prompt", lang=lang)

    if not services:
        await safe_send(message, f"{welcome}\n\n{i18n.t(lang, 'start.no_services')}")
        return

    kb = [[InlineKeyboardButton(
        text=i18n.service_button(s, lang),
        callback_data=f"srv:{s['service_id']}"
    )] for s in services]

    await safe_send(
        message,
        f"{welcome}\n{services_prompt}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )
    await state.set_state(BookingSG.selecting_service)


@booking_router.callback_query(F.data.startswith("lang:"))
async def process_language_choice(call: types.CallbackQuery, state: FSMContext):
    if call.bot.token == settings.PARENT_BOT_TOKEN:
        return

    code = call.data.split(":", 1)[1]
    if not i18n.is_supported(code):
        await call.answer(i18n.t(i18n.DEFAULT_LANG, "lang.choose"), show_alert=True)
        return

    await i18n.set_user_lang(call.from_user.id, code)
    await call.answer()

    tenant = await _client_tenant(call.bot.token)
    if not tenant:
        await safe_edit(call, i18n.t(code, "error.tenant_not_found"))
        return

    await state.update_data(tenant_id=tenant["tenant_id"])
    await safe_edit(call, i18n.t(code, "lang.changed"))
    await _show_start_menu(call.message, tenant, code, state)


@booking_router.callback_query(F.data == "langpick")
async def process_language_pick(call: types.CallbackQuery, state: FSMContext):
    if call.bot.token == settings.PARENT_BOT_TOKEN:
        return
    await _ask_language(call.message, state)


@booking_router.message(Command(commands=["language", "lang", "til"]))
async def cmd_language(message: types.Message, state: FSMContext):
    if message.bot.token == settings.PARENT_BOT_TOKEN:
        return
    await _ask_language(message, state)


@booking_router.message(Command(commands=["help", "menu", "startup"]))
async def cmd_help(message: types.Message, state: FSMContext):
    if message.bot.token == settings.PARENT_BOT_TOKEN:
        await parent_start(message, state)
        return

    lang = await _user_lang(message)
    await message.answer(
        i18n.t(lang, "help.commands"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=i18n.t(lang, "lang.button"),
            callback_data="langpick",
        )]]),
        parse_mode="Markdown",
    )


@booking_router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    lang = await _user_lang(message)
    await state.clear()
    await message.answer(i18n.t(lang, "cancel.done"))


@booking_router.message(Command("my"))
async def cmd_my(message: types.Message, state: FSMContext):
    if message.bot.token == settings.PARENT_BOT_TOKEN:
        return
    lang = await _user_lang(message)
    await state.clear()
    tenant = await _client_tenant(message.bot.token)
    if not tenant:
        await message.answer(i18n.t(lang, "error.tenant_not_found"))
        return
    text, kb = await _my_appointments_view(tenant["tenant_id"], message.from_user.id, lang)
    await message.answer(text, reply_markup=kb)


async def _my_appointments_view(tenant_id: str, telegram_id: int, lang: str = i18n.DEFAULT_LANG):
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
        return i18n.t(lang, "my.empty"), None

    lines = [i18n.t(lang, "my.title")]
    rows = []
    for a in appts:
        start = a.get("start_time")
        when = start.strftime("%d.%m %H:%M") if hasattr(start, "strftime") else str(start)
        lines.append(f"• {when} — {services.get(a.get('service_id'), '—')}")
        if a.get("appointment_id"):
            rows.append([InlineKeyboardButton(
                text=i18n.t(lang, "my.cancel_button", when=when),
                callback_data=f"myc:{a['appointment_id']}",
            )])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@booking_router.callback_query(F.data.startswith("myc:"))
async def process_my_cancel(call: types.CallbackQuery):
    if call.bot.token == settings.PARENT_BOT_TOKEN:
        return
    lang = await _user_lang(call)
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
        await call.answer(i18n.t(lang, "my.not_found"), show_alert=True)
        return
    await db.db.appointments.update_one(
        {"tenant_id": tenant["tenant_id"], "appointment_id": appointment_id},
        {"$set": {"status": "cancelled"}},
    )
    text, kb = await _my_appointments_view(tenant["tenant_id"], call.from_user.id, lang)
    await safe_edit(call, text, kb)


async def safe_edit(call: types.CallbackQuery, text: str, reply_markup=None):
    try:
        await call.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await call.message.answer(text, reply_markup=reply_markup)


@booking_router.callback_query(BookingSG.selecting_service, F.data.startswith("srv:"))
async def process_service_choice(call: types.CallbackQuery, state: FSMContext):
    lang = await _user_lang(call)
    tenant = await _client_tenant(call.bot.token)
    if not tenant:
        await call.answer(i18n.t(lang, "error.tenant_not_found"), show_alert=True)
        return
    if subscription.is_blocked(tenant):
        await call.answer(i18n.t(lang, "error.paused"), show_alert=True)
        return

    service_id = call.data.split(":", 1)[1]
    await state.update_data(service_id=service_id)

    today = datetime.now()
    weekday_names = i18n.weekdays(lang)
    kb = []
    for i in range(14):
        day = today + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        if i == 0:
            label = i18n.t(lang, "date.today")
        elif i == 1:
            label = i18n.t(lang, "date.tomorrow")
        else:
            label = f"{weekday_names[day.weekday()]} {day.strftime('%d.%m')}"
        kb.append([InlineKeyboardButton(text=f"📅 {label}", callback_data=f"date:{date_str}")])

    await call.message.edit_text(
        i18n.t(lang, "pick.date"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await state.set_state(BookingSG.selecting_date)
    await call.answer()


@booking_router.callback_query(BookingSG.selecting_date, F.data.startswith("date:"))
async def process_date_choice(call: types.CallbackQuery, state: FSMContext):
    lang = await _user_lang(call)
    tenant = await _client_tenant(call.bot.token)
    if not tenant:
        await call.answer(i18n.t(lang, "error.tenant_not_found"), show_alert=True)
        return
    if subscription.is_blocked(tenant):
        await call.answer(i18n.t(lang, "error.paused"), show_alert=True)
        return

    selected_date_str = call.data.split(":", 1)[1]
    await state.update_data(selected_date=selected_date_str)

    data = await state.get_data()
    slots = await BookingService.get_available_slots(data["tenant_id"], data["service_id"], selected_date_str)

    if selected_date_str == datetime.now().strftime("%Y-%m-%d"):
        now_hm = datetime.now().strftime("%H:%M")
        slots = [s for s in slots if s > now_hm]

    if not slots:
        await call.answer(i18n.t(lang, "pick.no_slots"), show_alert=True)
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

    await call.message.edit_text(
        i18n.t(lang, "pick.time"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await state.set_state(BookingSG.selecting_slot)
    await call.answer()


@booking_router.callback_query(BookingSG.selecting_slot, F.data.startswith("time:"))
async def process_slot_choice(call: types.CallbackQuery, state: FSMContext):
    lang = await _user_lang(call)
    tenant = await _client_tenant(call.bot.token)
    if not tenant:
        await call.answer(i18n.t(lang, "error.tenant_not_found"), show_alert=True)
        return
    if subscription.is_blocked(tenant):
        await call.answer(i18n.t(lang, "error.paused"), show_alert=True)
        return

    slot_time = call.data.split(":", 1)[1]
    await state.update_data(selected_time=slot_time)

    btn = KeyboardButton(text=i18n.t(lang, "phone.share_button"), request_contact=True)
    kb = ReplyKeyboardMarkup(keyboard=[[btn]], resize_keyboard=True, one_time_keyboard=True)

    phone_prompt = render(tenant, "phone_prompt", lang=lang)

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
    lang = await _user_lang(message)
    button = i18n.t(lang, "phone.share_button")
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        await message.answer(i18n.t(lang, "phone.enter", button=button))
        return
    if not PHONE_RE.match(raw):
        await message.answer(i18n.t(lang, "phone.invalid", button=button))
        return
    await _finalize_booking(message, state, raw)


async def _finalize_booking(message: types.Message, state: FSMContext, phone: str):
    lang = await _user_lang(message)
    full_name = message.from_user.full_name or message.from_user.first_name
    data = await state.get_data()
    tenant_id = data.get("tenant_id")
    service_id = data.get("service_id")
    selected_date = data.get("selected_date")
    selected_time = data.get("selected_time")

    if not all([tenant_id, service_id, selected_date, selected_time]):
        await state.clear()
        await message.answer(i18n.t(lang, "session.expired"))
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

    confirmation_title = render(tenant, "confirmation_title", lang=lang)
    details_line = i18n.format_details(service, lang) if isinstance(service, dict) else ""
    details_line = f"{details_line}\n" if details_line else ""
    await safe_send(
        message,
        f"{confirmation_title}\n\n"
        f"{i18n.t(lang, 'confirm.venue', name=tenant['name'])}\n"
        f"{i18n.t(lang, 'confirm.service', name=service['name'])}\n"
        f"{details_line}"
        f"{i18n.t(lang, 'confirm.datetime', date=selected_date, time=selected_time)}\n"
        f"{i18n.t(lang, 'confirm.price', price=profiles.format_price(service['price']), currency=service['currency'])}\n\n"
        f"{i18n.t(lang, 'confirm.my')}",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown",
    )

    notif_chat_id = tenant.get("notification_chat_id")
    if notif_chat_id:
        owner_lang = await _owner_lang(tenant)
        notify_title = render(tenant, "notify_title", lang=owner_lang)
        owner_details = i18n.format_details(service, owner_lang) if isinstance(service, dict) else ""
        owner_details = f"{owner_details}\n" if owner_details else ""
        try:
            await message.bot.send_message(
                chat_id=notif_chat_id,
                text=f"**{notify_title}**\n\n"
                     f"{i18n.t(owner_lang, 'notify.client', name=full_name, phone=phone)}\n"
                     f"{i18n.t(owner_lang, 'notify.service', name=service['name'])}\n"
                     f"{owner_details}"
                     f"{i18n.t(owner_lang, 'notify.time', date=selected_date, time=selected_time)}",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    await state.clear()
