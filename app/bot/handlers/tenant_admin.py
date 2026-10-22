import re
import uuid

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.mongodb import db
from app.services import payment_requests
from app.services import subscription
from app.services import service_profiles as profiles
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
    waiting_field = State()


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
        f"🏷 Категория: {profiles.category_label(tenant.get('category'))}\n"
        f"💳 Статус: {subscription.status_label(tenant)}\n"
        f"📅 Подписка до: {subscription.period_end_text(tenant)}\n"
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
                InlineKeyboardButton(text="💳 Подписка", callback_data=f"tadm:sub:{tid}"),
                InlineKeyboardButton(text="📨 Запросы", callback_data="tadm:reqs"),
            ],
            [
                InlineKeyboardButton(text="⬅️ К списку", callback_data="tadm:list"),
                InlineKeyboardButton(text="🏠 В меню", callback_data="tadm:menu"),
            ],
        ]
    )
    return text, kb


def build_subscription_screen(tenant: dict) -> str:
    tid = tenant["tenant_id"]
    status = subscription.status_of(tenant)
    end = subscription.period_end_text(tenant)
    blocked = subscription.is_blocked(tenant)
    note = "⛔ Обслуживание заведения приостановлено." if blocked else "✅ Заведения доступны для записи."
    return (
        f"💳 Подписка — «{tenant.get('name', '—')}»\n"
        "────────────────\n"
        f"🆔 {tid}\n"
        f"📌 Статус: {subscription.status_label(tenant)}\n"
        f"📅 Действует до: {end}\n"
        f"{note}\n\n"
        "Оплата сейчас ручная: продлите подписку здесь, "
        "и бизнес продолжит работать без ограничений."
    )


