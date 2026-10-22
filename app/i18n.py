import logging
from typing import Any, Optional

from app.db.mongodb import db

logger = logging.getLogger(__name__)

DEFAULT_LANG = "ru"

LANGUAGES: dict[str, dict[str, str]] = {
    "ru": {"emoji": "🇷🇺", "label": "Русский"},
    "ky": {"emoji": "🇰🇬", "label": "Кыргызча"},
    "en": {"emoji": "🇬🇧", "label": "English"},
}

PICKER_TEXT = "🌐 Выберите язык · Тилди тандаңыз · Choose your language"

LANG_ALIASES = {
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
    "kk": "ky",
    "kg": "ky",
    "ky": "ky",
    "ky-kg": "ky",
    "kirghiz": "ky",
    "kyrgyz": "ky",
    "en": "en",
    "eng": "en",
    "english": "en",
}

TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "lang.choose": "🌐 Выберите язык",
        "lang.changed": "✅ Язык изменён",
        "lang.hint": "Язык можно поменять в любой момент командой /language.",
        "lang.start_again": "Начните запись — /start.",
        "start.owner": "👋 Вы владелец бизнеса «{name}».\nПанель управления — командой /owner или кнопкой ниже.",
        "start.owner_button": "⚙️ Панель владельца",
        "start.no_services": "На данный момент услуг нет.",
        "error.tenant_not_found": "Ошибка: Заведение не найдено.",
        "error.paused": "Обслуживание заведения временно приостановлено.",
        "help.commands": (
            "📌 Доступные команды:\n"
            "/start — начать запись\n"
            "/my — мои записи\n"
            "/language — язык интерфейса\n"
            "/cancel — отменить текущее действие\n"
            "/help — помощь"
        ),
        "cancel.done": "Действие отменено. Чтобы начать заново — /start.",
        "my.empty": "📅 У вас нет предстоящих записей.",
        "my.title": "📅 Ваши записи:",
        "my.cancel_button": "❌ Отменить {when}",
        "my.not_found": "Запись не найдена.",
        "date.today": "Сегодня",
        "date.tomorrow": "Завтра",
        "pick.date": "Выберите дату:",
        "pick.time": "Выберите время:",
        "pick.no_slots": "На эту дату нет свободных слотов.",
        "phone.share_button": "📱 Поделиться контактом",
        "phone.enter": "Отправьте номер телефона или нажмите «{button}».",
        "phone.invalid": (
            "Похоже, это не номер телефона. Отправьте номер, например +996700123456, "
            "или нажмите «{button}»."
        ),
        "session.expired": "Сессия записи устарела. Начните заново — /start.",
        "confirm.venue": "📍 **Заведение:** {name}",
        "confirm.service": "💇 **Услуга:** {name}",
        "confirm.datetime": "📅 **Дата и время:** {date} {time}",
        "confirm.price": "💰 **Стоимость:** {price} {currency}",
        "confirm.my": "Мои записи — /my.",
        "notify.client": "👤 **Клиент:** {name} ({phone})",
        "notify.service": "🛠 **Услуга:** {name}",
        "notify.time": "🕒 **Время:** {date} {time}",
        "unit.minute": "мин",
        "lang.button": "🌐 Язык",
    },
    "ky": {
        "lang.choose": "🌐 Тилди тандаңыз",
        "lang.changed": "✅ Тил өзгөртүлдү",
        "lang.hint": "Тилди каалаган убакта /language командасы аркылуу алмаштыра аласыз.",
        "lang.start_again": "Жазылууну баштоо — /start.",
        "start.owner": (
            "👋 Сиз «{name}» бизнесинин ээсисиз.\n"
            "Башкаруу панели — /owner командасы же төмөндкү батырма аркылуу."
        ),
        "start.owner_button": "⚙️ Ээ панели",
        "start.no_services": "Азыр кызматтар жок.",
        "error.tenant_not_found": "Ката: Мекеме табылган жок.",
        "error.paused": "Мекеменин кызматтары убактылык токтотулду.",
        "help.commands": (
            "📌 Жеткиликтүү буйруктар:\n"
            "/start — жазылууну баштоо\n"
            "/my — менин жазылымдарым\n"
            "/language — интерфейс тили\n"
            "/cancel — амалды жокко чыгаруу\n"
            "/help — жардам"
        ),
        "cancel.done": "Амал жокко чыгарылды. Кайтадан баштоо үчүн — /start.",
        "my.empty": "📅 Алдыңкы жазылымдарыңыз жок.",
        "my.title": "📅 Сиздин жазылымдарыңыз:",
        "my.cancel_button": "❌ Жокко чыгаруу {when}",
        "my.not_found": "Жазылым табылган жок.",
        "date.today": "Бүгүн",
        "date.tomorrow": "Эртең",
        "pick.date": "Күндү тандаңыз:",
        "pick.time": "Убакытты тандаңыз:",
        "pick.no_slots": "Бул күнгө бош убакыт жок.",
        "phone.share_button": "📱 Байланыш маалыматын бөлүшүү",
        "phone.enter": "Телефон номуруңузду «{button}» батырмасы аркылуу жөткөрүңүз же билдириш жазыңыз.",
        "phone.invalid": (
            "Бул телефон номуруна окшош эмес. Номуру жазыңыз, мисалы +996700123456, "
            "же «{button}» батырмасын басыңыз."
        ),
        "session.expired": "Жазылуу сессиясы эскирип кетти. Кайтадан баштаңыз — /start.",
        "confirm.venue": "📍 **Мекеме:** {name}",
        "confirm.service": "💇 **Кызмат:** {name}",
        "confirm.datetime": "📅 **Күн жана убакыт:** {date} {time}",
        "confirm.price": "💰 **Баасы:** {price} {currency}",
        "confirm.my": "Менин жазылымдарым — /my.",
        "notify.client": "👤 **Кардар:** {name} ({phone})",
        "notify.service": "🛠 **Кызмат:** {name}",
        "notify.time": "🕒 **Убакыт:** {date} {time}",
        "unit.minute": "мүн",
        "lang.button": "🌐 Тил",
    },
    "en": {
        "lang.choose": "🌐 Choose your language",
        "lang.changed": "✅ Language changed",
        "lang.hint": "You can switch the language anytime with /language.",
        "lang.start_again": "Start booking — /start.",
        "start.owner": (
            "👋 You own «{name}».\n"
            "Open the owner panel with /owner or the button below."
        ),
        "start.owner_button": "⚙️ Owner panel",
        "start.no_services": "There are no services yet.",
        "error.tenant_not_found": "Error: business not found.",
        "error.paused": "This business is temporarily unavailable.",
        "help.commands": (
            "📌 Available commands:\n"
            "/start — start booking\n"
            "/my — my bookings\n"
            "/language — interface language\n"
            "/cancel — cancel current action\n"
            "/help — help"
        ),
        "cancel.done": "Action cancelled. To start over — /start.",
        "my.empty": "📅 You have no upcoming bookings.",
        "my.title": "📅 Your bookings:",
        "my.cancel_button": "❌ Cancel {when}",
        "my.not_found": "Booking not found.",
        "date.today": "Today",
        "date.tomorrow": "Tomorrow",
        "pick.date": "Choose a date:",
        "pick.time": "Choose a time:",
        "pick.no_slots": "No free slots for this date.",
        "phone.share_button": "📱 Share contact",
        "phone.enter": "Send your phone number with the «{button}» button or type it as a message.",
        "phone.invalid": (
            "That doesn't look like a phone number. Send a number, "
            "e.g. +996700123456, or tap «{button}»."
        ),
        "session.expired": "Your booking session expired. Start again — /start.",
        "confirm.venue": "📍 **Business:** {name}",
        "confirm.service": "💇 **Service:** {name}",
        "confirm.datetime": "📅 **Date and time:** {date} {time}",
        "confirm.price": "💰 **Price:** {price} {currency}",
        "confirm.my": "My bookings — /my.",
        "notify.client": "👤 **Client:** {name} ({phone})",
        "notify.service": "🛠 **Service:** {name}",
        "notify.time": "🕒 **Time:** {date} {time}",
        "unit.minute": "min",
        "lang.button": "🌐 Language",
    },
}

