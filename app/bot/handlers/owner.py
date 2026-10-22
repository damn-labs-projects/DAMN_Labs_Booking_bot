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
from app.services import payment_requests
from app.services import tenant_cache
from app.services import service_profiles as profiles
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


def _parent_bot(fallback=None):
    """Admins live in the platform bot chat, so notifications go through it."""
    parent_token = getattr(settings, "PARENT_BOT_TOKEN", "") if settings else ""
    if not parent_token:
        return fallback
    try:
        from app.bot.manager import bot_manager

        return bot_manager.get_bot_by_token(parent_token)
    except Exception:
        return fallback


def _support_contact() -> tuple[str, str | None]:
    """Support contact text plus a direct t.me link when a username is set."""
    contact = (getattr(settings, "SUPPORT_CONTACT", "") or "").strip() if settings else ""
    username = ""
    if "@" in contact:
        username = contact.split("@", 1)[1].strip().split()[0].lstrip("@")
    return contact, (f"https://t.me/{username}" if username else None)

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
    waiting_field = State()


class OwnerEditSG(StatesGroup):
    waiting_value = State()


async def _tenant_by_token(token: str):
    return await tenant_cache.get_tenant_by_token(token)


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

    if (getattr(settings, "PAYMENT_MODE", "manual") or "manual") != "paddle":
        request_id = None
        try:
            request_id = await payment_requests.create_request(
                tenant_id=tenant["tenant_id"],
                tenant_name=tenant.get("name", "—"),
                contact=str(call.from_user.id),
                source="owner",
            )
        except Exception:
            request_id = None

        support, support_url = _support_contact()

        try:
            await payment_requests.notify_admins(
                _parent_bot(call.bot),
                "💳 Запрос на оплату подписки\n"
                f"🏢 Бизнес: {tenant.get('name', '—')}\n"
                f"🆔 {tenant['tenant_id']}\n"
                f"👤 Telegram ID: {call.from_user.id}\n"
                f"🕒 {payment_requests.utcnow().strftime('%d.%m.%Y %H:%M')}\n\n"
                "Продлите подписку в админке: «Подписки» → нужный бизнес.",
            )
        except Exception:
            pass

        text = (
            f"{_subscription_line(tenant)}\n\n"
            "💬 Запрос на оплату отправлен администратору — он продлит подписку вручную "
            "и напишет вам."
        )
        if support:
            text += f"\n\nПоддержка: {support}"
        if request_id:
            text += f"\nНомер запроса: {request_id}"

        rows = [[InlineKeyboardButton(text="💳 Оплатить / продлить", callback_data="own:pay")]]
        if support_url:
            rows.append(
                [InlineKeyboardButton(text="💬 Написать администратору", url=support_url)]
            )
        rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="own:menu")])

        await safe_edit_message(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
        await safe_answer(call)
        return

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


async def _services_view(tid: str, category: str = "other"):
    services = []
    if db is not None and db.db is not None:
        cursor = db.db.services.find({"tenant_id": tid}).sort("name", 1)
        services = await cursor.to_list(length=50)

    profile = profiles.get_profile(category)

    if services:
        lines = [f"🛠 {profile.list_title}:"]
        for s in services:
            lines.append(f"• {profiles.describe_service_line(s)}")
        text = "\n".join(lines)
    else:
        text = f"🛠 {profile.list_title}: пока пусто. Добавьте первую позицию."

    rows = [
        [InlineKeyboardButton(
            text=f"🗑 {s.get('name', '—')}",
            callback_data=f"own:srvdel:{s.get('service_id')}",
        )]
        for s in services
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="own:srvadd")])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="own:menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@owner_router.callback_query(F.data == "own:srv")
async def own_services(call: types.CallbackQuery):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    text, kb = await _services_view(tenant["tenant_id"], tenant.get("category", "other"))
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
    await safe_answer(call, "Удалено")
    text, kb = await _services_view(tenant["tenant_id"], tenant.get("category", "other"))
    await safe_edit_message(call, text, kb)


def _step_prompt(step: dict, index: int, total: int) -> str:
    prompt = f"Шаг {index + 1}/{total}. {step['prompt']}"
    example = step.get("example") or ""
    if example:
        prompt += f" (например, {example})"
    if not step.get("required"):
        prompt += "\nЕсли нет — отправьте «-»."
    return prompt


