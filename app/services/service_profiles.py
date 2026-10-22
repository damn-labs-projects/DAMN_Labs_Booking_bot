from dataclasses import dataclass
from typing import Any


KIND_TEXT = "text"
KIND_INT = "int"

SKIP_TOKENS = {"-", "пропустить", "skip", "нет", "no"}

CATEGORY_LABELS = {
    "beauty": "💇 Красота и здоровье",
    "medical": "🏥 Медицина",
    "education": "🎓 Обучение",
    "auto": "🚗 Авто",
    "cafe": "☕ Кафе / ресторан",
    "other": "🏢 Другое",
}

CATEGORY_ALIASES = {
    "beauty": ("beauty", "salon", "салон", "красота", "barber", "барбер", "барбершоп", "парикмахер", "ногти", "spa", "спа"),
    "medical": ("medical", "медицина", "клиника", "стоматолог", "доктор", "врач", "аптека"),
    "education": ("education", "обучение", "школа", "курсы", "репетитор", "образование"),
    "auto": ("auto", "авто", "автосервис", "сто", "автомойка", "шиномонтаж"),
    "cafe": ("cafe", "café", "кафе", "кофейня", "restaurant", "ресторан", "бар", "bar", "кофе", "столовая"),
}


@dataclass(frozen=True)
class ServiceField:
    key: str
    prompt: str
    label: str = ""
    example: str = ""
    kind: str = KIND_TEXT
    required: bool = False


@dataclass(frozen=True)
class ServiceProfile:
    key: str
    title: str
    list_title: str
    add_title: str
    name_label: str
    name_example: str
    duration_label: str
    default_duration: int
    ask_duration: bool = True
    fields: tuple[ServiceField, ...] = ()


PROFILES: dict[str, ServiceProfile] = {
    "beauty": ServiceProfile(
        key="beauty",
        title="Красота и здоровье",
        list_title="Ваши услуги",
        add_title="Новая услуга",
        name_label="Название услуги",
        name_example="Стрижка женская, Маникюр, Массаж",
        duration_label="Длительность процедуры, мин",
        default_duration=60,
        fields=(
            ServiceField(
                key="master",
                prompt="Мастер / специалист",
                label="Мастер",
                example="Айгуль",
            ),
        ),
    ),
    "medical": ServiceProfile(
        key="medical",
        title="Медицина",
        list_title="Ваши услуги",
        add_title="Новая услуга",
        name_label="Название услуги",
        name_example="Консультация терапевта, УЗИ, Чистка зубов",
        duration_label="Длительность приёма, мин",
        default_duration=30,
        fields=(
            ServiceField(
                key="specialist",
                prompt="Врач / специалист",
                label="Врач",
                example="Терапевт Иванов",
            ),
            ServiceField(
                key="room",
                prompt="Кабинет / номер",
                label="Кабинет",
                example="Кабинет 3",
            ),
        ),
    ),
    "education": ServiceProfile(
        key="education",
        title="Обучение",
        list_title="Ваши занятия",
        add_title="Новое занятие",
        name_label="Название занятия",
        name_example="Английский для начинающих, Подготовка к экзамену",
        duration_label="Длительность занятия, мин",
        default_duration=60,
        fields=(
            ServiceField(
                key="format",
                prompt="Формат занятия",
                label="Формат",
                example="Группа до 6 человек / индивидуально",
            ),
            ServiceField(
                key="teacher",
                prompt="Преподаватель",
                label="Преподаватель",
                example="Азамат",
            ),
        ),
    ),
    "auto": ServiceProfile(
        key="auto",
        title="Авто",
        list_title="Ваши услуги",
        add_title="Новая услуга",
        name_label="Название услуги",
        name_example="Замена масла, Шиномонтаж, Диагностика",
        duration_label="Длительность работ, мин",
        default_duration=60,
        fields=(
            ServiceField(
                key="bay",
                prompt="Пост / бокс",
                label="Пост",
                example="Пост 2",
            ),
        ),
    ),
    "cafe": ServiceProfile(
        key="cafe",
        title="Кафе / ресторан",
        list_title="Ваши брони",
        add_title="Новое бронирование",
        name_label="Название брони",
        name_example="Столик у окна, VIP-зал, Бизнес-ланч",
        duration_label="Среднее время визита, мин",
        default_duration=90,
        fields=(
            ServiceField(
                key="table",
                prompt="Что бронируем: номер столика, зал или зона",
                label="Бронь",
                example="Столик №5, VIP-зал у окна",
                required=True,
            ),
            ServiceField(
                key="seats",
                prompt="Вместимость, гостей",
                label="Гостей",
                example="4",
                kind=KIND_INT,
            ),
            ServiceField(
                key="note",
                prompt="Условия брони (депозит, длительность)",
                label="Условия",
                example="Депозит 1000 сом, бронь на 2 часа",
            ),
        ),
    ),
    "other": ServiceProfile(
        key="other",
        title="Другое",
        list_title="Ваши позиции",
        add_title="Новая позиция",
        name_label="Название",
        name_example="Консультация, Доставка, Аренда",
        duration_label="Длительность, мин",
        default_duration=60,
        fields=(),
    ),
}