WEEKDAYS: dict[str, list[str]] = {
    "ru": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
    "ky": ["Дш", "Сэ", "Чэ", "Пэ", "Жм", "Жб", "Жк"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}

TENANT_TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "welcome": "Добро пожаловать в {name}!",
        "services_prompt": "Выберите услугу:",
        "phone_prompt": "Отправьте ваш номер телефона кнопкой ниже или напишите его сообщением:",
        "confirmation_title": "✅ Вы успешно записаны!",
        "notify_title": "🔔 Новая запись!",
    },
    "ky": {
        "welcome": "{name} бизнесине кош келдиңиз!",
        "services_prompt": "Кызматты тандаңыз:",
        "phone_prompt": "Телефон номуруңузду төмөндкү батырма менен жөткөрүңүз же билдириш жазыңыз:",
        "confirmation_title": "✅ Сиз ийгиликтүү жазылдыңыз!",
        "notify_title": "🔔 Жаңы жазылым!",
    },
    "en": {
        "welcome": "Welcome to {name}!",
        "services_prompt": "Choose a service:",
        "phone_prompt": "Send your phone number using the button below or type it as a message:",
        "confirmation_title": "✅ You are booked!",
        "notify_title": "🔔 New booking!",
    },
}

DETAIL_LABELS: dict[str, dict[str, str]] = {
    "master": {"ru": "Мастер", "ky": "Уста", "en": "Specialist"},
    "specialist": {"ru": "Врач", "ky": "Доктор", "en": "Doctor"},
    "room": {"ru": "Кабинет", "ky": "Кабинет", "en": "Room"},
    "format": {"ru": "Формат", "ky": "Формат", "en": "Format"},
    "teacher": {"ru": "Преподаватель", "ky": "Мугалим", "en": "Teacher"},
    "bay": {"ru": "Пост", "ky": "Пост", "en": "Bay"},
    "table": {"ru": "Бронь", "ky": "Бронь", "en": "Booking"},
    "seats": {"ru": "Гостей", "ky": "Мейкиндик", "en": "Capacity"},
    "note": {"ru": "Условия", "ky": "Шарттар", "en": "Conditions"},
}


