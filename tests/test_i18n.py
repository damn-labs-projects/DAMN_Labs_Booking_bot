import asyncio
import unittest
from types import SimpleNamespace

from app import i18n
from app.bot.handlers import booking
from app.services import tenant_texts


def run(coro):
    return asyncio.run(coro)


def _get(doc: dict, path: str):
    value = doc
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


class _FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return self.docs[:length] if length else list(self.docs)


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.upserts = []

    def _matches(self, doc, query) -> bool:
        for path, expected in query.items():
            if _get(doc, path) != expected:
                return False
        return True

    def find(self, query=None):
        query = query or {}
        return _FakeCursor([d for d in self.docs if self._matches(d, query)])

    async def find_one(self, query):
        for doc in self.docs:
            if self._matches(doc, query):
                return doc
        return None

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(update.get("$set", {}))
                return
        if upsert:
            doc = {"_id": query.get("_id")}
            doc.update(update.get("$set", {}))
            self.docs.append(doc)
            self.upserts.append(doc)


class _FakeState:
    def __init__(self):
        self.data = {}
        self.state = None

    async def clear(self):
        self.data = {}
        self.state = None

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state):
        self.state = state


class _FakeMessage:
    def __init__(self, text="", user_id=1, token="tenant-token"):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id, full_name="Test User", first_name="Test")
        self.bot = SimpleNamespace(token=token)
        self.answers = []
        self.markups = []
        self.deleted = False

    async def answer(self, text, reply_markup=None, parse_mode=None, **kwargs):
        self.answers.append(text)
        self.markups.append(reply_markup)
        return self

    async def delete(self):
        self.deleted = True

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.answers.append(text)
        self.markups.append(reply_markup)


class _FakeCall:
    def __init__(self, data, message=None, user_id=1):
        self.data = data
        self.message = message or _FakeMessage()
        self.from_user = SimpleNamespace(id=user_id)
        self.bot = self.message.bot
        self.answers = []
        self.edited = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.message.answers.append(text)
        self.edited.append(text)


def _make_db(tenant=None, services=None):
    return SimpleNamespace(
        tenants=_FakeCollection([tenant] if tenant else []),
        services=_FakeCollection(services or []),
        appointments=_FakeCollection([]),
        users=_FakeCollection(),
    )


TENANT = {
    "tenant_id": "t1",
    "name": "Bistro",
    "bot_config": {"token": "tenant-token"},
    "subscription": {"status": "active"},
}

SERVICE = {
    "tenant_id": "t1",
    "service_id": "s1",
    "name": "Столик у окна",
    "price": 1500,
    "currency": "KGS",
    "duration_minutes": 90,
    "is_active": True,
    "details": {"table": "№5", "seats": 4},
}


