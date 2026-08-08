import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
import pgcompat  # noqa: E402
from support import reset_database  # noqa: E402


class DailyQuoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_database({"100": "Owner"})

    def test_seed_and_metadata_are_preserved(self):
        with pgcompat.current_pool().connection() as conn:
            result = conn.execute(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE NOT verified), "
                "COUNT(*) FILTER (WHERE language='es-AR') FROM quotes"
            ).fetchone()
        self.assertEqual(tuple(result), (80, 10, 7))

    def test_selection_is_stable_and_family_specific(self):
        day = date(2026, 8, 8)
        first = db.get_daily_quote(1, day, "es-AR")
        self.assertEqual(first["id"], db.get_daily_quote(1, day, "es-AR")["id"])
        ids = {db.get_daily_quote(i, day, "es-AR")["id"] for i in range(1, 8)}
        self.assertGreater(len(ids), 1)

    def test_language_degrades_to_available_quotes(self):
        self.assertIsNotNone(db.get_daily_quote(1, date(2026, 8, 8), "pt-BR"))

    def test_query_failure_is_decorative(self):
        with patch("db.get_conn", side_effect=RuntimeError("database unavailable")):
            self.assertIsNone(db.get_daily_quote(1, date(2026, 8, 8)))


if __name__ == "__main__":
    unittest.main()
