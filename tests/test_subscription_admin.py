import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.config import settings
from app.services import payment_requests, subscription, tenant_cache


class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, field, direction=1):
        self._docs.sort(key=lambda d: (d.get(field) is None, d.get(field)), reverse=direction < 0)
        return self

    async def to_list(self, length=None):
        return self._docs[:length] if length else self._docs


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = [dict(doc) for doc in (docs or [])]

    @staticmethod
    def _set_path(doc, key, value):
        parts = key.split(".")
        current = doc
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    def _match(self, doc, query):
        for key, value in (query or {}).items():
            if "." in key:
                current = doc
                for part in key.split("."):
                    if not isinstance(current, dict) or part not in current:
                        return False
                    current = current[part]
                if current != value:
                    return False
            elif doc.get(key) != value:
                return False
        return True

    async def find_one(self, query):
        for doc in self.docs:
            if self._match(doc, query):
                return doc
        return None

    def find(self, query):
        return FakeCursor([doc for doc in self.docs if self._match(doc, query)])

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._match(doc, query):
                self._apply(doc, update)
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            new_doc = {}
            for key, value in (query or {}).items():
                self._set_path(new_doc, key, value)
            self._apply(new_doc, update)
            self.docs.append(new_doc)
            return SimpleNamespace(matched_count=0, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def _apply(self, doc, update):
        for op, payload in (update or {}).items():
            if op == "$set":
                for key, value in payload.items():
                    self._set_path(doc, key, value)
            elif op == "$push":
                for key, value in payload.items():
                    parts = key.split(".")
                    current = doc
                    for part in parts[:-1]:
                        current = current.setdefault(part, {})
                    current.setdefault(parts[-1], []).append(value)

    async def count_documents(self, query):
        return len([doc for doc in self.docs if self._match(doc, query)])


class FakeDb:
    def __init__(self, tenants=None, admins=None, requests=None):
        self.tenants = FakeCollection(tenants or [])
        self.services = FakeCollection([])
        self.appointments = FakeCollection([])
        self.users = FakeCollection([])
        self.admins = FakeCollection(admins or [])
        self.payment_requests = FakeCollection(requests or [])


class FakeMessage:
    def __init__(self):
        self.text = None
        self.markup = None
        self.answers = []

    async def edit_text(self, text, reply_markup=None):
        self.text = text
        self.markup = reply_markup

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)


class FakeBot:
    def __init__(self, token="TEST-TOKEN"):
        self.token = token
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=len(self.sent))


class FakeCall:
    def __init__(self, data, bot=None, user_id=555):
        self.data = data
        self.bot = bot or FakeBot()
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append({"args": args, "kwargs": kwargs})


def _tenant(tid="t1", status="active", days=30):
    return {
        "tenant_id": tid,
        "name": "Demo Salon",
        "category": "beauty",
        "bot_config": {"token": "TEST-TOKEN"},
        "owner": {"telegram_ids": [555]},
        "subscription": {
            "status": status,
            "current_period_end": subscription.utcnow() + timedelta(days=days),
            "grants": [],
        },
    }


class SubscriptionHelpersTests(unittest.TestCase):
    def test_extended_period_end_counts_from_current_end(self):
        tenant = _tenant(days=10)
        now = subscription.utcnow()
        new_end = subscription.extended_period_end(tenant, 30, now)
        self.assertEqual(new_end, tenant["subscription"]["current_period_end"] + timedelta(days=30))

    def test_extended_period_end_restarts_from_now_for_expired(self):
        now = subscription.utcnow()
        tenant = _tenant(status="expired", days=-5)
        new_end = subscription.extended_period_end(tenant, 30, now)
        self.assertEqual(new_end, now + timedelta(days=30))

    def test_status_label_and_blocking(self):
        self.assertEqual(subscription.status_label(_tenant(status="active")), "активна")
        self.assertEqual(subscription.status_label(_tenant(status="expired")), "истекла")
        self.assertTrue(subscription.is_blocked(_tenant(status="expired")))
        self.assertFalse(subscription.is_blocked(_tenant(status="active")))

    def test_grant_options_cover_admin_buttons(self):
        self.assertEqual(subscription.GRANT_OPTIONS, (7, 30, 90, 365))


