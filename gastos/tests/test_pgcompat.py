import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import pgcompat  # noqa: E402


class PgCompatInsertTests(unittest.TestCase):
    def test_conflict_do_nothing_does_not_request_missing_id(self):
        statement, wants_id = pgcompat._sql(
            """
            INSERT INTO income_categories (family_id, name)
            VALUES (?, ?)
            ON CONFLICT (family_id, name) DO NOTHING
            """
        )
        self.assertFalse(wants_id)
        self.assertNotIn("RETURNING id", statement)

    def test_regular_insert_still_returns_generated_id(self):
        statement, wants_id = pgcompat._sql(
            "INSERT INTO income_categories (name) VALUES (?)"
        )
        self.assertTrue(wants_id)
        self.assertTrue(statement.endswith("RETURNING id"))


if __name__ == "__main__":
    unittest.main()
