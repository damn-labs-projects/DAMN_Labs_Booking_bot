import asyncio
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.db import mongodb
from app.services import subscription
from app.services import booking_service as booking_service_module
from app.services.booking_service import BookingRejected, BookingService


def run(coro):
    return asyncio.run(coro)


class SubscriptionStatusTests(unittest.TestCase):
    def test_blocked_statuses_are_detected(self):
        for status in ("expired", "cancelled", "past_due"):
            with self.subTest(status=status):
                self.assertTrue(subscription.is_blocked({"subscription": {"status": status}}))

    def test_working_statuses_are_not_blocked(self):
        for status in ("trial", "active", None, ""):
            with self.subTest(status=status):
                self.assertFalse(subscription.is_blocked({"subscription": {"status": status}}))

    def test_missing_and_malformed_tenants_do_not_crash(self):
        self.assertFalse(subscription.is_blocked(None))
        self.assertFalse(subscription.is_blocked({}))
        self.assertFalse(subscription.is_blocked({"subscription": "broken"}))
        self.assertEqual(subscription.status_of(None), "unknown")
        self.assertIsNone(subscription.period_end_of({}))


class _RecordingCollection:
    def __init__(self, error: Exception | None = None):
        self.calls = []
        self.error = error

    async def create_index(self, keys, **kwargs):
        if self.error:
            raise self.error
        self.calls.append((keys, kwargs))


class MongoIndexTests(unittest.TestCase):
    def test_appointments_slot_index_is_unique(self):
        collections = {}

        def get(name):
            collections.setdefault(name, _RecordingCollection())
            return collections[name]

        fake_db = SimpleNamespace()
        for name in ("tenants", "services", "staff", "appointments", "admins", "fsm_states"):
            setattr(fake_db, name, get(name))

        old = mongodb.db.db
        mongodb.db.db = fake_db
        try:
            run(mongodb._ensure_indexes())
        finally:
            mongodb.db.db = old

        slot_index = [
            (keys, kwargs)
            for keys, kwargs in collections["appointments"].calls
            if keys == [("tenant_id", 1), ("start_time", 1)]
        ]

        self.assertEqual(len(slot_index), 1)
        self.assertTrue(slot_index[0][1].get("unique"))

    def test_failing_collection_does_not_stop_other_indexes(self):
        collections = {
            "tenants": _RecordingCollection(error=RuntimeError("duplicate key")),
            "staff": _RecordingCollection(),
            "admins": _RecordingCollection(),
        }
        fake_db = SimpleNamespace(
            tenants=collections["tenants"],
            staff=collections["staff"],
            admins=collections["admins"],
        )

        old = mongodb.db.db
        mongodb.db.db = fake_db
        try:
            run(mongodb._ensure_indexes())
        finally:
            mongodb.db.db = old

        self.assertEqual(collections["tenants"].calls, [])
        self.assertTrue(collections["staff"].calls)
        self.assertTrue(collections["admins"].calls)

    def test_missing_database_attribute_does_not_propagate(self):
        old = mongodb.db.db
        mongodb.db.db = SimpleNamespace()
        try:
            run(mongodb._ensure_indexes())
        finally:
            mongodb.db.db = old


class _FakeCollection:
    def __init__(self, docs=None, on_insert=None):
        self.docs = docs or []
        self.on_insert = on_insert
        self.inserted = []

    def _matches(self, doc, query) -> bool:
        for key, expected in query.items():
            value = doc.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and value == expected["$ne"]:
                    return False
                if "$lt" in expected and not (value is not None and value < expected["$lt"]):
                    return False
                if "$gt" in expected and not (value is not None and value > expected["$gt"]):
                    return False
                if "$gte" in expected and not (value is not None and value >= expected["$gte"]):
                    return False
            elif value != expected:
                return False
        return True

    async def find_one(self, query):
        for doc in self.docs:
            if self._matches(doc, query):
                return doc
        return None

    async def insert_one(self, doc):
        if self.on_insert:
            self.on_insert(doc)
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="id")


def _make_db(tenant, service, appointments=None, on_insert=None):
    return SimpleNamespace(
        tenants=_FakeCollection([tenant] if tenant else []),
        services=_FakeCollection([service] if service else []),
        appointments=_FakeCollection(appointments or [], on_insert=on_insert),
    )


