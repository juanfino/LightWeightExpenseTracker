import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard  # noqa: E402
import db  # noqa: E402
from support import authenticated_client, reset_database  # noqa: E402


class HistoryAddExpenseTests(unittest.TestCase):
    def setUp(self):
        reset_database({"123": "Tester"})
        self.client, self.csrf_headers = authenticated_client(dashboard, "123")
        self.user = db.get_user_by_telegram_id("123")
        self.category = db.get_category_by_name("Hogar")
        self.subcategory = db.find_subcategory_normalized(
            self.category["id"], "Supermercado"
        )

    def test_add_expense_persists_selected_subcategory(self):
        response = self.client.post(
            "/api/expenses/add",
            json={
                "concept": "Compra semanal",
                "amount": 15000,
                "currency": "ARS",
                "category_id": self.category["id"],
                "subcategory_id": self.subcategory["id"],
                "user_id": self.user["id"],
                "date": "2026-07-20",
            },
            headers=self.csrf_headers,
        )

        self.assertEqual(response.status_code, 200)
        expense = db.get_expense_by_id(response.get_json()["id"])
        self.assertEqual(expense["category_id"], self.category["id"])
        self.assertEqual(expense["subcategory_id"], self.subcategory["id"])

    def test_postgres_timestamp_keeps_dashboard_json_contract(self):
        db.create_expense_full(
            self.user["id"],
            self.category["id"],
            "Fecha PostgreSQL",
            100,
            "2026-07-20",
            subcategory_id=self.subcategory["id"],
        )

        recent = self.client.get("/api/expenses").get_json()
        filtered = self.client.get("/api/expenses?year=2026&month=7").get_json()

        for payload in (recent, filtered):
            row = next(item for item in payload if item["concept"] == "Fecha PostgreSQL")
            self.assertEqual(row["created_at"], "2026-07-20 00:00:00")

    def test_add_usd_expense_with_existing_fixed_expenses_does_not_crash(self):
        fixed_id = db.create_fixed_expense(
            "Viaje Porto de Galinhas", 4650, self.category["id"], currency="USD"
        )

        response = self.client.post(
            "/api/expenses/add",
            json={
                "concept": "Viaje Porto de Galinhas",
                "amount": 4650,
                "currency": "USD",
                "category_id": self.category["id"],
                "user_id": self.user["id"],
                "date": "2026-07-10",
            },
            headers=self.csrf_headers,
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["suggested_fixed_expense"]["id"], fixed_id)

    def test_history_modal_exposes_dependent_and_inline_taxonomy_controls(self):
        response = self.client.get("/history")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="add-subcategory"', response.data)
        self.assertIn(b'id="add-category-create"', response.data)
        self.assertIn(b'id="add-subcategory-create"', response.data)

    def test_add_expense_rejects_subcategory_from_another_category(self):
        other_category = db.get_category_by_name("Salud")

        response = self.client.post(
            "/api/expenses/add",
            json={
                "concept": "Compra inválida",
                "amount": 10,
                "category_id": other_category["id"],
                "subcategory_id": self.subcategory["id"],
                "user_id": self.user["id"],
                "date": "2026-07-20",
            },
            headers=self.csrf_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no pertenece", response.get_json()["error"])

    def test_inline_taxonomy_endpoints_reject_normalized_duplicates(self):
        category_response = self.client.post(
            "/api/categories/add", json={"name": "  hÓGAR  "}, headers=self.csrf_headers
        )
        subcategory_response = self.client.post(
            "/api/subcategories/add",
            json={"category_id": self.category["id"], "name": "supermercádo"},
            headers=self.csrf_headers,
        )

        self.assertEqual(category_response.status_code, 409)
        self.assertEqual(subcategory_response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
