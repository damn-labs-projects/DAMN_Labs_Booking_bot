from typing import Any


DEFAULT_TEXTS = {
    "welcome": "Добро пожаловать в {name}!",
    "services_prompt": "Выберите услугу:",
    "phone_prompt": "Отправьте ваш номер телефона кнопкой ниже или напишите его сообщением:",
    "confirmation_title": "✅ Вы успешно записаны!",
    "notify_title": "🔔 Новая запись!",
}

TEXT_LABELS = {
    "welcome": "Приветствие",
    "services_prompt": "Заголовок выбора услуг",
    "phone_prompt": "Просьба отправить телефон",
    "confirmation_title": "Заголовок подтверждения",
    "notify_title": "Заголовок уведомления владельцу",
}


def get_texts(tenant: dict) -> dict:
    texts = dict(DEFAULT_TEXTS)
    texts.update(tenant.get("texts") or {})
    return texts


def render(tenant: dict, key: str, **extra: Any) -> str:
    template = get_texts(tenant).get(key, DEFAULT_TEXTS.get(key, ""))
    brand = tenant.get("branding") or {}
    values = {
        "name": tenant.get("name", ""),
        "emoji": brand.get("emoji", ""),
        "tagline": brand.get("description", ""),
    }
    values.update(extra)
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template