def normalize_lang(code: Any) -> str:
    if not code:
        return DEFAULT_LANG
    raw = str(code).strip().lower().replace("_", "-")
    if raw in LANG_ALIASES:
        return LANG_ALIASES[raw]
    base = raw.split("-", 1)[0]
    return LANG_ALIASES.get(base, DEFAULT_LANG)


def is_supported(code: Any) -> bool:
    raw = str(code or "").strip().lower().replace("_", "-")
    return raw in LANG_ALIASES or raw.split("-", 1)[0] in LANG_ALIASES


def t(lang: Any, key: str, **kwargs: Any) -> str:
    code = normalize_lang(lang)
    template = TEXTS.get(code, {}).get(key) or TEXTS[DEFAULT_LANG].get(key) or key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def tenant_text(lang: Any, key: str) -> str:
    code = normalize_lang(lang)
    return TENANT_TEXTS.get(code, {}).get(key) or TENANT_TEXTS[DEFAULT_LANG].get(key) or ""


def weekdays(lang: Any) -> list[str]:
    return list(WEEKDAYS.get(normalize_lang(lang), WEEKDAYS[DEFAULT_LANG]))


def language_buttons() -> list[tuple[str, str]]:
    return [(code, f"{data['emoji']} {data['label']}") for code, data in LANGUAGES.items()]


def detail_label(key: str, lang: Any) -> str:
    code = normalize_lang(lang)
    return DETAIL_LABELS.get(key, {}).get(code) or DETAIL_LABELS.get(key, {}).get(DEFAULT_LANG, key)


def format_details(service: dict, lang: Any, inline: bool = False) -> str:
    details = (service or {}).get("details") or {}
    if not isinstance(details, dict) or not details:
        return ""
    parts = []
    for key, value in details.items():
        if value in (None, ""):
            continue
        parts.append(f"{detail_label(key, lang)}: {value}")
    if inline:
        return " · ".join(parts)
    return "\n".join(f"• {part}" for part in parts)


def service_button(service: dict, lang: Any) -> str:
    price = (service or {}).get("price", "—")
    if isinstance(price, (int, float)) and not isinstance(price, bool):
        price = int(price) if float(price) == int(price) else f"{price:.2f}".rstrip("0").rstrip(".")
    currency = (service or {}).get("currency", "")
    duration = (service or {}).get("duration_minutes", 60)
    text = f"{service.get('name', '—')} — {price} {currency} ({duration} {t(lang, 'unit.minute')})"
    details = format_details(service, lang, inline=True)
    if details:
        text += f" · {details}"
    return text[:120]


async def get_user_lang(telegram_id: Any) -> Optional[str]:
    if telegram_id is None or db.db is None:
        return None
    try:
        doc = await db.db.users.find_one({"_id": int(telegram_id)})
    except Exception as exc:
        logger.warning("Failed to read user language: %s", exc)
        return None
    lang = (doc or {}).get("lang")
    return normalize_lang(lang) if lang else None


async def set_user_lang(telegram_id: Any, lang: Any) -> str:
    code = normalize_lang(lang)
    if telegram_id is None:
        return code
    if db.db is None:
        return code
    try:
        await db.db.users.update_one(
            {"_id": int(telegram_id)},
            {"$set": {"lang": code}},
            upsert=True,
        )
    except Exception as exc:
        logger.warning("Failed to store user language: %s", exc)
    return code