class FakeDbPatch:
    def __init__(self, fake_db):
        self.fake_db = fake_db
        self._saved = []

    def __enter__(self):
        from app.db import mongodb
        from app.bot.handlers import owner, parent, tenant_admin

        holder = SimpleNamespace(db=self.fake_db)
        self._saved.append((mongodb, "db", mongodb.db))
        mongodb.db = holder
        for module in (owner, parent, tenant_admin, payment_requests):
            self._saved.append((module, "db", getattr(module, "db", None)))
            module.db = holder
        tenant_cache.clear_cache()
        return self.fake_db

    def __exit__(self, exc_type, exc, tb):
        for module, name, value in reversed(self._saved):
            setattr(module, name, value)
        tenant_cache.clear_cache()
        return False


class AdminSubscriptionFlowTests(unittest.TestCase):
    def setUp(self):
        from app.bot.handlers import tenant_admin

        self.tenant_admin = tenant_admin
        self.old_mode = settings.PAYMENT_MODE
        settings.PAYMENT_MODE = "manual"

    def tearDown(self):
        settings.PAYMENT_MODE = self.old_mode

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_admin_screen_shows_status_and_grant_buttons(self):
        from app.bot.handlers.tenant_admin import (
            build_subscription_kb,
            build_subscription_screen,
        )

        text = build_subscription_screen(_tenant())
        self.assertIn("Подписка", text)
        self.assertIn("активна", text)
        self.assertIn("Действует до", text)

        buttons = [
            button.callback_data
            for row in build_subscription_kb("t1").inline_keyboard
            for button in row
        ]
        self.assertIn("tadm:subext:t1:30", buttons)
        self.assertIn("tadm:subact:t1", buttons)
        self.assertIn("tadm:suboff:t1", buttons)

    def test_admin_extend_grants_days(self):
        fake_db = FakeDb(tenants=[_tenant(days=0)], admins=[{"telegram_id": 555}])
        with FakeDbPatch(fake_db):
            call = FakeCall("tadm:subext:t1:30")
            self._run(self.tenant_admin.tadm_subscription_extend(call))

        tenant = fake_db.tenants.docs[0]
        self.assertEqual(tenant["subscription"]["status"], "active")
        self.assertGreater(tenant["subscription"]["current_period_end"], subscription.utcnow())
        self.assertEqual(len(tenant["subscription"]["grants"]), 1)
        self.assertIn("продлена на 30 дн", call.message.text)

    def test_admin_disable_blocks_tenant(self):
        fake_db = FakeDb(tenants=[_tenant()], admins=[{"telegram_id": 555}])
        with FakeDbPatch(fake_db):
            call = FakeCall("tadm:suboff:t1")
            self._run(self.tenant_admin.tadm_subscription_disable(call))

        tenant = fake_db.tenants.docs[0]
        self.assertEqual(tenant["subscription"]["status"], "expired")
        self.assertTrue(subscription.is_blocked(tenant))
        self.assertIn("приостановлено", call.message.text)

    def test_admin_requests_list_shows_new_requests(self):
        request = {
            "_id": "req1",
            "tenant_id": "t1",
            "tenant_name": "Demo Salon",
            "contact": "555",
            "status": "new",
            "created_at": subscription.utcnow(),
        }
        fake_db = FakeDb(
            tenants=[_tenant()],
            admins=[{"telegram_id": 555}],
            requests=[request],
        )
        with FakeDbPatch(fake_db):
            call = FakeCall("tadm:reqs")
            self._run(self.tenant_admin.tadm_requests(call))

        self.assertIn("Запросы на оплату", call.message.text)
        self.assertIn("Demo Salon", call.message.text)
        buttons = [
            button.callback_data
            for row in call.message.markup.inline_keyboard
            for button in row
        ]
        self.assertIn("tadm:reqgrant:req1:t1", buttons)
        self.assertIn("tadm:reqclose:req1", buttons)

    def test_admin_grant_from_request_closes_it(self):
        request = {
            "_id": "req1",
            "tenant_id": "t1",
            "tenant_name": "Demo Salon",
            "contact": "555",
            "status": "new",
            "created_at": subscription.utcnow(),
        }
        fake_db = FakeDb(
            tenants=[_tenant(days=0)],
            admins=[{"telegram_id": 555}],
            requests=[request],
        )
        with FakeDbPatch(fake_db):
            call = FakeCall("tadm:reqgrant:req1:t1")
            self._run(self.tenant_admin.tadm_request_grant(call))

        self.assertEqual(fake_db.payment_requests.docs[0]["status"], "closed")
        tenant = fake_db.tenants.docs[0]
        self.assertEqual(tenant["subscription"]["status"], "active")
        self.assertIn("Выдано 30 дней", call.message.text)


