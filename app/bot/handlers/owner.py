import logging
import uuid
from datetime import datetime, timezone

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.db.mongodb import db
from app.services import subscription
from app.services.tenant_auth import (
    hash_owner_password,
    owner_is_authorized,
    owner_password_set,
    verify_owner_password,
)
from app.services.tenant_texts import TEXT_LABELS, get_texts
from app.bot.filters import is_child_bot
from app.bot.handlers.parent import safe_answer, safe_edit_message
from app.bot.handlers.tenant_admin import CURRENCIES, TIME_RE

try:
    from app.api.v1.pages import build_pay_link, payment_is_configured
except (ModuleNotFoundError, ImportError):  # pragma: no cover
    def payment_is_configured() -> bool:
        return False

    def build_pay_link(tenant_id: str) -> str:
        return f"/pay?tenant_id={tenant_id}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

owner_router = Router()
owner_router.message.filter(is_child_bot)
owner_router.callback_query.filter(is_child_bot)
logger = logging.getLogger(__name__)


class OwnerAuthSG(StatesGroup):
    waiting_password = State()


class OwnerServiceSG(StatesGroup):
    waiting_name = State()
    waiting_price = State()
    waiting_currency = State()
    waiting_duration = State()


class OwnerEditSG(StatesGroup):
    waiting_value = State()


async def _tenant_by_token(token: str):
    if db is None or db.db is None:
        return None
    return await db.db.tenants.find_one({"bot_config.token": token})


async def _get_tenant(tenant_id: str):
    if db is None or db.db is None:
        return None
    return await db.db.tenants.find_one({"tenant_id": tenant_id})


async def _save_tenant_field(tenant_id: str, update: dict) -> None:
    if db is None or db.db is None:
        return

    update = dict(update)
    update["updated_at"] = _utcnow()
    await db.db.tenants.update_one({"tenant_id": tenant_id}, {"$set": update})


async def _owner_guard(call: types.CallbackQuery):
    tenant = await _tenant_by_token(call.bot.token)
    if not tenant:
        await safe_answer(call, "Бот не привязан к бизнесу.", show_alert=True)
        return None
    if not owner_is_authorized(tenant, getattr(getattr(call, "from_user", None), "id", None)):
        await safe_answer(call, "🔒 Требуется вход владельца: /owner", show_alert=True)
        return None
    return tenant


def _subscription_line(tenant: dict) -> str:
    status = subscription.status_of(tenant)
    labels = {
        "trial": "пробный период",
        "active": "активна",
        "past_due": "просрочена оплата",
        "expired": "истекла",
        "cancelled": "отменена",
    }
    label = labels.get(status, status)
    end = subscription.period_end_of(tenant)
    if end is not None and hasattr(end, "strftime"):
        return f"💳 Подписка: {label} до {end.strftime('%d.%m.%Y')}"
    return f"💳 Подписка: {label}"


