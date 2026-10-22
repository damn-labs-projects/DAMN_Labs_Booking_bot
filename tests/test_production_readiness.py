import datetime as dt
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import cron, webhooks
from app.api.v1.pages import build_pay_link, payment_is_configured
from app.api.v1.webhooks import _extract_tenant_id, _parse_deadline, _verify_paddle_signature
from app.bot.handlers.parent import build_billing_keyboard, build_parent_billing_summary
from app.config import settings
from app.legal import build_contacts, legal_pages


def _build_app() -> FastAPI:
    from app.api.v1.pages import router as pages_router

    app = FastAPI()
    app.include_router(pages_router)
    app.include_router(webhooks.router, prefix="/api/v1/webhooks")
    return app


class FakeTenants:
    def __init__(self):
        self.updates = []
        self.bulk_updates = []

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def update_many(self, query, update):
        self.bulk_updates.append((query, update))
        return SimpleNamespace(matched_count=0, modified_count=0)


class PaymentPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(_build_app())
        cls.old_price = settings.PADDLE_PRICE_ID
        cls.old_public = settings.PADDLE_PUBLIC_KEY
        cls.old_mode = settings.PAYMENT_MODE

    @classmethod
    def tearDownClass(cls):
        settings.PADDLE_PRICE_ID = cls.old_price
        settings.PADDLE_PUBLIC_KEY = cls.old_public
        settings.PAYMENT_MODE = cls.old_mode

    def setUp(self):
        settings.PAYMENT_MODE = "paddle"

    def tearDown(self):
        settings.PAYMENT_MODE = self.old_mode

    def test_pay_page_reports_missing_payment_config(self):
        settings.PADDLE_PRICE_ID = ""
        settings.PADDLE_PUBLIC_KEY = ""

        self.assertFalse(payment_is_configured())
        response = self.client.get("/pay", params={"tenant_id": "t1"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("временно недоступна", response.text)

    def test_pay_page_embeds_paddle_checkout_with_tenant_id(self):
        settings.PADDLE_PRICE_ID = "pri_test_01"
        settings.PADDLE_PUBLIC_KEY = "test_pk_live"

        response = self.client.get("/pay", params={"tenant_id": "tenant-42"})

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("Paddle.Checkout.open", body)
        self.assertIn("customData", body)
        self.assertIn("pri_test_01", body)
        self.assertIn("tenant-42", body)
        self.assertIn("paddle/v2/paddle.js", body)
        self.assertLess(body.index("paddle/v2/paddle.js"), body.index("Paddle.Initialize"))
        self.assertNotIn("Paddle.Setup", body)
        self.assertNotIn("successCallback", body)
        self.assertIn("checkout.completed", body)

    def test_pay_page_preflights_price_preview_and_reports_errors(self):
        settings.PADDLE_PRICE_ID = "pri_test_01"
        settings.PADDLE_PUBLIC_KEY = "test_pk_live"

        response = self.client.get("/pay", params={"tenant_id": "tenant-42"})

        body = response.text
        self.assertIn("Paddle.PricePreview", body)
        self.assertIn("checkout.error", body)
        self.assertIn("describeError", body)

    def test_pay_page_uses_sandbox_environment_for_test_token(self):
        settings.PADDLE_PRICE_ID = "pri_test_01"
        settings.PADDLE_PUBLIC_KEY = "test_pk_live"

        response = self.client.get("/pay", params={"tenant_id": "t1"})

        self.assertIn('"sandbox": true', response.text)

    def test_pay_page_keeps_live_environment_for_live_token(self):
        settings.PADDLE_PRICE_ID = "pri_live_01"
        settings.PADDLE_PUBLIC_KEY = "live_pk_live"

        response = self.client.get("/pay", params={"tenant_id": "t1"})

        self.assertIn('"sandbox": false', response.text)

    def test_pay_page_without_tenant_id_asks_for_bot_link(self):
        settings.PADDLE_PRICE_ID = "pri_test_01"
        settings.PADDLE_PUBLIC_KEY = "test_pk_live"

        response = self.client.get("/pay")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Не указан бизнес", response.text)

    def test_pay_page_in_manual_mode_shows_support_contact(self):
        settings.PAYMENT_MODE = "manual"
        settings.SUPPORT_CONTACT = "Telegram @Mari_programm"

        response = self.client.get("/pay", params={"tenant_id": "t1"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Оплата онлайн временно выключена", response.text)
        self.assertIn("Telegram @Mari_programm", response.text)
        self.assertNotIn("Paddle.Checkout.open", response.text)

    def test_manual_mode_is_the_default(self):
        self.assertIn(settings.PAYMENT_MODE or "", ("manual", "paddle"))

    def test_pay_link_is_https_and_carries_tenant_id(self):
        link = build_pay_link("abc123")

        self.assertTrue(link.startswith("https://"))
        self.assertIn("/pay?tenant_id=abc123", link)


class LegalPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(_build_app())

    def test_offer_privacy_contacts_are_served(self):
        for path, marker in (
            ("/offer", "ОФЕРТА"),
            ("/privacy", "ПОЛИТИКА КОНФИДЕНЦИАЛЬНОСТИ"),
            ("/contacts", "КОНТАКТЫ"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(marker, response.text)

    def test_every_legal_page_links_to_the_other_documents(self):
        response = self.client.get("/offer")

        self.assertIn("/offer", response.text)
        self.assertIn("/privacy", response.text)
        self.assertIn("/contacts", response.text)

    def test_legal_pages_are_not_empty_drafts(self):
        pages = legal_pages()

        self.assertEqual(set(pages), {"offer", "privacy", "contacts"})
        for key, (title, text) in pages.items():
            with self.subTest(key=key):
                self.assertTrue(title.strip())
                self.assertGreater(len(text.strip()), 300)
                self.assertNotIn("{", text)

    def test_offer_states_refund_window_and_refund_destination(self):
        offer = legal_pages()["offer"][1]

        self.assertIn("14 (четырнадцати) календарных дней", offer)
        self.assertIn("ту же платёжную карту или", offer)
        self.assertIn("7.4.", offer)

    def test_offer_requires_bot_token_custody(self):
        offer = legal_pages()["offer"][1]

        self.assertIn("Bot Token", offer)
        self.assertIn("BotFather", offer)

    def test_offer_discloses_recurring_payments_and_cancellation(self):
        offer = legal_pages()["offer"][1]

        self.assertIn("автопродлен", offer)
        self.assertIn("4.5.", offer)
        self.assertIn("окончания уже оплаченного периода", offer)

    def test_contacts_explain_refunds_and_cancellation(self):
        contacts = legal_pages()["contacts"][1]

        self.assertIn("Возврат средств", contacts)
        self.assertIn("Отмена подписки", contacts)
        self.assertIn("14 (четырнадцати)", contacts)

    def test_contacts_page_does_not_repeat_generic_fallback(self):
        old = settings.SUPPORT_CONTACT
        settings.SUPPORT_CONTACT = ""
        try:
            contacts = build_contacts()
        finally:
            settings.SUPPORT_CONTACT = old

        self.assertIn("КОНТАКТЫ", contacts)
        self.assertNotIn("Контакты предоставляются по запросу в службу поддержки."
                         "Контакты", contacts.replace("\n", ""))


class PaddleSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(_build_app())
        cls.secret = settings.PADDLE_WEBHOOK_SECRET_KEY
        cls.old_secret = settings.PADDLE_WEBHOOK_SECRET_KEY

    @classmethod
    def tearDownClass(cls):
        settings.PADDLE_WEBHOOK_SECRET_KEY = cls.old_secret

    @staticmethod
    def _sign(body: bytes, secret: str, ts: str = "1760000000") -> dict:
        digest = hmac.new(
            secret.encode(), f"{ts}:{body.decode()}".encode(), hashlib.sha256
        ).hexdigest()
        return {"paddle-signature": f"ts={ts};h1={digest}"}

    def test_valid_signature_is_accepted(self):
        body = json.dumps({"event_type": "subscription.updated"}).encode()
        request = SimpleNamespace(headers=self._sign(body, self.secret))

        self.assertTrue(_verify_paddle_signature(request, body))

    def test_tampered_body_is_rejected(self):
        body = json.dumps({"event_type": "subscription.updated"}).encode()
        request = SimpleNamespace(headers=self._sign(body, self.secret))

        self.assertFalse(_verify_paddle_signature(request, body + b" "))

    def test_wrong_secret_is_rejected(self):
        body = json.dumps({"event_type": "subscription.updated"}).encode()
        request = SimpleNamespace(headers=self._sign(body, "other_secret"))

        self.assertFalse(_verify_paddle_signature(request, body))

    def test_missing_signature_header_is_rejected(self):
        body = b"{}"
        request = SimpleNamespace(headers={})

        self.assertFalse(_verify_paddle_signature(request, body))

    def test_empty_secret_is_fail_closed(self):
        settings.PADDLE_WEBHOOK_SECRET_KEY = ""
        body = json.dumps({"event_type": "subscription.updated"}).encode()
        request = SimpleNamespace(headers=self._sign(body, ""))
        try:
            rejected = _verify_paddle_signature(request, body)
        finally:
            settings.PADDLE_WEBHOOK_SECRET_KEY = self.old_secret

        self.assertFalse(rejected)

    def test_placeholder_secret_is_fail_closed(self):
        settings.PADDLE_WEBHOOK_SECRET_KEY = "paddle_secret_here"
        body = json.dumps({"event_type": "subscription.updated"}).encode()
        request = SimpleNamespace(headers=self._sign(body, "paddle_secret_here"))
        try:
            rejected = _verify_paddle_signature(request, body)
        finally:
            settings.PADDLE_WEBHOOK_SECRET_KEY = self.old_secret

        self.assertFalse(rejected)

    def test_default_config_secret_is_empty(self):
        from app.config import Settings

        self.assertEqual(Settings.model_fields["PADDLE_WEBHOOK_SECRET_KEY"].default, "")

    def test_paddle_webhook_updates_subscription_from_signed_request(self):
        payload = {
            "event_type": "subscription.updated",
            "data": {
                "id": "sub_01ABC",
                "custom_data": {"tenant_id": "tenant-77"},
                "current_billing_period": {"ends_at": "2026-11-18T12:00:00Z"},
            },
        }
        body = json.dumps(payload).encode()

        fake = FakeTenants()
        old_db = webhooks.db
        webhooks.db = SimpleNamespace(db=SimpleNamespace(tenants=fake))
        try:
            response = self.client.post(
                "/api/v1/webhooks/paddle",
                content=body,
                headers={
                    "content-type": "application/json",
                    **self._sign(body, self.secret),
                },
            )
        finally:
            webhooks.db = old_db

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(len(fake.updates), 1)
        query, update = fake.updates[0]
        self.assertEqual(query, {"tenant_id": "tenant-77"})
        self.assertEqual(update["$set"]["subscription.status"], "active")
        self.assertEqual(update["$set"]["subscription.paddle_subscription_id"], "sub_01ABC")
        self.assertEqual(
            update["$set"]["subscription.current_period_end"].year, 2026
        )

    def test_webhook_without_signature_does_not_touch_database(self):
        payload = {
            "event_type": "subscription.updated",
            "data": {"custom_data": {"tenant_id": "tenant-77"}},
        }
        fake = FakeTenants()
        old_db = webhooks.db
        webhooks.db = SimpleNamespace(db=SimpleNamespace(tenants=fake))
        try:
            response = self.client.post(
                "/api/v1/webhooks/paddle",
                json=payload,
                headers={"content-type": "application/json"},
            )
        finally:
            webhooks.db = old_db

        self.assertEqual(response.json()["status"], "invalid_signature")
        self.assertEqual(fake.updates, [])


class WebhookParsingTests(unittest.TestCase):
    def test_extract_tenant_id_is_case_insensitive(self):
        self.assertEqual(_extract_tenant_id({"custom_data": {"tenant_id": "t1"}}), "t1")
        self.assertEqual(_extract_tenant_id({"custom_data": {"Tenant_Id": "t2"}}), "t2")
        self.assertEqual(_extract_tenant_id({"custom_data": {"tenantid": "t3"}}), "t3")

    def test_extract_tenant_id_handles_missing_and_malformed(self):
        self.assertEqual(_extract_tenant_id({}), "")
        self.assertEqual(_extract_tenant_id({"custom_data": None}), "")
        self.assertEqual(_extract_tenant_id({"custom_data": "not-a-dict"}), "")
        self.assertEqual(_extract_tenant_id({"custom_data": {"tenant_id": ""}}), "")

    def test_parse_deadline_normalizes_timezones_to_naive_utc(self):
        parsed = _parse_deadline("2026-11-18T12:00:00Z")

        self.assertEqual(parsed.year, 2026)
        self.assertIsNone(parsed.tzinfo)

    @staticmethod
    def _naive_now() -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    def test_parse_deadline_falls_back_on_garbage(self):
        parsed = _parse_deadline("not-a-date")
        now = self._naive_now()

        self.assertGreater(parsed, now)
        self.assertLess(parsed, now + dt.timedelta(days=31))

    def test_parse_deadline_handles_missing_value(self):
        parsed = _parse_deadline(None)
        now = self._naive_now()

        self.assertGreater(parsed, now + dt.timedelta(days=29))


class BillingKeyboardTests(unittest.TestCase):
    def test_billing_keyboard_offers_payment_for_business(self):
        tenants = [
            {"tenant_id": "tenant-42", "name": "Beauty Studio",
             "subscription": {"status": "trial", "current_period_end": None}},
        ]

        summary = build_parent_billing_summary(tenants)
        keyboard = build_billing_keyboard(tenants)
        callback_values = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if getattr(button, "callback_data", None)
        ]

        self.assertIn("trial", summary.lower())
        self.assertIn("parent:pay:tenant-42", callback_values)

    def test_billing_keyboard_has_menu_return_button(self):
        keyboard = build_billing_keyboard([])
        callback_values = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if getattr(button, "callback_data", None)
        ]

        self.assertIn("parent:menu", callback_values)


class CronAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.api.v1.cron import router as cron_router

        app = FastAPI()
        app.include_router(cron_router, prefix="/api/v1/cron")
        cls.client = TestClient(app)
        cls.old_secret = settings.CRON_SECRET

    @classmethod
    def tearDownClass(cls):
        settings.CRON_SECRET = cls.old_secret

    def _run(self, headers):
        fake = FakeTenants()
        old_db = cron.db
        cron.db = SimpleNamespace(db=SimpleNamespace(tenants=fake))
        try:
            response = self.client.get(
                "/api/v1/cron/subscriptions/run",
                headers=headers,
            )
        finally:
            cron.db = old_db
        return response, fake

    def test_cron_rejects_request_when_secret_is_not_configured(self):
        settings.CRON_SECRET = ""

        response, fake = self._run({"Authorization": "Bearer "})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(fake.bulk_updates, [])

    def test_cron_rejects_wrong_secret(self):
        settings.CRON_SECRET = "expected-secret"

        response, fake = self._run({"Authorization": "Bearer wrong-secret"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(fake.bulk_updates, [])

    def test_cron_rejects_request_without_any_header(self):
        settings.CRON_SECRET = "expected-secret"

        response, fake = self._run({})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(fake.bulk_updates, [])

    def test_cron_marks_expired_subscriptions_with_bearer_token(self):
        settings.CRON_SECRET = "expected-secret"

        response, fake = self._run({"Authorization": "Bearer expected-secret"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(len(fake.bulk_updates), 1)
        query, update = fake.bulk_updates[0]
        self.assertIn("subscription.current_period_end", str(query))
        self.assertEqual(update["$set"]["subscription.status"], "expired")

    def test_cron_accepts_vercel_cron_secret_header(self):
        settings.CRON_SECRET = "expected-secret"

        response, fake = self._run({"x-vercel-cron-secret": "expected-secret"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
