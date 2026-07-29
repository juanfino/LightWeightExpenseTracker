import os
import unittest

import auth
import db
import llm_limits
import pgcompat
from support import authenticated_client, reset_database


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "requires PostgreSQL")
class SuperadminIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
        os.environ.setdefault("AUTH_SECRET_KEY", "test-secret")
        db.init_db()

    def setUp(self):
        reset_database({"100": "Owner"})
        with auth.platform_transaction() as raw:
            raw.execute(
                "UPDATE users SET email = 'owner@example.com' WHERE telegram_id = '100'"
            )
        self.user = db.get_user_by_telegram_id("100")
        pgcompat.set_family_id(self.user["family_id"])
        pgcompat.set_user_id(self.user["id"])

    def _set_superadmin(self, enabled=True):
        with auth.platform_transaction() as raw:
            raw.execute(
                "UPDATE users SET is_superadmin = %s WHERE id = %s",
                (enabled, self.user["id"]),
            )

    def test_panel_is_superadmin_only_and_uses_global_metrics(self):
        import dashboard

        client, _headers = authenticated_client(dashboard)
        self.assertEqual(client.get("/superadmin").status_code, 403)
        self._set_superadmin()
        client, _headers = authenticated_client(dashboard)
        page = client.get("/superadmin")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Vista global", page.data)
        self.assertIn(b"Familia de prueba", page.data)

    def test_backup_routes_are_superadmin_only(self):
        import dashboard

        client, headers = authenticated_client(dashboard)
        self.assertEqual(client.get("/config").status_code, 403)
        self.assertEqual(client.get("/api/backup-status").status_code, 403)
        self.assertEqual(
            client.post("/admin/backup-now", headers=headers).status_code, 403
        )

        self._set_superadmin()
        client, headers = authenticated_client(dashboard)
        self.assertEqual(client.get("/config").status_code, 200)
        self.assertEqual(client.get("/api/backup-status").status_code, 200)

    def test_quota_override_is_tenant_visible_and_resettable(self):
        import dashboard

        self._set_superadmin()
        client, headers = authenticated_client(dashboard)
        response = client.post(
            "/admin/families/1/quotas",
            json={"routine_daily_limit": 3, "summary_monthly_limit": 2},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(llm_limits.routine_usage()["limit"], 3)
        self.assertEqual(llm_limits.summary_usage()["limit"], 2)

        response = client.post(
            "/admin/families/1/quotas",
            json={"routine_daily_limit": "", "summary_monthly_limit": ""},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(llm_limits.routine_usage()["limit"], 100)
        self.assertEqual(llm_limits.summary_usage()["limit"], 15)

    def test_recent_errors_and_cost_settings_are_persisted(self):
        import dashboard

        self._set_superadmin()
        db.record_system_error("test", RuntimeError("boom"), "trace")
        client, headers = authenticated_client(dashboard)
        response = client.post(
            "/admin/costs/Resend",
            json={
                "unit_label": "emails",
                "unit_rate_usd": "0.001",
                "monthly_volume": "250",
                "notes": "medido",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        page = client.get("/superadmin")
        self.assertIn(b"boom", page.data)
        self.assertIn(b"0.2500", page.data)


if __name__ == "__main__":
    unittest.main()