def build_subscription_kb(tid: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="➕ 7", callback_data=f"tadm:subext:{tid}:7"),
            InlineKeyboardButton(text="➕ 30", callback_data=f"tadm:subext:{tid}:30"),
            InlineKeyboardButton(text="➕ 90", callback_data=f"tadm:subext:{tid}:90"),
        ],
        [
            InlineKeyboardButton(text="➕ 365", callback_data=f"tadm:subext:{tid}:365"),
            InlineKeyboardButton(text="✅ Активировать", callback_data=f"tadm:subact:{tid}"),
        ],
        [InlineKeyboardButton(text="⛔ Отключить", callback_data=f"tadm:suboff:{tid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"tadm:open:{tid}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


@tenant_admin_router.callback_query(F.data.startswith("tadm:sub:"))
async def tadm_subscription(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    tenant = await _get_tenant(tid)
    if not tenant:
        await safe_answer(call, "Бизнес не найден.", show_alert=True)
        return
    await safe_edit_message(call, build_subscription_screen(tenant), build_subscription_kb(tid))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:subext:"))
async def tadm_subscription_extend(call: types.CallbackQuery):
    if not await _guard(call):
        return
    parts = call.data.split(":", 3)
    tid, days = parts[2], parts[3]
    if not days.isdigit() or int(days) <= 0:
        await safe_answer(call, "Некорректное количество дней.", show_alert=True)
        return
    new_end = await subscription.grant_days(tid, int(days))
    tenant = await _get_tenant(tid)
    if not tenant:
        await safe_answer(call, "Бизнес не найден.", show_alert=True)
        return
    if new_end is None:
        await safe_answer(call, "Не удалось сохранить подписку.", show_alert=True)
        return
    await safe_edit_message(
        call,
        f"✅ Подписка продлена на {days} дн.\n"
        f"📅 Действует до: {new_end.strftime('%d.%m.%Y')}\n\n"
        + build_subscription_screen(tenant),
        build_subscription_kb(tid),
    )
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:subact:"))
async def tadm_subscription_activate(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    new_end = await subscription.grant_days(tid, 30)
    tenant = await _get_tenant(tid)
    if not tenant:
        await safe_answer(call, "Бизнес не найден.", show_alert=True)
        return
    await safe_edit_message(
        call,
        "✅ Подписка активирована.\n"
        + build_subscription_screen(tenant),
        build_subscription_kb(tid),
    )
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:suboff:"))
async def tadm_subscription_disable(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    await subscription.set_status(tid, "expired")
    tenant = await _get_tenant(tid)
    if not tenant:
        await safe_answer(call, "Бизнес не найден.", show_alert=True)
        return
    await safe_edit_message(
        call,
        "⛔ Подписка отключена: заведения временно недоступны для записи.\n\n"
        + build_subscription_screen(tenant),
        build_subscription_kb(tid),
    )
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data == "tadm:reqs")
async def tadm_requests(call: types.CallbackQuery):
    if not await _guard(call):
        return
    requests = await payment_requests.list_requests("new")
    if not requests:
        await safe_edit_message(call, "📨 Новых запросов на оплату нет.", build_parent_menu())
        await safe_answer(call)
        return

    lines = ["📨 Запросы на оплату:"]
    rows = []
    for request in requests:
        created = request.get("created_at")
        created_text = created.strftime("%d.%m %H:%M") if hasattr(created, "strftime") else "—"
        lines.append(
            f"• {request.get('tenant_name', '—')} ({request.get('tenant_id')}) — "
            f"Telegram ID {request.get('contact')} — {created_text}"
        )
        rows.append([
            InlineKeyboardButton(
                text="➕ 30 дней",
                callback_data=f"tadm:reqgrant:{request.get('_id')}:{request.get('tenant_id')}",
            ),
            InlineKeyboardButton(
                text="✅ Закрыть",
                callback_data=f"tadm:reqclose:{request.get('_id')}",
            ),
        ])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="tadm:menu")])
    await safe_edit_message(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:reqgrant:"))
async def tadm_request_grant(call: types.CallbackQuery):
    if not await _guard(call):
        return
    parts = call.data.split(":", 3)
    request_id, tid = parts[2], parts[3]
    new_end = await subscription.grant_days(tid, 30)
    await payment_requests.close_request(request_id, "Выдано 30 дней")
    tenant = await _get_tenant(tid)
    text = "✅ Выдано 30 дней."
    if new_end is not None:
        text += f"\n📅 Действует до: {new_end.strftime('%d.%m.%Y')}"
    if tenant:
        text += "\n\n" + build_subscription_screen(tenant)
    await safe_edit_message(call, text, build_subscription_kb(tid))
    await safe_answer(call)


@tenant_admin_router.callback_query(F.data.startswith("tadm:reqclose:"))
async def tadm_request_close(call: types.CallbackQuery):
    if not await _guard(call):
        return
    request_id = call.data.split(":", 2)[2]
    await payment_requests.close_request(request_id, "Закрыт вручную")
    await safe_answer(call, "Закрыто")
    requests = await payment_requests.list_requests("new")
    if requests:
        lines = ["📨 Запросы на оплату:"]
        rows = []
        for request in requests:
            lines.append(
                f"• {request.get('tenant_name', '—')} ({request.get('tenant_id')}) — "
                f"Telegram ID {request.get('contact')}"
            )
            rows.append([
                InlineKeyboardButton(
                    text="➕ 30 дней",
                    callback_data=f"tadm:reqgrant:{request.get('_id')}:{request.get('tenant_id')}",
                ),
                InlineKeyboardButton(
                    text="✅ Закрыть",
                    callback_data=f"tadm:reqclose:{request.get('_id')}",
                ),
            ])
        rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="tadm:menu")])
        await safe_edit_message(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
        return
    await safe_edit_message(call, "📨 Новых запросов на оплату нет.", build_parent_menu())


@tenant_admin_router.callback_query(F.data.startswith("tadm:srv:"))
async def tadm_services(call: types.CallbackQuery):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    await _show_services(call, tid)
    await safe_answer(call)


async def _services_view(tid: str):
    services = []
    profile = profiles.get_profile("other")
    if db is not None and db.db is not None:
        tenant = await db.db.tenants.find_one({"tenant_id": tid})
        if tenant:
            profile = profiles.get_profile(tenant.get("category", "other"))
        cursor = db.db.services.find({"tenant_id": tid}).sort("name", 1)
        services = await cursor.to_list(length=50)

    if services:
        lines = [f"🛠 {profile.list_title} ({profiles.category_label(profile.key)}):"]
        for s in services:
            lines.append(f"• {profiles.describe_service_line(s)}")
        text = "\n".join(lines)
    else:
        text = f"🛠 {profile.list_title}: пока пусто. Добавьте первую позицию."

    rows = [
        [InlineKeyboardButton(
            text=f"🗑 {s.get('name', '—')}",
            callback_data=f"tadm:srvdel:{tid}:{s.get('service_id')}",
        )]
        for s in services
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data=f"tadm:srvadd:{tid}")])
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
    await safe_answer(call, "Удалено")
    await _show_services(call, tid)