class CreateAppointmentTests(unittest.TestCase):
    def setUp(self):
        self.old_db = booking_service_module.db
        self.tenant = {"tenant_id": "t1", "name": "Salon", "subscription": {"status": "active"}}
        self.service = {"tenant_id": "t1", "service_id": "s1", "duration_minutes": 30, "name": "Стрижка"}
        self.date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        booking_service_module.db = self.old_db

    def _create(self):
        return BookingService.create_appointment(
            tenant_id="t1",
            service_id="s1",
            telegram_id=42,
            full_name="Иван",
            phone="+70000000000",
            date_str=self.date,
            time_str="10:00",
        )

    def test_creates_appointment_when_slot_is_free(self):
        booking_service_module.db = SimpleNamespace(db=_make_db(self.tenant, self.service))

        appointment, tenant, service = run(self._create())

        self.assertEqual(appointment["status"], "confirmed")
        self.assertEqual(tenant["tenant_id"], "t1")
        self.assertEqual(service["service_id"], "s1")

    def test_blocks_booking_for_suspended_subscription(self):
        self.tenant["subscription"]["status"] = "expired"
        booking_service_module.db = SimpleNamespace(db=_make_db(self.tenant, self.service))

        with self.assertRaises(BookingRejected) as ctx:
            run(self._create())

        self.assertIn("приостановлено", ctx.exception.message)

    def test_rejects_booking_when_overlapping_appointment_exists(self):
        start = datetime.strptime(f"{self.date} 10:00", "%Y-%m-%d %H:%M")
        conflicting = {
            "tenant_id": "t1",
            "status": "confirmed",
            "start_time": start - timedelta(minutes=10),
            "end_time": start + timedelta(minutes=10),
        }
        booking_service_module.db = SimpleNamespace(
            db=_make_db(self.tenant, self.service, [conflicting])
        )

        with self.assertRaises(BookingRejected) as ctx:
            run(self._create())

        self.assertIn("уже заняли", ctx.exception.message)

    def test_rejects_booking_when_another_appointment_still_overlaps(self):
        start = datetime.strptime(f"{self.date} 10:00", "%Y-%m-%d %H:%M")
        later = {
            "tenant_id": "t1",
            "status": "confirmed",
            "start_time": start + timedelta(minutes=15),
            "end_time": start + timedelta(minutes=75),
        }
        booking_service_module.db = SimpleNamespace(
            db=_make_db(self.tenant, self.service, [later])
        )

        with self.assertRaises(BookingRejected):
            run(self._create())

    def test_cancelled_appointment_does_not_block_slot(self):
        start = datetime.strptime(f"{self.date} 10:00", "%Y-%m-%d %H:%M")
        cancelled = {
            "tenant_id": "t1",
            "status": "cancelled",
            "start_time": start,
            "end_time": start + timedelta(minutes=30),
        }
        booking_service_module.db = SimpleNamespace(
            db=_make_db(self.tenant, self.service, [cancelled])
        )

        appointment, _, _ = run(self._create())

        self.assertEqual(appointment["status"], "confirmed")

    def test_duplicate_key_race_is_reported_to_client(self):
        def boom(_doc):
            raise DuplicateKeyError("E11000 duplicate key")

        booking_service_module.db = SimpleNamespace(
            db=_make_db(self.tenant, self.service, on_insert=boom)
        )

        with self.assertRaises(BookingRejected) as ctx:
            run(self._create())

        self.assertIn("уже заняли", ctx.exception.message)

    def test_missing_tenant_is_rejected(self):
        booking_service_module.db = SimpleNamespace(db=_make_db(None, self.service))

        with self.assertRaises(BookingRejected):
            run(self._create())

    def test_database_outage_is_rejected(self):
        booking_service_module.db = SimpleNamespace(db=None)

        with self.assertRaises(BookingRejected) as ctx:
            run(self._create())

        self.assertIn("недоступен", ctx.exception.message)


class OwnerPanelTests(unittest.TestCase):
    def test_owner_panel_offers_subscription_button(self):
        from app.bot.handlers.owner import _panel_kb

        keyboard = _panel_kb({"tenant_id": "t1"})
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]

        self.assertIn("own:pay", callbacks)

    def test_subscription_line_shows_status_and_deadline(self):
        from datetime import datetime as dt

        from app.bot.handlers.owner import _subscription_line

        line = _subscription_line({
            "subscription": {"status": "trial", "current_period_end": dt(2026, 10, 25)}
        })

        self.assertIn("пробный период", line)
        self.assertIn("25.10.2026", line)

    def test_subscription_line_handles_missing_subscription(self):
        from app.bot.handlers.owner import _subscription_line

        line = _subscription_line({"tenant_id": "t1"})

        self.assertIn("пробный период", line)
        self.assertIn("💳 Подписка", line)


class LandingPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.api.v1.pages import router as pages_router

        app = FastAPI()
        app.include_router(pages_router)
        cls.client = TestClient(app)

    def test_root_is_a_landing_page_not_json(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("D.A.M.N Booking Hub", response.text)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertNotIn('"status": "ok"', response.text)

    def test_landing_links_to_legal_documents(self):
        response = self.client.get("/")

        self.assertIn("/offer", response.text)
        self.assertIn("/privacy", response.text)
        self.assertIn("/contacts", response.text)


class ApplicationBootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import app

        cls.client = TestClient(app)

    def test_healthz_still_answers_json_for_monitoring(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_payment_and_legal_routes_are_reachable(self):
        for path in ("/pay", "/offer", "/privacy", "/contacts", "/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
