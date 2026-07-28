import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import fixed_matcher  # noqa: E402


class ExpensePeriodTests(unittest.TestCase):
    def test_accepts_postgres_aware_datetime(self):
        baires = timezone(timedelta(hours=-3))
        created_at = datetime(2026, 7, 1, 1, 30, tzinfo=timezone.utc)

        self.assertEqual(fixed_matcher.expense_period(created_at, baires), (2026, 6))

    def test_accepts_naive_datetime_as_utc(self):
        baires = timezone(timedelta(hours=-3))
        created_at = datetime(2026, 7, 1, 1, 30)

        self.assertEqual(fixed_matcher.expense_period(created_at, baires), (2026, 6))

    def test_keeps_legacy_string_support(self):
        baires = timezone(timedelta(hours=-3))

        self.assertEqual(
            fixed_matcher.expense_period("2026-07-01 03:30:00", baires),
            (2026, 7),
        )


if __name__ == "__main__":
    unittest.main()
