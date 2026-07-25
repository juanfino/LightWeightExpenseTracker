import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from support import reset_database  # noqa: E402


class MultiCurrencyDBTests(unittest.TestCase):
    def setUp(self):
        reset_database({"123": "Tester"})
        self.user = db.get_user_by_telegram_id("123")

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

if __name__ == "__main__":
    unittest.main()
