import sys
import unittest
from pathlib import Path

from psycopg import IntegrityError

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import pgcompat  # noqa: E402
import seed  # noqa: E402
import sqlro  # noqa: E402
from support import reset_database  # noqa: E402


class TenantIsolationTests(unittest.TestCase):
    def setUp(self):
        reset_database({"100": "Familia A"})
        self.user_a = db.get_user_by_telegram_id("100")
        pgcompat.set_family_id(1)
        self.expense_a = db.create_expense(
            self.user_a["id"], None, "Solo A", 100, "Solo A 100"
        )

        with pgcompat.current_pool().connection() as raw:
            self.family_b = raw.execute(
                "INSERT INTO families (name) VALUES ('Familia B') RETURNING id"
            ).fetchone()[0]
            user_b = raw.execute(
                "INSERT INTO users (telegram_id, name) VALUES ('200', 'Familia B') RETURNING id"
            ).fetchone()[0]
            raw.execute(
                "INSERT INTO memberships (user_id, family_id, role) VALUES (%s, %s, 'owner')",
                (user_b, self.family_b),
            )
            raw.commit()
        pgcompat.set_family_id(self.family_b)
        with db.get_conn() as conn:
            seed.create_family_defaults(conn, self.family_b)
        self.user_b = db.get_user_by_telegram_id("200")
        self.expense_b = db.create_expense(
            self.user_b["id"], None, "Solo B", 200, "Solo B 200"
        )

    def test_everyday_reads_are_isolated(self):
        pgcompat.set_family_id(1)
        concepts = {row["concept"] for row in db.get_recent_expenses()}
        self.assertIn("Solo A", concepts)
        self.assertNotIn("Solo B", concepts)
        self.assertIsNone(db.get_expense_by_id(self.expense_b))
        self.assertTrue(db.has_expenses())
        self.assertTrue(db.delete_expense(self.expense_a))
        self.assertFalse(db.has_expenses(), "family B expenses must stay hidden")
        pgcompat.set_family_id(self.family_b)
        self.assertTrue(db.has_expenses())

    def test_hostile_generated_sql_is_isolated(self):
        pgcompat.set_family_id(1)
        hostile_queries = [
            "SELECT * FROM expenses",
            f"SELECT * FROM expenses WHERE family_id = {self.family_b}",
            "SELECT * FROM expenses WHERE 1=1",
            "WITH leaked AS (SELECT * FROM expenses) SELECT * FROM leaked",
            "SELECT e.* FROM expenses e CROSS JOIN categories c",
            "SELECT * FROM expenses WHERE id IN (SELECT id FROM expenses)",
        ]
        for query in hostile_queries:
            with self.subTest(query=query):
                rows = sqlro.run_readonly(query)
                self.assertTrue(all(row["family_id"] == 1 for row in rows))

    def test_cross_family_update_and_delete_are_noops(self):
        pgcompat.set_family_id(1)
        self.assertFalse(db.delete_expense(self.expense_b))
        self.assertFalse(db.update_expense_amount(self.expense_b, self.user_a["id"], 1))
        pgcompat.set_family_id(self.family_b)
        self.assertEqual(db.get_expense_by_id(self.expense_b)["amount"], 200)

    def test_cross_family_foreign_key_is_rejected(self):
        pgcompat.set_family_id(self.family_b)
        category_b = db.get_all_categories()[0]["id"]
        pgcompat.set_family_id(1)
        with self.assertRaises(IntegrityError):
            db.create_expense(
                self.user_a["id"], category_b, "Referencia cruzada", 1, "x"
            )

    def test_family_b_can_be_populated_without_code_changes(self):
        pgcompat.set_family_id(self.family_b)
        self.assertTrue(db.get_all_categories())
        self.assertEqual(db.get_expense_by_id(self.expense_b)["concept"], "Solo B")

    def test_every_tenant_table_has_forced_rls(self):
        expected = {
            "families", "users", "memberships", "categories", "subcategories",
            "keywords", "expenses", "fixed_expenses", "cambios_dolar",
            "ipc_series", "reports", "expense_classifications", "llm_calls",
            "income_categories", "incomes",
            "shopping_items",
        }
        with db.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT relname
                FROM pg_class
                WHERE relname = ANY(?)
                  AND relrowsecurity IS TRUE
                  AND relforcerowsecurity IS TRUE
                """,
                (list(expected),),
            ).fetchall()
        self.assertEqual({row["relname"] for row in rows}, expected)

    def test_llm_usage_is_tenant_scoped(self):
        pgcompat.set_family_id(1)
        pgcompat.set_user_id(self.user_a["id"])
        db.record_llm_call("intent", "test-model", 100, 20, 0.001, 50, True)
        self.assertEqual(len(sqlro.run_readonly("SELECT * FROM llm_calls")), 1)
        pgcompat.set_family_id(self.family_b)
        self.assertEqual(sqlro.run_readonly("SELECT * FROM llm_calls"), [])


if __name__ == "__main__":
    unittest.main()
