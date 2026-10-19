import asyncio
import unittest
from types import SimpleNamespace

from app.bot.handlers.booking import cmd_start
from app.bot.handlers.parent import (
    build_parent_billing_summary,
    build_parent_demo_message,
    build_parent_tenant_summary,
    build_parent_tenants_list,
    parent_action,
)
from app.config import settings


class ParentBotTests(unittest.TestCase):
    def test_demo_message_mentions_platform_and_parent_bot(self):
        text = build_parent_demo_message("Demo Salon")

        self.assertIn("D.A.M.N Labs", text)
        self.assertIn("Demo Salon", text)
        self.assertIn("родительский", text.lower())

    def test_tenant_summary_includes_required_fields(self):
        text = build_parent_tenant_summary(
            name="Beauty Studio",
            category="beauty",
            owner_name="Aida",
            owner_phone="+996555123456",
            owner_telegram_id="123456789",
            bot_token="123456:ABCDEF",
        )

        self.assertIn("Beauty Studio", text)
        self.assertIn("beauty", text)
        self.assertIn("Aida", text)
        self.assertIn("123456789", text)
        self.assertIn("123456:ABCDEF", text)

    def test_tenant_list_and_trial_billing_summary_are_generated(self):
        tenants = [
            {
                "name": "Beauty Studio",
                "bot_config": {"token": "123:ABC"},
                "subscription": {"status": "trial", "current_period_end": None},
            }
        ]

        list_text = build_parent_tenants_list(tenants)
        billing_text = build_parent_billing_summary(tenants)

        self.assertIn("Beauty Studio", list_text)
        self.assertIn("trial", billing_text.lower())
        self.assertIn("7 дней trial", billing_text)

    def test_parent_bot_start_command_does_not_hit_tenant_not_found(self):
        old_token = settings.PARENT_BOT_TOKEN
        settings.PARENT_BOT_TOKEN = "parent-token"

        class FakeState:
            async def clear(self):
                pass

            async def set_state(self, *args, **kwargs):
                pass

        class FakeMessage:
            def __init__(self):
                self.bot = SimpleNamespace(token="parent-token")
                self._answers = []

            async def answer(self, text, **kwargs):
                self._answers.append(text)

        try:
            message = FakeMessage()
            asyncio.run(cmd_start(message, FakeState()))
            self.assertTrue(message._answers)
            self.assertNotIn("Ошибка: Заведение не найдено.", message._answers[0])
        finally:
            settings.PARENT_BOT_TOKEN = old_token

    def test_is_admin_denies_unknown_and_missing_user(self):
        from app.bot.handlers import parent as parent_module

        self.assertFalse(asyncio.run(parent_module.is_admin(None)))
        self.assertFalse(asyncio.run(parent_module.is_admin(12345)))

    def test_parent_bot_callback_button_route_works(self):
        class FakeMessage:
            def __init__(self):
                self.edits = []
                self.answers = []

            async def edit_text(self, text, **kwargs):
                self.edits.append(text)

            async def answer(self, *args, **kwargs):
                self.answers.append("answer")

        class FakeCall:
            def __init__(self):
                self.data = "parent:tenants"
                self.message = FakeMessage()

            async def answer(self, *args, **kwargs):
                pass

        class FakeState:
            async def set_state(self, *args, **kwargs):
                pass

        from app.bot.handlers import parent as parent_module

        async def _allow(_user_id):
            return True

        message = FakeCall()
        original = parent_module.is_admin
        parent_module.is_admin = _allow
        try:
            asyncio.run(parent_action(message, FakeState()))
        finally:
            parent_module.is_admin = original

        self.assertTrue(message.message.edits)
        self.assertIn("Список бизнесов", message.message.edits[0])


if __name__ == "__main__":
    unittest.main()