DEFAULT_PROFILE_KEY = "other"


def normalize_category(category: Any) -> str:
    value = (category or "").strip().lower() if isinstance(category, str) else ""
    if value in PROFILES:
        return value
    for key, aliases in CATEGORY_ALIASES.items():
        if value in aliases:
            return key
    for key, aliases in CATEGORY_ALIASES.items():
        if value and any(alias in value for alias in aliases):
            return key
    return DEFAULT_PROFILE_KEY


def get_profile(category: Any) -> ServiceProfile:
    return PROFILES[normalize_category(category)]


def category_label(category: Any) -> str:
    return CATEGORY_LABELS[normalize_category(category)]


def build_field_queue(profile: ServiceProfile) -> list[dict]:
    queue: list[dict] = []
    if profile.ask_duration:
        queue.append({
            "key": "duration_minutes",
            "prompt": profile.duration_label,
            "example": str(profile.default_duration),
            "kind": KIND_INT,
            "required": True,
        })
    for field in profile.fields:
        queue.append({
            "key": field.key,
            "prompt": field.prompt,
            "label": field.label or field.prompt,
            "example": field.example,
            "kind": field.kind,
            "required": field.required,
        })
    return queue


def is_skip_token(raw: str) -> bool:
    return (raw or "").strip().lower() in SKIP_TOKENS


def parse_field_value(step: dict, raw: str) -> tuple[bool, Any]:
    text = (raw or "").strip()
    kind = step.get("kind", KIND_TEXT)
    required = bool(step.get("required"))

    if is_skip_token(text) or not text:
        if required:
            return False, "Это поле обязательное — введите значение."
        return True, None

    if kind == KIND_INT:
        if not text.isdigit():
            return False, "Введите целое число, например 4:"
        value = int(text)
        if step.get("key") == "duration_minutes":
            if not (1 <= value <= 480):
                return False, "Введите число от 1 до 480:"
        elif not (1 <= value <= 500):
            return False, "Введите число от 1 до 500:"
        return True, value

    if len(text) > 80:
        return False, "Слишком длинно — до 80 символов. Введите короче:"

    return True, text


def field_label(profile: ServiceProfile, key: str) -> str:
    if key == "duration_minutes":
        return profile.duration_label
    for field in profile.fields:
        if field.key == key:
            return field.label or field.prompt
    return key


def format_price(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value) == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_details(service: dict) -> str:
    details = service.get("details") or {}
    if not isinstance(details, dict):
        return ""
    parts = []
    for key, value in details.items():
        if value in (None, ""):
            continue
        if key == "seats":
            parts.append(f"{value} гостей")
        else:
            parts.append(str(value))
    return " · ".join(parts)


def describe_service_line(service: dict) -> str:
    price = format_price(service.get("price", "—"))
    currency = service.get("currency", "")
    duration = service.get("duration_minutes", 60)
    line = f"{service.get('name', '—')} — {price} {currency} ({duration} мин)"
    details = format_details(service)
    if details:
        line += f"\n   {details}"
    return line


def describe_button(service: dict) -> str:
    price = format_price(service.get("price", "—"))
    currency = service.get("currency", "")
    duration = service.get("duration_minutes", 60)
    details = format_details(service)
    text = f"{service.get('name', '—')} — {price} {currency} ({duration} мин)"
    if details:
        text += f" · {details}"
    return text[:120]