class LangNormalizationTests(unittest.TestCase):
    def test_aliases_are_normalized(self):
        cases = {
            "ru": "ru",
            "RU": "ru",
            "ru-RU": "ru",
            "russian": "ru",
            "ky": "ky",
            "kg": "ky",
            "ky-KG": "ky",
            "kirghiz": "ky",
            "en": "en",
            "en-US": "en",
            "english": "en",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(i18n.normalize_lang(raw), expected)

    def test_unknown_language_falls_back_to_russian(self):
        for raw in ("de", "", None, 42, "zz-ZZ"):
            with self.subTest(raw=raw):
                self.assertEqual(i18n.normalize_lang(raw), "ru")

    def test_supported_languages_are_exactly_three(self):
        self.assertEqual(sorted(i18n.LANGUAGES), ["en", "ky", "ru"])

    def test_language_buttons_cover_every_language(self):
        codes = [code for code, _ in i18n.language_buttons()]
        self.assertEqual(codes, ["ru", "ky", "en"])


class TextCatalogTests(unittest.TestCase):
    def test_all_languages_share_the_same_keys(self):
        base = set(i18n.TEXTS["ru"])
        for code, texts in i18n.TEXTS.items():
            with self.subTest(code=code):
                self.assertEqual(set(texts), base)

    def test_all_languages_share_the_same_tenant_texts(self):
        base = set(i18n.TENANT_TEXTS["ru"])
        for code, texts in i18n.TENANT_TEXTS.items():
            with self.subTest(code=code):
                self.assertEqual(set(texts), base)

    def test_every_text_key_is_translated_in_every_language(self):
        for code in ("ky", "en"):
            for key in i18n.TEXTS[code]:
                with self.subTest(code=code, key=key):
                    self.assertNotEqual(i18n.TEXTS[code][key], i18n.TEXTS["ru"][key])

    def test_translation_falls_back_to_russian(self):
        self.assertEqual(i18n.t("de", "pick.date"), i18n.TEXTS["ru"]["pick.date"])

    def test_translation_interpolates_values(self):
        self.assertIn("+996700123456", i18n.t("ky", "phone.invalid", button="📱"))

    def test_weekdays_are_localized(self):
        self.assertEqual(i18n.weekdays("en")[0], "Mon")
        self.assertEqual(i18n.weekdays("ru")[0], "Пн")
        self.assertEqual(i18n.weekdays("ky")[0], "Дш")
        self.assertEqual(len(i18n.weekdays("en")), 7)

    def test_detail_labels_are_localized(self):
        service = {"details": {"table": "№5", "seats": 4}}

        self.assertIn("Booking", i18n.format_details(service, "en"))
        self.assertIn("Бронь", i18n.format_details(service, "ru"))
        self.assertIn("Бронь", i18n.format_details(service, "ky"))

    def test_detail_formatting_skips_empty_values(self):
        self.assertEqual(i18n.format_details({"details": {"table": ""}}, "en"), "")

    def test_service_button_is_localized(self):
        service = {
            "name": "Table",
            "price": 1500,
            "currency": "KGS",
            "duration_minutes": 90,
            "details": {"table": "№5"},
        }

        en = i18n.service_button(service, "en")
        ru = i18n.service_button(service, "ru")

        self.assertIn("90 min", en)
        self.assertIn("Booking: №5", en)
        self.assertIn("90 мин", ru)
        self.assertIn("Бронь: №5", ru)

    def test_service_button_fits_telegram_limit(self):
        service = {"name": "Long " * 40, "price": 1, "currency": "KGS", "duration_minutes": 60}

        self.assertLessEqual(len(i18n.service_button(service, "en")), 120)

    def test_tenant_texts_are_localized(self):
        self.assertIn("Welcome to", i18n.tenant_text("en", "welcome"))
        self.assertIn("кош келдиңиз", i18n.tenant_text("ky", "welcome"))

    def test_tenant_custom_text_wins_over_localization(self):
        tenant = {"name": "Bistro", "texts": {"welcome": "Custom {name}!"}}

        self.assertEqual(tenant_texts.render(tenant, "welcome", lang="en"), "Custom Bistro!")

    def test_tenant_default_text_is_localized(self):
        tenant = {"name": "Bistro"}

        self.assertIn("Bistro", tenant_texts.render(tenant, "welcome", lang="en"))
        self.assertIn("Bistro", tenant_texts.render(tenant, "welcome", lang="ky"))


class UserLanguageStorageTests(unittest.TestCase):
    def setUp(self):
        self.old_db = i18n.db.db
        self.fake = _make_db()
        i18n.db.db = self.fake

    def tearDown(self):
        i18n.db.db = self.old_db

    def test_language_is_stored_and_read_back(self):
        run(i18n.set_user_lang(42, "ky"))

        self.assertEqual(self.fake.users.docs[0]["_id"], 42)
        self.assertEqual(self.fake.users.docs[0]["lang"], "ky")
        self.assertEqual(run(i18n.get_user_lang(42)), "ky")

    def test_missing_language_returns_none(self):
        self.assertIsNone(run(i18n.get_user_lang(999)))

    def test_language_is_updated_in_place(self):
        run(i18n.set_user_lang(42, "ru"))
        run(i18n.set_user_lang(42, "en"))

        self.assertEqual(len(self.fake.users.docs), 1)
        self.assertEqual(run(i18n.get_user_lang(42)), "en")


class BookingLanguageFlowTests(unittest.TestCase):
    def setUp(self):
        self.old_db = booking.db.db
        self.fake = _make_db(TENANT, [SERVICE])
        booking.db.db = self.fake
        i18n.db.db = self.fake

    def tearDown(self):
        booking.db.db = self.old_db
        i18n.db.db = self.old_db

    def test_first_start_asks_for_language(self):
        message = _FakeMessage("/start")
        state = _FakeState()

        run(booking.cmd_start(message, state))

        self.assertEqual(len(message.answers), 1)
        self.assertIn("Выберите язык", message.answers[0])
        self.assertEqual(state.state, booking.BookingSG.selecting_language)

    def test_language_picker_has_three_buttons(self):
        message = _FakeMessage("/start")
        state = _FakeState()

        run(booking.cmd_start(message, state))

        markup = message.markups[0]
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertEqual([b.callback_data for b in buttons], ["lang:ru", "lang:ky", "lang:en"])
        self.assertIn("🇰🇬 Кыргызча", [b.text for b in buttons])

    def test_choosing_language_saves_it_and_shows_services(self):
        message = _FakeMessage("/start")
        state = _FakeState()
        run(booking.cmd_start(message, state))

        call = _FakeCall("lang:en", message=message)
        run(booking.process_language_choice(call, state))

        self.assertEqual(run(i18n.get_user_lang(1)), "en")
        self.assertIn("Welcome to Bistro", message.answers[-1])
        self.assertEqual(state.state, booking.BookingSG.selecting_service)

    def test_start_after_language_is_set_skips_picker(self):
        run(i18n.set_user_lang(1, "ky"))
        message = _FakeMessage("/start")
        state = _FakeState()

        run(booking.cmd_start(message, state))

        self.assertIn("кош келдиңиз", message.answers[0])
        self.assertNotIn("Выберите язык", message.answers[0])

    def test_date_buttons_are_localized(self):
        run(i18n.set_user_lang(1, "en"))
        call = _FakeCall("srv:s1")
        state = _FakeState()

        run(booking.process_service_choice(call, state))

        labels = [b.text for row in call.message.markups[-1].inline_keyboard for b in row]
        self.assertIn("📅 Today", labels)
        self.assertIn("📅 Tomorrow", labels)
        self.assertTrue(any(label.startswith("📅 Mon") for label in labels), labels)

    def test_date_buttons_use_kyrillic_weekdays(self):
        run(i18n.set_user_lang(1, "ky"))
        call = _FakeCall("srv:s1")
        state = _FakeState()

        run(booking.process_service_choice(call, state))

        labels = [b.text for row in call.message.markups[-1].inline_keyboard for b in row]
        self.assertIn("📅 Бүгүн", labels)
        self.assertIn("📅 Эртең", labels)

    def test_help_is_localized(self):
        run(i18n.set_user_lang(1, "en"))
        message = _FakeMessage("/help")
        state = _FakeState()

        run(booking.cmd_help(message, state))

        self.assertIn("/language", message.answers[0])
        self.assertIn("Available commands", message.answers[0])

    def test_language_command_shows_picker(self):
        run(i18n.set_user_lang(1, "ru"))
        message = _FakeMessage("/language")
        state = _FakeState()

        run(booking.cmd_language(message, state))

        self.assertIn("Выберите язык", message.answers[0])

    def test_help_has_language_button(self):
        run(i18n.set_user_lang(1, "en"))
        message = _FakeMessage("/help")
        state = _FakeState()

        run(booking.cmd_help(message, state))

        buttons = [b for row in message.markups[-1].inline_keyboard for b in row]
        self.assertEqual([b.callback_data for b in buttons], ["langpick"])
        self.assertEqual(buttons[0].text, "🌐 Language")

    def test_language_pick_button_opens_picker(self):
        run(i18n.set_user_lang(1, "en"))
        call = _FakeCall("langpick")
        state = _FakeState()

        run(booking.process_language_pick(call, state))

        self.assertIn("Выберите язык", call.message.answers[-1])
        self.assertEqual(state.state, booking.BookingSG.selecting_language)

    def test_my_bookings_empty_message_is_localized(self):
        run(i18n.set_user_lang(1, "en"))
        message = _FakeMessage("/my")
        state = _FakeState()

        run(booking.cmd_my(message, state))

        self.assertIn("no upcoming bookings", message.answers[0])

    def test_cancel_message_is_localized(self):
        run(i18n.set_user_lang(1, "ky"))
        message = _FakeMessage("/cancel")
        state = _FakeState()

        run(booking.cmd_cancel(message, state))

        self.assertIn("Амал жокко чыгарылды", message.answers[0])


if __name__ == "__main__":
    unittest.main()
