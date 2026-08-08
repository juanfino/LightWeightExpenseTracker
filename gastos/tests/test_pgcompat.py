import sys
import unittest
from decimal import Decimal
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import pgcompat  # noqa: E402


class _FakeCursor:
    rowcount = 2

    def __init__(self):
        self.call = None

    def executemany(self, statement, params_seq):
        self.call = (statement, params_seq)


class _FakePsycopgConnection:
    def __init__(self):
        self.cursor_instance = _FakeCursor()

    def cursor(self):
        return self.cursor_instance


class PgCompatInsertTests(unittest.TestCase):
    def test_row_preserves_decimal_values(self):
        value = Decimal("123.45")

        row = pgcompat.Row(["amount"], [value])

        self.assertIs(row["amount"], value)
        self.assertIsInstance(row["amount"], Decimal)

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

    def test_executemany_uses_cursor_api(self):
        raw = _FakePsycopgConnection()
        connection = pgcompat.Connection(raw)
        params = [(1, "uno"), (2, "dos")]

        result = connection.executemany(
            "INSERT INTO expense_classifications (report_id, label) VALUES (?, ?)",
            params,
        )

        statement, passed_params = raw.cursor_instance.call
        self.assertIn("VALUES (%s, %s)", statement)
        self.assertEqual(passed_params, params)
        self.assertEqual(result.rowcount, 2)


if __name__ == "__main__":
    unittest.main()
