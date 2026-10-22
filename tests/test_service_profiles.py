import asyncio
import unittest
from types import SimpleNamespace

from app.services import service_profiles as profiles


def run(coro):
    return asyncio.run(coro)


class NormalizeCategoryTests(unittest.TestCase):
    def test_known_categories_map_to_themselves(self):
        for key in profiles.PROFILES:
            with self.subTest(key=key):
                self.assertEqual(profiles.normalize_category(key), key)

    def test_aliases_are_translated(self):
        cases = {
            "Кафе": "cafe",
            "ресторан": "cafe",
            "Кофейня у дома": "cafe",
            "salon": "beauty",
            "Стоматология": "medical",
            "Курсы английского": "education",
            "Автосервис": "auto",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(profiles.normalize_category(raw), expected)

    def test_unknown_and_empty_fall_back_to_other(self):
        for raw in ("", None, "цветочный магазин", "   ", 42):
            with self.subTest(raw=raw):
                self.assertEqual(profiles.normalize_category(raw), "other")

    def test_category_label_is_human_readable(self):
        self.assertEqual(profiles.category_label("кафе"), "☕ Кафе / ресторан")


class CafeProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = profiles.get_profile("cafe")

    def test_duration_is_still_asked_for_scheduling(self):
        self.assertTrue(self.profile.ask_duration)
        self.assertEqual(self.profile.default_duration, 90)

    def test_table_is_required_and_seats_are_numeric(self):
        fields = {f.key: f for f in self.profile.fields}

        self.assertEqual(set(fields), {"table", "seats", "note"})
        self.assertTrue(fields["table"].required)
        self.assertEqual(fields["seats"].kind, profiles.KIND_INT)
        self.assertFalse(fields["note"].required)

    def test_field_queue_starts_with_duration(self):
        queue = profiles.build_field_queue(self.profile)
        keys = [step["key"] for step in queue]

        self.assertEqual(keys, ["duration_minutes", "table", "seats", "note"])

    def test_other_category_keeps_the_old_single_duration_step(self):
        queue = profiles.build_field_queue(profiles.get_profile("other"))

        self.assertEqual([step["key"] for step in queue], ["duration_minutes"])


class ParseFieldValueTests(unittest.TestCase):
    def setUp(self):
        self.profile = profiles.get_profile("cafe")
        self.queue = {step["key"]: step for step in profiles.build_field_queue(self.profile)}

    def test_duration_accepts_reasonable_values(self):
        self.assertEqual(profiles.parse_field_value(self.queue["duration_minutes"], "45"), (True, 45))

    def test_duration_rejects_zero_text_and_out_of_range(self):
        for raw in ("0", "abc", "600", ""):
            with self.subTest(raw=raw):
                ok, message = profiles.parse_field_value(self.queue["duration_minutes"], raw)
                self.assertFalse(ok)
                self.assertIsInstance(message, str)

    def test_required_table_cannot_be_skipped(self):
        for raw in ("-", "", "skip"):
            with self.subTest(raw=raw):
                ok, _ = profiles.parse_field_value(self.queue["table"], raw)
                self.assertFalse(ok)

    def test_optional_seats_can_be_skipped(self):
        self.assertEqual(profiles.parse_field_value(self.queue["seats"], "-"), (True, None))
        self.assertEqual(profiles.parse_field_value(self.queue["seats"], ""), (True, None))
        self.assertEqual(profiles.parse_field_value(self.queue["seats"], "4"), (True, 4))

    def test_seats_rejects_non_numeric(self):
        ok, message = profiles.parse_field_value(self.queue["seats"], "четыре")
        self.assertFalse(ok)
        self.assertIn("число", message)

    def test_long_text_is_rejected(self):
        ok, _ = profiles.parse_field_value(self.queue["note"], "x" * 81)
        self.assertFalse(ok)


class DisplayHelpersTests(unittest.TestCase):
    def test_format_details_renders_seats_and_skips_empty(self):
        service = {"details": {"table": "Столик №5", "seats": 4, "note": ""}}

        self.assertEqual(profiles.format_details(service), "Столик №5 · 4 гостей")

    def test_format_details_handles_missing_or_broken_details(self):
        self.assertEqual(profiles.format_details({}), "")
        self.assertEqual(profiles.format_details({"details": None}), "")
        self.assertEqual(profiles.format_details({"details": "не словарь"}), "")

    def test_describe_service_line_includes_details(self):
        service = {
            "name": "Столик у окна",
            "price": 1000,
            "currency": "KGS",
            "duration_minutes": 90,
            "details": {"table": "Столик №5"},
        }

        line = profiles.describe_service_line(service)

        self.assertIn("Столик у окна", line)
        self.assertIn("90 мин", line)
        self.assertIn("Столик №5", line)

    def test_describe_button_is_short_enough_for_telegram(self):
        service = {
            "name": "Очень длинное название " * 5,
            "price": 1,
            "currency": "KGS",
            "duration_minutes": 60,
            "details": {"table": "Столик №5"},
        }

        self.assertLessEqual(len(profiles.describe_button(service)), 120)

    def test_price_is_rendered_without_trailing_zeros(self):
        self.assertEqual(profiles.format_price(1500.0), "1500")
        self.assertEqual(profiles.format_price(1500), "1500")
        self.assertEqual(profiles.format_price(1500.5), "1500.5")
        self.assertEqual(profiles.format_price("—"), "—")

    def test_field_label_prefers_short_label(self):
        cafe = profiles.get_profile("cafe")
        beauty = profiles.get_profile("beauty")

        self.assertEqual(profiles.field_label(cafe, "table"), "Бронь")
        self.assertEqual(profiles.field_label(cafe, "seats"), "Гостей")
        self.assertEqual(profiles.field_label(cafe, "duration_minutes"), cafe.duration_label)
        self.assertEqual(profiles.field_label(beauty, "master"), "Мастер")
        self.assertEqual(profiles.field_label(cafe, "unknown"), "unknown")


class _FakeState:
    def __init__(self, **data):
        self.data = dict(data)
        self.state = None

    async def set_state(self, state):
        self.state = state

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        return dict(self.data)

    async def clear(self):
        self.data = {}
        self.state = None


class _FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class _FakeBotMessage:
    def __init__(self):
        self.edited = []
        self.answers = []

    async def edit_text(self, text, reply_markup=None):
        self.edited.append(text)

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)