def _panel_kb(tenant: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📅 Записи", callback_data="own:appts")],
        [InlineKeyboardButton(text="🛠 Услуги", callback_data="own:srv")],
        [InlineKeyboardButton(text="✏️ Тексты", callback_data="own:txt")],
        [InlineKeyboardButton(text="🕘 Часы работы", callback_data="own:hours")],
        [InlineKeyboardButton(text="🎨 Брендинг", callback_data="own:brand")],
        [InlineKeyboardButton(text="💳 Подписка", callback_data="own:pay")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 В меню", callback_data="own:menu")]]
    )


async def build_owner_panel(tenant: dict) -> str:
    brand = tenant.get("branding") or {}
    hours = tenant.get("working_hours", {})
    count = 0
    upcoming = 0
    if db is not None and db.db is not None:
        count = await db.db.services.count_documents({"tenant_id": tenant["tenant_id"]})
        upcoming = await db.db.appointments.count_documents({
            "tenant_id": tenant["tenant_id"],
            "status": {"$ne": "cancelled"},
            "start_time": {"$gte": _utcnow()},
        })
    return (
        f"⚙️ Панель владельца\n"
        f"{brand.get('emoji', '')} {tenant.get('name', '—')}\n"
        f"────────────────\n"
        f"📅 Записей всего: {upcoming} (предстоящих)\n"
        f"🛠 Услуг: {count}\n"
        f"🕘 Часы: {hours.get('start', '09:00')}–{hours.get('end', '20:00')} "
        f"(слот {hours.get('slot_duration_minutes', 60)} мин)\n"
        f"{_subscription_line(tenant)}\n\n"
        "Выберите раздел:"
    )


async def _send_panel_message(message: types.Message, tenant: dict) -> None:
    await message.answer(await build_owner_panel(tenant), reply_markup=_panel_kb(tenant))


@owner_router.message(Command("owner", "admin", "login"))
async def owner_enter(message: types.Message, state: FSMContext):
    tenant = await _tenant_by_token(message.bot.token)
    if not tenant:
        await message.answer("Этот бот не привязан к бизнесу.")
        return
    if owner_is_authorized(tenant, message.from_user.id):
        await state.clear()
        await _send_panel_message(message, tenant)
        return
    if not owner_password_set(tenant):
        await message.answer(
            "🔒 Доступ владельца ещё не настроен.\n"
            "Попросите администратора платформы задать пароль владельца."
        )
        return
    await state.clear()
    await state.set_state(OwnerAuthSG.waiting_password)
    await state.update_data(tid=tenant["tenant_id"])
    await message.answer("🔒 Введите пароль владельца бизнеса (отмена — /cancel):")


@owner_router.message(Command("cancel"))
async def owner_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.")


@owner_router.message(OwnerAuthSG.waiting_password)
async def owner_password(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tenant = await _get_tenant(data.get("tid"))
    if not tenant:
        await state.clear()
        await message.answer("Бизнес не найден.")
        return
    if not verify_owner_password(tenant, message.text or ""):
        await message.answer("❌ Неверный пароль. Попробуйте ещё раз или /cancel.")
        return
    if db is not None and db.db is not None:
        await db.db.tenants.update_one(
            {"tenant_id": tenant["tenant_id"]},
            {"$addToSet": {"owner.telegram_ids": message.from_user.id}},
        )
    await state.clear()
    await message.answer("✅ Доступ открыт.")
    await _send_panel_message(message, await _tenant_by_token(message.bot.token) or tenant)


@owner_router.callback_query(F.data == "own:menu")
async def own_menu(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    await safe_edit_message(call, await build_owner_panel(tenant), _panel_kb(tenant))
    await safe_answer(call)


@owner_router.callback_query(F.data == "own:pay")
async def own_pay(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return

    support = (settings.SUPPORT_CONTACT or "").strip() if settings else ""
    if not payment_is_configured():
        await safe_edit_message(
            call,
            "💳 Оплата пока не подключена.\n"
            "Напишите в поддержку — включим оплату."
            + (f"\n\n{support}" if support else ""),
            _panel_kb(tenant),
        )
        await safe_answer(call)
        return

    link = build_pay_link(tenant["tenant_id"])
    if not link.startswith("http"):
        await safe_edit_message(
            call,
            "💳 Не задан BASE_WEBHOOK_URL — ссылка на оплату недоступна.",
            _panel_kb(tenant),
        )
        await safe_answer(call)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить подписку", url=link)],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="own:menu")],
    ])
    await safe_edit_message(
        call,
        f"{_subscription_line(tenant)}\n\n"
        "Оплата проходит на защищённой странице. После оплаты подписка "
        "активируется в течение минуты.",
        kb,
    )
    await safe_answer(call)


_STATUS_EMOJI = {"confirmed": "✅", "cancelled": "❌", "done": "🏁", "pending": "🕓"}


async def _appointments_view(tid: str, scope: str = "upcoming"):
    appts = []
    services = {}
    if db is not None and db.db is not None:
        for s in await db.db.services.find({"tenant_id": tid}).to_list(length=200):
            services[s.get("service_id")] = s.get("name", "—")
        query = {"tenant_id": tid}
        if scope == "upcoming":
            query["status"] = {"$ne": "cancelled"}
            query["start_time"] = {"$gte": _utcnow()}
        cursor = db.db.appointments.find(query).sort("start_time", 1)
        appts = await cursor.to_list(length=30)

    if appts:
        lines = ["📅 Записи:" if scope == "upcoming" else "📅 Все записи:"]
        for a in appts:
            info = a.get("client_info", {}) or {}
            start = a.get("start_time")
            when = start.strftime("%d.%m %H:%M") if hasattr(start, "strftime") else str(start)
            status = _STATUS_EMOJI.get(a.get("status", ""), a.get("status", "—"))
            lines.append(
                f"{status} {when} — {info.get('full_name', 'Клиент')} "
                f"({info.get('phone', '—')})\n     {services.get(a.get('service_id'), '—')}"
            )
        text = "\n".join(lines)
    else:
        text = "📅 Предстоящих записей нет." if scope == "upcoming" else "📅 Записей нет."

    rows = []
    for a in appts:
        if a.get("status") != "cancelled" and a.get("appointment_id"):
            info = a.get("client_info", {}) or {}
            start = a.get("start_time")
            when = start.strftime("%d.%m %H:%M") if hasattr(start, "strftime") else ""
            rows.append([InlineKeyboardButton(
                text=f"❌ Отменить {when} {info.get('full_name', '')}".strip()[:60],
                callback_data=f"own:appt:cancel:{a['appointment_id']}",
            )])
    toggle = (
        InlineKeyboardButton(text="📜 Все записи", callback_data="own:appts:all")
        if scope == "upcoming"
        else InlineKeyboardButton(text="⏭ Предстоящие", callback_data="own:appts")
    )
    rows.append([toggle])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="own:menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@owner_router.callback_query(F.data == "own:appts")
async def own_appointments(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    text, kb = await _appointments_view(tenant["tenant_id"], "upcoming")
    await safe_edit_message(call, text, kb)
    await safe_answer(call)


@owner_router.callback_query(F.data == "own:appts:all")
async def own_appointments_all(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    text, kb = await _appointments_view(tenant["tenant_id"], "all")
    await safe_edit_message(call, text, kb)
    await safe_answer(call)


@owner_router.callback_query(F.data.startswith("own:appt:cancel:"))
async def own_appointment_cancel(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    appointment_id = call.data.split(":", 3)[3]

    appointment = None
    if db is not None and db.db is not None:
        appointment = await db.db.appointments.find_one(
            {"tenant_id": tenant["tenant_id"], "appointment_id": appointment_id}
        )
        if appointment and appointment.get("status") != "cancelled":
            await db.db.appointments.update_one(
                {"tenant_id": tenant["tenant_id"], "appointment_id": appointment_id},
                {"$set": {"status": "cancelled", "updated_at": _utcnow()}},
            )

    if not appointment or appointment.get("status") == "cancelled":
        await safe_answer(call, "Запись уже была отменена.")
        text, kb = await _appointments_view(tenant["tenant_id"], "upcoming")
        await safe_edit_message(call, text, kb)
        return

    await safe_answer(call, "Запись отменена. Клиенту отправлено уведомление.")
    await _notify_client_of_cancellation(call.bot, tenant, appointment)
    text, kb = await _appointments_view(tenant["tenant_id"], "upcoming")
    await safe_edit_message(call, text, kb)


async def _notify_client_of_cancellation(bot, tenant: dict, appointment: dict) -> None:
    info = appointment.get("client_info") or {}
    chat_id = info.get("telegram_id")
    if not chat_id:
        return

    start = appointment.get("start_time")
    when = start.strftime("%d.%m в %H:%M") if hasattr(start, "strftime") else str(start)

    service_name = "—"
    if db is not None and db.db is not None:
        service = await db.db.services.find_one({
            "tenant_id": tenant["tenant_id"],
            "service_id": appointment.get("service_id"),
        })
        if service:
            service_name = service.get("name", "—")

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"❌ Ваша запись отменена владельцем.\n\n"
                f"🏢 Заведение: {tenant.get('name', '—')}\n"
                f"💇 Услуга: {service_name}\n"
                f"📅 Когда: {when}"
            ),
        )
    except Exception as exc:
        logger.warning(
            "Failed to notify client %s about cancelled appointment %s: %s",
            chat_id,
            appointment.get("appointment_id"),
            exc,
        )


async def _services_view(tid: str):
    services = []
    if db is not None and db.db is not None:
        cursor = db.db.services.find({"tenant_id": tid}).sort("name", 1)
        services = await cursor.to_list(length=50)

    if services:
        lines = ["🛠 Ваши услуги:"]
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
            callback_data=f"own:srvdel:{s.get('service_id')}",
        )]
        for s in services
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить услугу", callback_data="own:srvadd")])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="own:menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@owner_router.callback_query(F.data == "own:srv")
async def own_services(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    text, kb = await _services_view(tenant["tenant_id"])
    await safe_edit_message(call, text, kb)
    await safe_answer(call)


@owner_router.callback_query(F.data.startswith("own:srvdel:"))
async def own_service_delete(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    sid = call.data.split(":", 2)[2]
    if db is not None and db.db is not None:
        await db.db.services.delete_one({"tenant_id": tenant["tenant_id"], "service_id": sid})
    await safe_answer(call, "Услуга удалена")
    text, kb = await _services_view(tenant["tenant_id"])
    await safe_edit_message(call, text, kb)


@owner_router.callback_query(F.data == "own:srvadd")
async def own_service_add(call: types.CallbackQuery, state: FSMContext):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    await state.clear()
    await state.set_state(OwnerServiceSG.waiting_name)
    await state.update_data(tid=tenant["tenant_id"])
    await safe_edit_message(call, "➕ Новая услуга.\nШаг 1/4. Введите название услуги:", _back_kb())
    await safe_answer(call)


@owner_router.message(OwnerServiceSG.waiting_name)
async def own_service_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Введите название услуги:")
        return
    await state.update_data(name=name)
    await state.set_state(OwnerServiceSG.waiting_price)
    await message.answer("Шаг 2/4. Введите цену (например, 1500):")


@owner_router.message(OwnerServiceSG.waiting_price)
async def own_service_price(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = float(raw)
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите цену числом, например 1500 или 1500.50:")
        return
    await state.update_data(price=price)
    await state.set_state(OwnerServiceSG.waiting_currency)
    rows = [[InlineKeyboardButton(text=c, callback_data=f"own:srvcur:{c}")] for c in CURRENCIES]
    await message.answer("Шаг 3/4. Выберите валюту:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@owner_router.callback_query(OwnerServiceSG.waiting_currency, F.data.startswith("own:srvcur:"))
async def own_service_currency(call: types.CallbackQuery, state: FSMContext):
    currency = call.data.split(":", 2)[2]
    await state.update_data(currency=currency)
    await state.set_state(OwnerServiceSG.waiting_duration)
    await safe_edit_message(call, f"Валюта: {currency}\nШаг 4/4. Введите длительность в минутах (например, 60):")
    await safe_answer(call)


@owner_router.message(OwnerServiceSG.waiting_duration)
async def own_service_duration(message: types.Message, state: FSMContext):
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


@owner_router.callback_query(F.data == "own:txt")
async def own_texts(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    texts = get_texts(tenant)
    lines = ["✏️ Тексты бота:"]
    for key, label in TEXT_LABELS.items():
        value = texts.get(key, "")
        lines.append(f"• {label}: {value[:60]}")
    lines.append("\nДоступны подстановки: {name}, {emoji}, {tagline}.")
    rows = [
        [InlineKeyboardButton(text=f"✏️ {TEXT_LABELS[key]}", callback_data=f"own:txtset:{key}")]
        for key in TEXT_LABELS
    ]
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="own:menu")])
    await safe_edit_message(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await safe_answer(call)


@owner_router.callback_query(F.data.startswith("own:txtset:"))
async def own_text_set(call: types.CallbackQuery, state: FSMContext):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    field = call.data.split(":", 2)[2]
    current = get_texts(tenant).get(field, "")
    label = TEXT_LABELS.get(field, field)
    await state.clear()
    await state.set_state(OwnerEditSG.waiting_value)
    await state.update_data(tid=tenant["tenant_id"], kind="text", field=field)
    await safe_edit_message(
        call,
        f"✏️ {label}\n\nТекущее значение:\n{current}\n\n"
        "Отправьте новый текст сообщением. Доступны {name}, {emoji}, {tagline}.",
        _back_kb(),
    )
    await safe_answer(call)


@owner_router.callback_query(F.data == "own:hours")
async def own_hours(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    hours = tenant.get("working_hours", {})
    text = (
        "🕘 Часы работы:\n\n"
        f"Начало: {hours.get('start', '09:00')}\n"
        f"Конец: {hours.get('end', '20:00')}\n"
        f"Длительность слота: {hours.get('slot_duration_minutes', 60)} мин"
    )
    rows = [
        [InlineKeyboardButton(text="🕘 Начало", callback_data="own:hoursedit:start"),
         InlineKeyboardButton(text="🕘 Конец", callback_data="own:hoursedit:end")],
        [InlineKeyboardButton(text="⏱ Слот", callback_data="own:hoursedit:slot_duration_minutes")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="own:menu")],
    ]
    await safe_edit_message(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await safe_answer(call)


@owner_router.callback_query(F.data.startswith("own:hoursedit:"))
async def own_hours_edit(call: types.CallbackQuery, state: FSMContext):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    field = call.data.split(":", 2)[2]
    await state.clear()
    await state.set_state(OwnerEditSG.waiting_value)
    await state.update_data(tid=tenant["tenant_id"], kind="hours", field=field)
    if field == "slot_duration_minutes":
        prompt = "Введите длительность слота в минутах (1–480):"
    else:
        prompt = "Введите время в формате ЧЧ:ММ (например, 09:00):"
    await safe_edit_message(call, prompt, _back_kb())
    await safe_answer(call)


@owner_router.callback_query(F.data == "own:brand")
async def own_brand(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    brand = tenant.get("branding") or {}
    text = (
        "🎨 Брендинг:\n\n"
        f"Эмодзи: {brand.get('emoji', '—')}\n"
        f"Описание: {brand.get('description', '—')}"
    )
    rows = [
        [InlineKeyboardButton(text="😀 Эмодзи", callback_data="own:brandedit:emoji")],
        [InlineKeyboardButton(text="📝 Описание", callback_data="own:brandedit:description")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="own:menu")],
    ]
    await safe_edit_message(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await safe_answer(call)


@owner_router.callback_query(F.data.startswith("own:brandedit:"))
async def own_brand_edit(call: types.CallbackQuery, state: FSMContext):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    field = call.data.split(":", 2)[2]
    await state.clear()
    await state.set_state(OwnerEditSG.waiting_value)
    await state.update_data(tid=tenant["tenant_id"], kind="brand", field=field)
    if field == "emoji":
        prompt = "Отправьте эмодзи для бренда (например, ✨ или 💇):"
    else:
        prompt = "Отправьте короткое описание бизнеса (одна строка):"
    await safe_edit_message(call, prompt, _back_kb())
    await safe_answer(call)


@owner_router.message(OwnerEditSG.waiting_value)
async def own_edit_value(message: types.Message, state: FSMContext):
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
    else:
        await state.clear()
        await message.answer("Неизвестное действие.")
        return

    await state.clear()
    tenant = await _get_tenant(tid)
    await message.answer("✅ Сохранено.")
    if tenant:
        await message.answer(await build_owner_panel(tenant), reply_markup=_panel_kb(tenant))
