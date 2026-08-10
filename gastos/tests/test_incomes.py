import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import pgcompat  # noqa: E402
from support import authenticated_client, reset_database  # noqa: E402


class IncomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import dashboard
        cls.dashboard = dashboard

    def setUp(self):
        reset_database({"100": "Juampi", "200": "Cele"})
        self.user = db.get_user_by_telegram_id("100")
        self.other = db.get_user_by_telegram_id("200")

    def test_income_crud_and_ownership(self):
        category = db.find_income_category_normalized("Sueldo")
        income_id = db.create_income(self.user["id"], "Sueldo", 3000, "USD", "2026-07-01", category["id"])
        self.assertEqual(db.get_income_month_totals(2026, 7), {"USD": 3000.0})
        self.assertFalse(db.update_income(income_id, self.other["id"], "Ajeno", 1, "USD", "2026-07-01", None))
        self.assertFalse(db.delete_income(income_id, self.other["id"]))
        self.assertTrue(db.update_income(income_id, self.user["id"], "Sueldo", 3100, "USD", "2026-07-01", category["id"]))

    def test_web_uses_authenticated_user_and_blocks_other_owner(self):
        client, headers = authenticated_client(self.dashboard, "100")
        response = client.post("/api/incomes", json={
            "concept": "Honorarios", "amount": 100000, "currency": "ARS",
            "date": "2026-07-02",
        }, headers=headers)
        self.assertEqual(response.status_code, 201)
        income_id = response.get_json()["id"]
        self.assertEqual(db.get_income(income_id)["user_id"], self.user["id"])

        other_client, other_headers = authenticated_client(self.dashboard, "200")
        response = other_client.delete(f"/api/incomes/{income_id}", headers=other_headers)
        self.assertEqual(response.status_code, 403)

    def test_incomes_are_tenant_isolated(self):
        income_a = db.create_income(self.user["id"], "Solo A", 10, "ARS", "2026-07-01")
        with pgcompat.current_pool().connection() as raw:
            family_b = raw.execute("INSERT INTO families (name) VALUES ('B') RETURNING id").fetchone()[0]
            user_b = raw.execute("INSERT INTO users (telegram_id, name) VALUES ('300', 'B') RETURNING id").fetchone()[0]
            raw.execute("INSERT INTO memberships (user_id, family_id, role) VALUES (%s, %s, 'owner')", (user_b, family_b))
            raw.commit()
        pgcompat.set_family_id(family_b)
        import seed
        with db.get_conn() as conn:
            seed.create_family_defaults(conn, family_b)
        income_b = db.create_income(user_b, "Solo B", 20, "ARS", "2026-07-01")
        self.assertEqual([r["id"] for r in db.get_incomes()], [income_b])
        self.assertIsNone(db.get_income(income_a))
        pgcompat.set_family_id(1)
        self.assertEqual([r["id"] for r in db.get_incomes()], [income_a])
        self.assertIsNone(db.get_income(income_b))

    def test_income_summary_tiles_include_period_eur_and_default_first(self):
        db.set_family_default_currency("BRL")
        db.create_income(self.user["id"], "Europa", 42, "EUR", "2026-07-01")
        client, _headers = authenticated_client(self.dashboard, "100")

        response = client.get("/ingresos?period=2026-07")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Ingresos BRL del mes", html)
        self.assertIn("Ingresos EUR del mes", html)
        self.assertLess(html.index("Ingresos BRL del mes"), html.index("Ingresos EUR del mes"))
        self.assertNotIn("Ingresos ARS del mes", html)
        self.assertNotIn("Ingresos USD del mes", html)


if __name__ == "__main__":
    unittest.main()