class _FakeCall:
    def __init__(self, data=""):
        self.data = data
        self.message = _FakeBotMessage()
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append(args)


class _FakeCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, length=None):
        return list(self.docs)


class _FakeServices:
    def __init__(self):
        self.docs = []
        self.inserted = []

    def find(self, query):
        tid = query.get("tenant_id")
        return _FakeCursor([d for d in self.docs if d.get("tenant_id") == tid])

    async def insert_one(self, doc):
        self.inserted.append(doc)
        self.docs.append(doc)


class _FakeDb:
    def __init__(self):
        self.services = _FakeServices()


class OwnerServiceWizardTests(unittest.TestCase):
    def setUp(self):
        from app.bot.handlers import owner as owner_module

        self.owner = owner_module
        self.old_db = owner_module.db
        self.old_guard = owner_module._owner_guard
        self.fake_db = _FakeDb()
        owner_module.db = SimpleNamespace(db=self.fake_db)
        self.tenant = {"tenant_id": "t1", "name": "Bistro", "category": "cafe"}

        async def guard(call):
            return self.tenant

        owner_module._owner_guard = guard

    def tearDown(self):
        self.owner.db = self.old_db
        self.owner._owner_guard = self.old_guard

    def _start(self):
        call = _FakeCall(data="own:srvadd")
        state = _FakeState()
        run(self.owner.own_service_add(call, state))
        return call, state

    def test_cafe_owner_is_asked_for_table_and_seats(self):
        call, state = self._start()

        prompt = call.message.edited[0]
        self.assertIn("Шаг 1/7", prompt)
        self.assertIn("Название брони", prompt)
        self.assertEqual(state.data["profile_key"], "cafe")
        self.assertEqual(
            [step["key"] for step in state.data["queue"]],
            ["duration_minutes", "table", "seats", "note"],
        )

    def test_full_cafe_flow_stores_details_and_duration(self):
        call, state = self._start()

        run(self.owner.own_service_name(_FakeMessage("Столик у окна"), state))
        run(self.owner.own_service_price(_FakeMessage("1500"), state))
        run(self.owner.own_service_currency(_FakeCall(data="own:srvcur:KGS"), state))
        run(self.owner.own_service_field(_FakeMessage("60"), state))
        run(self.owner.own_service_field(_FakeMessage("Столик №5"), state))
        run(self.owner.own_service_field(_FakeMessage("4"), state))
        run(self.owner.own_service_field(_FakeMessage("-"), state))

        self.assertEqual(len(self.fake_db.services.inserted), 1)
        service = self.fake_db.services.inserted[0]
        self.assertEqual(service["name"], "Столик у окна")
        self.assertEqual(service["price"], 1500.0)
        self.assertEqual(service["currency"], "KGS")
        self.assertEqual(service["duration_minutes"], 60)
        self.assertEqual(service["details"], {"table": "Столик №5", "seats": 4})
        self.assertEqual(state.data, {})

    def test_required_table_rejects_skip_and_keeps_step(self):
        _, state = self._start()
        run(self.owner.own_service_name(_FakeMessage("Бизнес-ланч"), state))
        run(self.owner.own_service_price(_FakeMessage("500"), state))
        run(self.owner.own_service_currency(_FakeCall(data="own:srvcur:KGS"), state))
        run(self.owner.own_service_field(_FakeMessage("60"), state))

        message = _FakeMessage("-")
        run(self.owner.own_service_field(message, state))

        self.assertEqual(state.data["index"], 1)
        self.assertIn("обязательное", message.answers[0])
        self.assertEqual(self.fake_db.services.inserted, [])

    def test_beauty_flow_has_one_optional_extra_step(self):
        self.tenant["category"] = "beauty"
        call, state = self._start()

        self.assertEqual(
            [step["key"] for step in state.data["queue"]],
            ["duration_minutes", "master"],
        )
        self.assertIn("Шаг 1/5", call.message.edited[0])

        run(self.owner.own_service_name(_FakeMessage("Стрижка"), state))
        run(self.owner.own_service_price(_FakeMessage("800"), state))
        run(self.owner.own_service_currency(_FakeCall(data="own:srvcur:KGS"), state))
        run(self.owner.own_service_field(_FakeMessage("45"), state))
        run(self.owner.own_service_field(_FakeMessage("-"), state))

        service = self.fake_db.services.inserted[0]
        self.assertEqual(service["duration_minutes"], 45)
        self.assertEqual(service["details"], {})

    def test_unknown_category_falls_back_to_single_duration_step(self):
        self.tenant["category"] = "цветы"
        call, state = self._start()

        self.assertEqual(
            [step["key"] for step in state.data["queue"]],
            ["duration_minutes"],
        )
        self.assertIn("Шаг 1/4", call.message.edited[0])


