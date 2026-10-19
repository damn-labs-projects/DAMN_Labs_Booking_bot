import re
import uuid

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.mongodb import db
from app.services.tenant_auth import hash_owner_password, owner_password_set
from app.services.tenant_texts import DEFAULT_TEXTS, TEXT_LABELS, get_texts
from app.bot.handlers.parent import (
    _load_tenants,
    _mask_token,
    build_parent_demo_message,
    build_parent_menu,
    build_parent_tenants_list,
    build_tenants_keyboard,
    is_admin,
    safe_answer,
    safe_edit_message,
)

from app.bot.filters import is_parent_bot

tenant_admin_router = Router()
tenant_admin_router.message.filter(is_parent_bot)
tenant_admin_router.callback_query.filter(is_parent_bot)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
CURRENCIES = ["KGS", "USD", "EUR", "KZT", "RUB"]


class AddServiceSG(StatesGroup):
    waiting_name = State()
    waiting_price = State()
    waiting_currency = State()
    waiting_duration = State()


class EditFieldSG(StatesGroup):
    waiting_value = State()


async def _guard(call: types.CallbackQuery) -> bool:
    if await is_admin(getattr(getattr(call, "from_user", None), "id", None)):
        return True
    await safe_answer(call, "🔒 Требуется вход в панель администратора.", show_alert=True)
    return False


async def _get_tenant(tenant_id: str):
    if db is None or db.db is None:
        return None
    return await db.db.tenants.find_one({"tenant_id": tenant_id})


async def _count_services(tenant_id: str) -> int:
    if db is None or db.db is None:
        return 0
    return await db.db.services.count_documents({"tenant_id": tenant_id})


def _back_kb(tenant_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"tadm:open:{tenant_id}")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="tadm:menu")],
        ]
    )


async def build_tenant_card(tenant: dict):
    tid = tenant["tenant_id"]
    hours = tenant.get("working_hours", {})
    brand = tenant.get("branding") or {}
    sub_status = tenant.get("subscription", {}).get("status", "—")
    services_count = await _count_services(tid)
    owner_ids = (tenant.get("owner") or {}).get("telegram_ids") or []
    if not owner_ids:
        owner_info = tenant.get("owner_info") or {}
        if owner_info.get("telegram_id"):
            owner_ids = [owner_info["telegram_id"]]
    owner_line = ", ".join(str(i) for i in owner_ids) if owner_ids else "не задан"

    text = (
        f"{brand.get('emoji', '')} {tenant.get('name', '—')}\n"
        f"────────────────\n"
        f"🆔 {tid}\n"
        f"🏷 Категория: {tenant.get('category', '—')}\n"
        f"💳 Статус: {sub_status}\n"
        f"🛠 Услуг: {services_count}\n"
        f"🕘 Часы: {hours.get('start', '09:00')}–{hours.get('end', '20:00')} "
        f"(слот {hours.get('slot_duration_minutes', 60)} мин)\n"
        f"🆔 Telegram ID владельца: {owner_line}\n"
        f"🔑 Токен: {_mask_token(tenant.get('bot_config', {}).get('token', ''))}\n"
        f"👤 Пароль владельца: {'настроен' if owner_password_set(tenant) else 'не задан'}"
    )
    if brand.get("description"):
        text += f"\n📝 {brand['description']}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Услуги", callback_data=f"tadm:srv:{tid}")],
            [InlineKeyboardButton(text="✏️ Тексты", callback_data=f"tadm:txt:{tid}")],
            [InlineKeyboardButton(text="🕘 Часы работы", callback_data=f"tadm:hours:{tid}")],
            [InlineKeyboardButton(text="🎨 Брендинг", callback_data=f"tadm:brand:{tid}")],
            [InlineKeyboardButton(text="👤 Пароль владельца", callback_data=f"tadm:ownerpw:{tid}")],
            [InlineKeyboardButton(text="🔗 Webhook / статус", callback_data=f"tadm:webhook:{tid}")],
            [
                InlineKeyboardButton(text="⬅️ К списку", callback_data="tadm:list"),
                InlineKeyboardButton(text="🏠 В меню", callback_data="tadm:menu"),
            ],
        ]
    )
    return text, kb