async def _ask_service_step(message: types.Message, state: FSMContext, prefix: str = ""):
    data = await state.get_data()
    profile = profiles.PROFILES.get(data.get("profile_key")) or profiles.get_profile("other")
    queue = data.get("queue") or []
    index = data.get("index", 0)

    if index >= len(queue):
        await _finalize_service(message, state, profile)
        return

    step = queue[index]
    total = 3 + len(queue)
    text = _step_prompt(step, index + 3, total)
    if prefix:
        text = f"{prefix}\n\n{text}"
    await state.set_state(OwnerServiceSG.waiting_field)
    await message.answer(text)


async def _finalize_service(message: types.Message, state: FSMContext, profile):
    data = await state.get_data()
    duration = int(data.get("duration_minutes") or profile.default_duration)
    details = {k: v for k, v in (data.get("details") or {}).items() if v not in (None, "")}
    service = {
        "tenant_id": data.get("tid"),
        "service_id": str(uuid.uuid4())[:8],
        "name": data.get("name", "Услуга"),
        "description": "",
        "price": data.get("price", 0),
        "currency": data.get("currency", "KGS"),
        "duration_minutes": duration,
        "details": details,
        "is_active": True,
    }
    if db is not None and db.db is not None:
        await db.db.services.insert_one(service)

    lines = [
        f"✅ Добавлено: «{service['name']}»",
        f"💰 {profiles.format_price(service['price'])} {service['currency']} · ⏱ {duration} мин",
    ]
    for key, value in details.items():
        lines.append(f"• {profiles.field_label(profile, key)}: {value}")

    tid = data.get("tid")
    await state.clear()
    await message.answer("\n".join(lines))
    text, kb = await _services_view(tid, profile.key)
    await message.answer(text, reply_markup=kb)


@owner_router.callback_query(F.data == "own:srvadd")
async def own_service_add(call: types.CallbackQuery, state: FSMContext):
    tenant = await _owner_guard(call)
    if not tenant:
        return
    await state.clear()
    profile = profiles.get_profile(tenant.get("category", "other"))
    queue = profiles.build_field_queue(profile)
    await state.set_state(OwnerServiceSG.waiting_name)
    await state.update_data(
        tid=tenant["tenant_id"],
        profile_key=profile.key,
        queue=queue,
        index=0,
        details={},
    )
    total = 3 + len(queue)
    await safe_edit_message(
        call,
        f"➕ {profile.add_title} для «{profile.title}».\n\n"
        f"Шаг 1/{total}. {profile.name_label} "
        f"(например, {profile.name_example}):",
        _back_kb(),
    )
    await safe_answer(call)


@owner_router.message(OwnerServiceSG.waiting_name)
async def own_service_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    data = await state.get_data()
    profile = profiles.get_profile(data.get("profile_key", "other"))
    if not name:
        await message.answer(f"{profile.name_label} не может быть пустым. Введите заново:")
        return
    if len(name) > 80:
        await message.answer("Слишком длинно — до 80 символов:")
        return
    await state.update_data(name=name)
    total = 3 + len(data.get("queue") or [])
    await state.set_state(OwnerServiceSG.waiting_price)
    await message.answer(f"Шаг 2/{total}. Введите цену (например, 1500):")


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
    data = await state.get_data()
    total = 3 + len(data.get("queue") or [])
    await state.set_state(OwnerServiceSG.waiting_currency)
    rows = [[InlineKeyboardButton(text=c, callback_data=f"own:srvcur:{c}")] for c in CURRENCIES]
    await message.answer(
        f"Шаг 3/{total}. Выберите валюту:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@owner_router.callback_query(OwnerServiceSG.waiting_currency, F.data.startswith("own:srvcur:"))
async def own_service_currency(call: types.CallbackQuery, state: FSMContext):
    currency = call.data.split(":", 2)[2]
    await state.update_data(currency=currency)
    await safe_edit_message(call, f"Валюта: {currency}")
    await safe_answer(call)
    await _ask_service_step(call.message, state)


@owner_router.message(OwnerServiceSG.waiting_field)
async def own_service_field(message: types.Message, state: FSMContext):
    data = await state.get_data()
    queue = data.get("queue") or []
    index = data.get("index", 0)
    if index >= len(queue):
        profile = profiles.get_profile(data.get("profile_key", "other"))
        await _finalize_service(message, state, profile)
        return

    step = queue[index]
    ok, value = profiles.parse_field_value(step, message.text or "")
    if not ok:
        await message.answer(value)
        return

    if step["key"] == "duration_minutes":
        await state.update_data(duration_minutes=value)
    else:
        details = dict(data.get("details") or {})
        if value is not None:
            details[step["key"]] = value
        await state.update_data(details=details)

    await state.update_data(index=index + 1)
    await _ask_service_step(message, state)


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
