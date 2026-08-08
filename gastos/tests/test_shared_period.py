import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard  # noqa: E402
import pgcompat  # noqa: E402
from support import authenticated_client, reset_database  # noqa: E402


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = datetime(2025, 12, 31, 12, 30, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


class SharedPeriodTests(unittest.TestCase):
    def setUp(self):
        reset_database({"123": "Tester"})
        self.client, _ = authenticated_client(dashboard, "123")

    def test_explicit_url_period_wins_over_cookie_and_updates_it(self):
        self.client.set_cookie(dashboard.PERIOD_COOKIE, "2026-05")

        response = self.client.get("/ingresos?period=2026-06")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Junio 2026", response.data)
        self.assertIn("gastos_period=2026-06", response.headers["Set-Cookie"])

    def test_cookie_carries_period_to_an_in_scope_page_without_query(self):
        self.client.set_cookie(dashboard.PERIOD_COOKIE, "2026-04")

        response = self.client.get("/fijos")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Abril 2026", response.data)
        self.assertIn(b"/history?period=2026-04", response.data)

    def test_default_month_uses_family_timezone(self):
        with pgcompat.current_pool().connection() as conn:
            conn.execute("UPDATE families SET timezone = %s WHERE id = 1", ("Pacific/Kiritimati",))
            conn.commit()

        with patch("dashboard.datetime", _FixedDateTime):
            response = self.client.get("/ingresos")

        # The frozen instant is still Dec 31 in UTC/server time, but Jan 1 in Kiritimati.
        self.assertIn(b"Enero 2026", response.data)
        self.assertIn("gastos_period=2026-01", response.headers["Set-Cookie"])

    def test_invalid_explicit_period_redirects_to_family_current_month(self):
        with patch("dashboard._family_now", return_value=datetime(2026, 8, 8, tzinfo=timezone.utc)):
            response = self.client.get("/dolares?period=not-a-month", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dolares?period=2026-08")

    def test_old_summary_deep_link_redirects_to_canonical_query(self):
        response = self.client.get("/resumenes/2026-06", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/resumenes?period=2026-06")

    def test_each_in_scope_page_renders_exactly_one_period_control(self):
        for path in ("/dashboard", "/history", "/ingresos", "/fijos", "/dolares", "/resumenes"):
            with self.subTest(path=path):
                response = self.client.get(f"{path}?period=2026-06")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data.count(b"data-period-control"), 1)

    def test_history_offers_global_period_year_even_without_expenses_in_it(self):
        response = self.client.get("/history?period=2030-06")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<option value="2030">2030</option>', response.data)


if __name__ == "__main__":
    unittest.main()
