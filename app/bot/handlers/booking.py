from aiogram import Router, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from datetime import datetime, timedelta

from app.config import settings
from app.db.mongodb import db
from app.bot.handlers.parent import parent_start
from app.services.booking_service import BookingService

booking_router = Router()

class BookingSG(StatesGroup):
    selecting_service = State()
    selecting_date = State()
    selecting_slot = State()
    entering_phone = State()

@booking_router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    if message.bot.token == settings.PARENT_BOT_TOKEN:
        await parent_start(message)
        return

    await state.clear()

    if db.db is None:
        await message.answer("База данных недоступна. Попробуйте позже.")
        return

    tenant = await db.db.tenants.find_one({"bot_config.token": message.bot.token})
    if not tenant:
        await message.answer("Ошибка: Заведение не найдено.")
        return

    sub_status = tenant.get("subscription", {}).get("status")
    if sub_status in ["expired", "cancelled"]:
        await message.answer("Обслуживание заведения временно приостановлено.")
        return

    await state.update_data(tenant_id=tenant["tenant_id"])

    services_cursor = db.db.services.find({"tenant_id": tenant["tenant_id"], "is_active": True})
    services = await services_cursor.to_list(length=50)

    if not services:
        await message.answer(f"Добро пожаловать в **{tenant['name']}**!\nНа данный момент услуг нет.")
        return

    kb = [[InlineKeyboardButton(
        text=f"{s['name']} — {s['price']} {s['currency']} ({s['duration_minutes']} мин)",
        callback_data=f"srv:{s['service_id']}"
    )] for s in services]

    await message.answer(
        f"Добро пожаловать в **{tenant['name']}**!\nВыберите услугу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )
    await state.set_state(BookingSG.selecting_service)


@booking_router.message(Command(commands=["help", "menu", "startup"]))
async def cmd_help(message: types.Message):
    if message.bot.token == settings.PARENT_BOT_TOKEN:
        await parent_start(message)
        return

    await message.answer(
        "📌 Доступные команды:\n/start — начать запись\n/help — помощь",
        parse_mode="Markdown"
    )


@booking_router.callback_query(BookingSG.selecting_service, F.data.startswith("srv:"))
async def process_service_choice(call: types.CallbackQuery, state: FSMContext):
    service_id = call.data.split(":")[1]
    await state.update_data(service_id=service_id)

    today = datetime.now()
    kb = []
    for i in range(3):
        day = today + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        label = "Сегодня" if i == 0 else ("Завтра" if i == 1 else day.strftime("%d.%m"))
        kb.append([InlineKeyboardButton(text=f"📅 {label} ({date_str})", callback_data=f"date:{date_str}")])

    await call.message.edit_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await state.set_state(BookingSG.selecting_date)


@booking_router.callback_query(BookingSG.selecting_date, F.data.startswith("date:"))
async def process_date_choice(call: types.CallbackQuery, state: FSMContext):
    selected_date_str = call.data.split(":")[1]
    await state.update_data(selected_date=selected_date_str)

    data = await state.get_data()
    slots = await BookingService.get_available_slots(data["tenant_id"], data["service_id"], selected_date_str)

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


@booking_router.callback_query(BookingSG.selecting_slot, F.data.startswith("time:"))
async def process_slot_choice(call: types.CallbackQuery, state: FSMContext):
    slot_time = call.data.split(":")[1]
    await state.update_data(selected_time=slot_time)

    btn = KeyboardButton(text="📱 Поделиться контактом", request_contact=True)
    kb = ReplyKeyboardMarkup(keyboard=[[btn]], resize_keyboard=True, one_time_keyboard=True)

    await call.message.delete()
    await call.message.answer("Отправьте ваш номер телефона для подтверждения:", reply_markup=kb)
    await state.set_state(BookingSG.entering_phone)


@booking_router.message(BookingSG.entering_phone, F.contact)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    full_name = message.from_user.full_name or message.from_user.first_name

    data = await state.get_data()
    appointment, tenant, service = await BookingService.create_appointment(
        tenant_id=data["tenant_id"],
        service_id=data["service_id"],
        telegram_id=message.from_user.id,
        full_name=full_name,
        phone=phone,
        date_str=data["selected_date"],
        time_str=data["selected_time"]
    )

    await message.answer(
        f"✅ **Вы успешно записаны!**\n\n"
        f"📍 **Заведение:** {tenant['name']}\n"
        f"💇‍♂️ **Услуга:** {service['name']}\n"
        f"📅 **Дата и время:** {data['selected_date']} {data['selected_time']}\n"
        f"💰 **Стоимость:** {service['price']} {service['currency']}",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )

    notif_chat_id = tenant.get("notification_chat_id")
    if notif_chat_id:
        try:
            await message.bot.send_message(
                chat_id=notif_chat_id,
                text=f"🔔 **Новая запись!**\n\n"
                     f"👤 **Клиент:** {full_name} ({phone})\n"
                     f"🛠 **Услуга:** {service['name']}\n"
                     f"🕒 **Время:** {data['selected_date']} {data['selected_time']}",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    await state.clear()