class OwnerPaymentRequestTests(unittest.TestCase):
    def setUp(self):
        from app.bot.handlers import owner

        self.owner = owner
        self.old_mode = settings.PAYMENT_MODE
        settings.PAYMENT_MODE = "manual"

    def tearDown(self):
        settings.PAYMENT_MODE = self.old_mode

    def test_owner_pay_creates_request_and_notifies_admin(self):
        import asyncio

        fake_db = FakeDb(
            tenants=[_tenant(status="trial")],
            admins=[{"telegram_id": 777}],
        )
        with FakeDbPatch(fake_db):
            bot = FakeBot()
            call = FakeCall("own:pay", bot=bot)
            original = self.owner._parent_bot
            self.owner._parent_bot = lambda fallback=None: bot
            try:
                asyncio.run(self.owner.own_pay(call))
            finally:
                self.owner._parent_bot = original

        self.assertEqual(len(fake_db.payment_requests.docs), 1)
        request = fake_db.payment_requests.docs[0]
        self.assertEqual(request["tenant_id"], "t1")
        self.assertEqual(request["status"], "new")
        self.assertEqual(request["contact"], "555")
        self.assertTrue(bot.sent)
        self.assertEqual(bot.sent[0]["chat_id"], 777)
        self.assertIn("Запрос на оплату отправлен", call.message.text)
        self.assertIn(request["_id"], call.message.text)

    def test_owner_pay_offers_direct_admin_chat_link(self):
        import asyncio

        fake_db = FakeDb(
            tenants=[_tenant(status="trial")],
            admins=[{"telegram_id": 777}],
        )
        old_contact = settings.SUPPORT_CONTACT
        settings.SUPPORT_CONTACT = "Telegram @Mari_programm"
        try:
            with FakeDbPatch(fake_db):
                bot = FakeBot()
                call = FakeCall("own:pay", bot=bot)
                original = self.owner._parent_bot
                self.owner._parent_bot = lambda fallback=None: bot
                try:
                    asyncio.run(self.owner.own_pay(call))
                finally:
                    self.owner._parent_bot = original
        finally:
            settings.SUPPORT_CONTACT = old_contact

        urls = [
            button.url
            for row in call.message.markup.inline_keyboard
            for button in row
            if button.url
        ]
        self.assertIn("https://t.me/Mari_programm", urls)
        self.assertIn("Telegram @Mari_programm", call.message.text)

    def test_payment_request_service_lists_and_closes(self):
        import asyncio

        fake_db = FakeDb(
            tenants=[_tenant()],
            admins=[{"telegram_id": 777}],
            requests=[
                {
                    "_id": "req1",
                    "tenant_id": "t1",
                    "tenant_name": "Demo Salon",
                    "contact": "555",
                    "status": "new",
                    "created_at": subscription.utcnow(),
                }
            ],
        )
        with FakeDbPatch(fake_db):
            listed = asyncio.run(payment_requests.list_requests("new"))
            self.assertEqual([item["_id"] for item in listed], ["req1"])
            asyncio.run(payment_requests.close_request("req1", "ok"))
            self.assertEqual(fake_db.payment_requests.docs[0]["status"], "closed")
            self.assertEqual(fake_db.payment_requests.docs[0]["note"], "ok")
            self.assertEqual(asyncio.run(payment_requests.admin_chat_ids()), [777])


class ParentPaymentTests(unittest.TestCase):
    def test_parent_pay_opens_subscription_screen_in_manual_mode(self):
        import asyncio

        from app.bot.handlers import parent

        old_mode = settings.PAYMENT_MODE
        settings.PAYMENT_MODE = "manual"
        fake_db = FakeDb(tenants=[_tenant()], admins=[{"telegram_id": 555}])
        try:
            with FakeDbPatch(fake_db):
                call = FakeCall("parent:pay:t1")
                asyncio.run(parent.parent_action(call, SimpleNamespace(set_state=lambda *a: asyncio.sleep(0))))
        finally:
            settings.PAYMENT_MODE = old_mode

        self.assertIn("Подписка", call.message.text)
        buttons = [
            button.callback_data
            for row in call.message.markup.inline_keyboard
            for button in row
        ]
        self.assertIn("tadm:subext:t1:30", buttons)


if __name__ == "__main__":
    unittest.main()
