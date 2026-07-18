import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402


class MultiCurrencyDBTests(unittest.TestCase):
    def setUp(self):
        self._old_db_path = db.DB_PATH
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = str(Path(self._tmp.name) / "gastos.db")
        db.init_db({"123": "Tester"})
        self.user = db.get_user_by_telegram_id("123")

    def tearDown(self):
        db.DB_PATH = self._old_db_path
        self._tmp.cleanup()

    def test_aggregations_never_mix_ars_and_usd(self):
        db.create_expense_full(self.user["id"], None, "Pesos", 1000, "2026-07-01")
        db.create_expense_full(
            self.user["id"], None, "Dolares", 25, "2026-07-02", currency="USD"
        )

        ars = db.get_expenses_summary_by_category(2026, 7, currency="ARS")
        usd = db.get_expenses_summary_by_category(2026, 7, currency="USD")

        self.assertEqual(sum(row["total"] for row in ars), 1000)
        self.assertEqual(sum(row["total"] for row in usd), 25)

    def test_fixed_expense_requires_matching_currency_and_locks_changes(self):
        usd_fixed = db.create_fixed_expense("Hosting", 20, None, currency="USD")
        ars_expense = db.create_expense_full(
            self.user["id"], None, "Hosting", 20000, "2026-07-01"
        )
        usd_expense = db.create_expense_full(
            self.user["id"], None, "Hosting", 20, "2026-07-01", currency="USD"
        )

        self.assertFalse(db.link_expense_to_fixed(ars_expense, usd_fixed, 2026, 7))
        self.assertTrue(db.link_expense_to_fixed(usd_expense, usd_fixed, 2026, 7))
        self.assertTrue(
            db.update_expense(usd_expense, "Hosting anual", 21, None, currency="USD")
        )
        self.assertFalse(
            db.update_expense(usd_expense, "Hosting anual", 21, None, currency="ARS")
        )
        self.assertFalse(
            db.update_fixed_expense(usd_fixed, "Hosting", 20, None, currency="ARS")
        )


class MultiCurrencyMigrationTests(unittest.TestCase):
    def test_legacy_rows_are_migrated_to_ars(self):
        old_db_path = db.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db.DB_PATH = str(Path(tmp) / "legacy.db")
            try:
                with sqlite3.connect(db.DB_PATH) as conn:
                    conn.executescript(
                        """
                        CREATE TABLE expenses (id INTEGER PRIMARY KEY, amount REAL);
                        INSERT INTO expenses (amount) VALUES (100);
                        CREATE TABLE fixed_expenses (id INTEGER PRIMARY KEY, estimated_amount REAL);
                        INSERT INTO fixed_expenses (estimated_amount) VALUES (200);
                        CREATE TABLE expense_classifications (
                            id INTEGER PRIMARY KEY, amount REAL
                        );
                        INSERT INTO expense_classifications (amount) VALUES (100);
                        """
                    )

                db._migrate_currencies()

                with sqlite3.connect(db.DB_PATH) as conn:
                    expense_currency = conn.execute(
                        "SELECT currency FROM expenses"
                    ).fetchone()[0]
                    fixed_currency = conn.execute(
                        "SELECT currency FROM fixed_expenses"
                    ).fetchone()[0]
                    classification_currency = conn.execute(
                        "SELECT currency FROM expense_classifications"
                    ).fetchone()[0]

                self.assertEqual(expense_currency, "ARS")
                self.assertEqual(fixed_currency, "ARS")
                self.assertEqual(classification_currency, "ARS")
            finally:
                db.DB_PATH = old_db_path


if __name__ == "__main__":
    unittest.main()