async def _save_tenant_field(tenant_id: str, update: dict) -> None:
    if db is None or db.db is None:
        return
    from datetime import datetime

    update = dict(update)
    update["updated_at"] = datetime.utcnow()
    await db.db.tenants.update_one({"tenant_id": tenant_id}, {"$set": update})


@tenant_admin_router.callback_query(F.data.startswith("tadm:open:"))
async def tadm_open(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    tenant = await _get_tenant(tid)
    if not tenant:
        await safe_edit_message(call, "Бизнес не найден.", build_parent_menu())
        await safe_answer(call)
        return
    text, kb = await build_tenant_card(tenant)
    await safe_edit_message(call, text, kb)
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data == "tadm:list")
async def tadm_list(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tenants = await _load_tenants()
    await safe_edit_message(call, build_parent_tenants_list(tenants), build_tenants_keyboard(tenants))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data == "tadm:menu")
async def tadm_menu(call: types.CallbackQuery):
    if not await _guard(call):
        return
    await safe_edit_message(call, build_parent_demo_message("Demo Salon"), build_parent_menu())
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:webhook:"))
async def tadm_webhook(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    tenant = await _get_tenant(tid)
    if not tenant:
        await safe_answer(call, "Бизнес не найден.", show_alert=True)
        return
    token = tenant.get("bot_config", {}).get("token")
    note = "⚠️ Токен не задан."
    if token:
        try:
            from app.bot.manager import bot_manager

            await bot_manager.register_tenant_webhook(token)
            note = "✅ Webhook дочернего бота зарегистрирован."
        except Exception as exc:
            note = f"⚠️ Не удалось зарегистрировать webhook: {exc}"
    await safe_edit_message(call, f"🔗 Webhook для «{tenant.get('name')}»\n\n{note}", _back_kb(tid))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:srv:"))
async def tadm_services(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    await _show_services(call, tid)
    await safe_answer(call)


async def _services_view(tid: str):
    services = []
    if db is not None and db.db is not None:
        cursor = db.db.services.find({"tenant_id": tid}).sort("name", 1)
        services = await cursor.to_list(length=50)

    if services:
        lines = ["🛠 Услуги бизнеса:"]
        for s in services:
            lines.append(
                f"• {s.get('name', '—')} — {s.get('price', '—')} {s.get('currency', '')} "
                f"({s.get('duration_minutes', 60)} мин)"
            )
        text = "\n".join(lines)
    else:
        text = "🛠 Услуг пока нет. Добавьте первую услугу."

    rows = [
        [InlineKeyboardButton(
            text=f"🗑 {s.get('name', '—')}",
            callback_data=f"tadm:srvdel:{tid}:{s.get('service_id')}",
        )]
        for s in services
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить услугу", callback_data=f"tadm:srvadd:{tid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"tadm:open:{tid}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_services(call: types.CallbackQuery, tid: str) -> None:
    text, kb = await _services_view(tid)
    await safe_edit_message(call, text, kb)


@tenant_admin_router.callback_query(F.data.startswith("tadm:srvdel:"))
async def tadm_service_delete(call: types.CallbackQuery):
    if not await _guard(call):
        return
    parts = call.data.split(":", 3)
    tid, sid = parts[2], parts[3]
    if db is not None and db.db is not None:
        await db.db.services.delete_one({"tenant_id": tid, "service_id": sid})
    await safe_answer(call, "Услуга удалена")
    await _show_services(call, tid)


@tenant_admin_router.callback_query(F.data.startswith("tadm:srvadd:"))
async def tadm_service_add(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    await state.clear()
    await state.set_state(AddServiceSG.waiting_name)
    await state.update_data(tid=tid)
    await safe_edit_message(
        call,
        "➕ Новая услуга.\nШаг 1/4. Введите название услуги:",
        _back_kb(tid),
    )
    await safe_answer(call)


@tenant_admin_router.message(AddServiceSG.waiting_name)
async def tadm_service_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Введите название услуги:")
        return
    await state.update_data(name=name)
    await state.set_state(AddServiceSG.waiting_price)
    await message.answer("Шаг 2/4. Введите цену (например, 1500):")


@tenant_admin_router.message(AddServiceSG.waiting_price)
async def tadm_service_price(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = float(raw)
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите цену числом, например 1500 или 1500.50:")
        return
    await state.update_data(price=price)
    await state.set_state(AddServiceSG.waiting_currency)
    rows = [[InlineKeyboardButton(text=c, callback_data=f"tadm:srvcur:{c}")] for c in CURRENCIES]
    await message.answer("Шаг 3/4. Выберите валюту:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@tenant_admin_router.callback_query(AddServiceSG.waiting_currency, F.data.startswith("tadm:srvcur:"))
async def tadm_service_currency(call: types.CallbackQuery, state: FSMContext):
    currency = call.data.split(":", 2)[2]
    await state.update_data(currency=currency)
    await state.set_state(AddServiceSG.waiting_duration)
    await safe_edit_message(call, f"Валюта: {currency}\nШаг 4/4. Введите длительность в минутах (например, 60):")
    await safe_answer(call)


@tenant_admin_router.message(AddServiceSG.waiting_duration)
async def tadm_service_duration(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (0 < int(raw) <= 480):
        await message.answer("Введите длительность в минутах числом от 1 до 480:")
        return
    duration = int(raw)
    data = await state.get_data()
    tid = data.get("tid")
    service = {
        "tenant_id": tid,
        "service_id": str(uuid.uuid4())[:8],
        "name": data.get("name", "Услуга"),
        "description": "",
        "price": data.get("price", 0),
        "currency": data.get("currency", "KGS"),
        "duration_minutes": duration,
        "is_active": True,
    }
    if db is not None and db.db is not None:
        await db.db.services.insert_one(service)
    await state.clear()
    await message.answer(
        f"✅ Услуга «{service['name']}» добавлена.\n\n"
        f"Цена: {service['price']} {service['currency']}, {service['duration_minutes']} мин."
    )
    text, kb = await _services_view(tid)
    await message.answer(text, reply_markup=kb)


@tenant_admin_router.callback_query(F.data.startswith("tadm:txt:"))
async def tadm_texts(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    tenant = await _get_tenant(tid)
    if not tenant:
        await safe_answer(call, "Бизнес не найден.", show_alert=True)
        return
    texts = get_texts(tenant)
    lines = ["✏️ Тексты дочернего бота:"]
    for key, label in TEXT_LABELS.items():
        value = texts.get(key, "")
        lines.append(f"• {label}: {value[:60]}")
    lines.append("\nВыберите текст для изменения. Доступны подстановки: {name}, {emoji}, {tagline}.")
    rows = [
        [InlineKeyboardButton(text=f"✏️ {TEXT_LABELS[key]}", callback_data=f"tadm:txtset:{tid}:{key}")]
        for key in TEXT_LABELS
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"tadm:open:{tid}")])
    await safe_edit_message(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:txtset:"))
async def tadm_text_set(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    parts = call.data.split(":", 3)
    tid, field = parts[2], parts[3]
    tenant = await _get_tenant(tid)
    current = get_texts(tenant or {}).get(field, "")
    label = TEXT_LABELS.get(field, field)
    await state.clear()
    await state.set_state(EditFieldSG.waiting_value)
    await state.update_data(tid=tid, kind="text", field=field)
    await safe_edit_message(
        call,
        f"✏️ {label}\n\nТекущее значение:\n{current}\n\n"
        "Отправьте новый текст сообщением. Доступны {name}, {emoji}, {tagline}.",
        _back_kb(tid),
    )
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:hours:"))
async def tadm_hours(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    tenant = await _get_tenant(tid)
    if not tenant:
        await safe_answer(call, "Бизнес не найден.", show_alert=True)
        return
    hours = tenant.get("working_hours", {})
    text = (
        "🕘 Часы работы:\n\n"
        f"Начало: {hours.get('start', '09:00')}\n"
        f"Конец: {hours.get('end', '20:00')}\n"
        f"Длительность слота: {hours.get('slot_duration_minutes', 60)} мин"
    )
    rows = [
        [InlineKeyboardButton(text="🕘 Начало", callback_data=f"tadm:hoursedit:{tid}:start"),
         InlineKeyboardButton(text="🕘 Конец", callback_data=f"tadm:hoursedit:{tid}:end")],
        [InlineKeyboardButton(text="⏱ Слот", callback_data=f"tadm:hoursedit:{tid}:slot_duration_minutes")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"tadm:open:{tid}")],
    ]
    await safe_edit_message(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:hoursedit:"))
async def tadm_hours_edit(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    parts = call.data.split(":", 3)
    tid, field = parts[2], parts[3]
    await state.clear()
    await state.set_state(EditFieldSG.waiting_value)
    await state.update_data(tid=tid, kind="hours", field=field)
    if field == "slot_duration_minutes":
        prompt = "Введите длительность слота в минутах (1–480):"
    else:
        prompt = "Введите время в формате ЧЧ:ММ (например, 09:00):"
    await safe_edit_message(call, prompt, _back_kb(tid))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:brand:"))
async def tadm_brand(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    tenant = await _get_tenant(tid)
    if not tenant:
        await safe_answer(call, "Бизнес не найден.", show_alert=True)
        return
    brand = tenant.get("branding") or {}
    text = (
        "🎨 Брендинг:\n\n"
        f"Эмодзи: {brand.get('emoji', '—')}\n"
        f"Описание: {brand.get('description', '—')}"
    )
    rows = [
        [InlineKeyboardButton(text="😀 Эмодзи", callback_data=f"tadm:brandedit:{tid}:emoji")],
        [InlineKeyboardButton(text="📝 Описание", callback_data=f"tadm:brandedit:{tid}:description")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"tadm:open:{tid}")],
    ]
    await safe_edit_message(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:brandedit:"))
async def tadm_brand_edit(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    parts = call.data.split(":", 3)
    tid, field = parts[2], parts[3]
    await state.clear()
    await state.set_state(EditFieldSG.waiting_value)
    await state.update_data(tid=tid, kind="brand", field=field)
    if field == "emoji":
        prompt = "Отправьте эмодзи для бренда (например, ✨ или 💇):"
    else:
        prompt = "Отправьте короткое описание бизнеса (одна строка):"
    await safe_edit_message(call, prompt, _back_kb(tid))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:ownerpw:"))
async def tadm_owner_password(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    tenant = await _get_tenant(tid)
    current = "задан" if tenant and owner_password_set(tenant) else "не задан"
    await state.clear()
    await state.set_state(EditFieldSG.waiting_value)
    await state.update_data(tid=tid, kind="owner_password", field="password_hash")
    await safe_edit_message(
        call,
        f"👤 Пароль владельца бизнеса (сейчас: {current}).\n\n"
        "Отправьте новый пароль сообщением. Владелец использует его для входа "
        "командой /owner в дочернем боте.",
        _back_kb(tid),
    )
    await safe_answer(call)


@tenant_admin_router.message(EditFieldSG.waiting_value)
async def tadm_edit_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tid = data.get("tid")
    kind = data.get("kind")
    field = data.get("field")
    value = (message.text or "").strip()

    if not value:
        await message.answer("Значение не может быть пустым. Отправьте текст ещё раз:")
        return

    if kind == "text":
        await _save_tenant_field(tid, {f"texts.{field}": value})
    elif kind == "hours":
        if field == "slot_duration_minutes":
            if not value.isdigit() or not (0 < int(value) <= 480):
                await message.answer("Введите число минут от 1 до 480:")
                return
            await _save_tenant_field(tid, {"working_hours.slot_duration_minutes": int(value)})
        else:
            if not TIME_RE.match(value):
                await message.answer("Неверный формат. Введите время как ЧЧ:ММ, например 09:00:")
                return
            await _save_tenant_field(tid, {f"working_hours.{field}": value})
    elif kind == "brand":
        await _save_tenant_field(tid, {f"branding.{field}": value})
    elif kind == "owner_password":
        await _save_tenant_field(tid, {"owner.password_hash": hash_owner_password(tid, value)})
    else:
        await state.clear()
        await message.answer("Неизвестное действие.")
        return

    await state.clear()
    tenant = await _get_tenant(tid)
    await message.answer("✅ Сохранено.")
    if tenant:
        text, kb = await build_tenant_card(tenant)
        await message.answer(text, reply_markup=kb)
