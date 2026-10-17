import unittest

from app.bot.handlers.parent import (
    build_parent_billing_summary,
    build_parent_demo_message,
    build_parent_tenant_summary,
    build_parent_tenants_list,
)


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
            bot_token="123456:ABCDEF",
        )

        self.assertIn("Beauty Studio", text)
        self.assertIn("beauty", text)
        self.assertIn("Aida", text)
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


if __name__ == "__main__":
    unittest.main()