class TenantAdminWizardTests(unittest.TestCase):
    def setUp(self):
        from app.bot.handlers import tenant_admin as tadm_module

        self.tadm = tadm_module
        self.old_db = tadm_module.db
        self.old_guard = tadm_module._guard
        self.fake_db = _FakeDb()
        self.fake_db.tenants = self._Tenants({"tenant_id": "t1", "category": "кафе"})
        tadm_module.db = SimpleNamespace(db=self.fake_db)

    def tearDown(self):
        self.tadm.db = self.old_db
        self.tadm._guard = self.old_guard

    class _Tenants:
        def __init__(self, doc):
            self.doc = doc

        async def find_one(self, query):
            return self.doc if self.doc.get("tenant_id") == query.get("tenant_id") else None

    def test_services_view_uses_category_specific_listing(self):
        run(self.fake_db.services.insert_one({
            "tenant_id": "t1",
            "name": "Столик у окна",
            "price": 1000,
            "currency": "KGS",
            "duration_minutes": 90,
            "details": {"table": "Столик №5", "seats": 4},
        }))

        text, _ = run(self.tadm._services_view("t1"))

        self.assertIn("Ваши брони", text)
        self.assertIn("4 гостей", text)
        self.assertIn("Столик №5", text)

    def test_wizard_uses_cafe_profile_from_tenant_category(self):
        call = _FakeCall(data="tadm:srvadd:t1")
        state = _FakeState()

        async def guard(_call):
            return True

        self.tadm._guard = guard
        run(self.tadm.tadm_service_add(call, state))

        self.assertEqual(state.data["profile_key"], "cafe")
        self.assertIn("Шаг 1/7", call.message.edited[0])


if __name__ == "__main__":
    unittest.main()