@tenant_admin_router.callback_query(F.data.startswith("tadm:srvadd:"))
async def tadm_service_add(call: types.CallbackQuery, state: FSMContext):
    if not await _guard(call):
        return
    tid = call.data.split(":", 2)[2]
    await state.clear()
    category = "other"
    if db is not None and db.db is not None:
        tenant = await db.db.tenants.find_one({"tenant_id": tid})
        if tenant:
            category = tenant.get("category", "other")
    profile = profiles.get_profile(category)
    queue = profiles.build_field_queue(profile)
    await state.set_state(AddServiceSG.waiting_name)
    await state.update_data(
        tid=tid,
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
        _back_kb(tid),
    )
    await safe_answer(call)


@tenant_admin_router.message(AddServiceSG.waiting_name)
async def tadm_service_name(message: types.Message, state: FSMContext):
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
    await state.set_state(AddServiceSG.waiting_price)
    await message.answer(f"Шаг 2/{total}. Введите цену (например, 1500):")


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
    data = await state.get_data()
    total = 3 + len(data.get("queue") or [])
    await state.set_state(AddServiceSG.waiting_currency)
    rows = [[InlineKeyboardButton(text=c, callback_data=f"tadm:srvcur:{c}")] for c in CURRENCIES]
    await message.answer(
        f"Шаг 3/{total}. Выберите валюту:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@tenant_admin_router.callback_query(AddServiceSG.waiting_currency, F.data.startswith("tadm:srvcur:"))
async def tadm_service_currency(call: types.CallbackQuery, state: FSMContext):
    currency = call.data.split(":", 2)[2]
    await state.update_data(currency=currency)
    await safe_edit_message(call, f"Валюта: {currency}")
    await safe_answer(call)
    await _ask_service_step(call.message, state)


def _step_prompt(step: dict, index: int, total: int) -> str:
    prompt = f"Шаг {index + 1}/{total}. {step['prompt']}"
    example = step.get("example") or ""
    if example:
        prompt += f" (например, {example})"
    if not step.get("required"):
        prompt += "\nЕсли нет — отправьте «-»."
    return prompt


async def _ask_service_step(message: types.Message, state: FSMContext):
    data = await state.get_data()
    profile = profiles.PROFILES.get(data.get("profile_key")) or profiles.get_profile("other")
    queue = data.get("queue") or []
    index = data.get("index", 0)

    if index >= len(queue):
        await _finalize_service(message, state, profile)
        return

    step = queue[index]
    total = 3 + len(queue)
    await state.set_state(AddServiceSG.waiting_field)
    await message.answer(_step_prompt(step, index + 3, total))


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
    text, kb = await _services_view(tid)
    await message.answer(text, reply_markup=kb)


@tenant_admin_router.message(AddServiceSG.waiting_field)
async def tadm_service_field(message: types.Message, state: FSMContext):
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
