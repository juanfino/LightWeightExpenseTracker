import sys
import unittest
from decimal import Decimal
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import forecast  # noqa: E402
from support import reset_database  # noqa: E402


class ForecastTests(unittest.TestCase):
    def setUp(self):
        reset_database({"123": "Tester"})
        self.user = db.get_user_by_telegram_id("123")
        self.food = db.create_category("Forecast Food", "F", "#f59e0b")
        self.misc = db.create_category("Forecast Misc", "M", "#fb923c")

    def _expense(self, date, amount, category=None, currency="ARS"):
        return db.create_expense_full(
            self.user["id"], category or self.food, "Forecast", amount, date,
            currency=currency,
        )

    def test_thin_history_exposes_fixed_only_and_no_variable_estimate(self):
        self._expense("2026-07-01", 100)
        fixed_id = db.create_fixed_expense("Alquiler forecast", 500, None)
        with db.get_conn() as conn:
            conn.execute("UPDATE fixed_expenses SET created_at=? WHERE id=?", ("2026-07-01 03:00:00", fixed_id))

        result = forecast.build_forecast(2026, 8)["currencies"]["ARS"]

        self.assertFalse(result["variable_available"])
        self.assertTrue(result["total"]["fixed_only"])
        self.assertEqual(result["total"]["central"], Decimal("500.00"))
        self.assertNotIn("low", result["tail"])

    def test_currencies_have_independent_data_floors(self):
        for month in (6, 7, 8):
            self._expense(f"2026-{month:02d}-01", 100 + month)
        self._expense("2026-08-02", 20, currency="USD")

        result = forecast.build_forecast(2026, 8)["currencies"]

        self.assertTrue(result["ARS"]["variable_available"])
        self.assertFalse(result["USD"]["variable_available"])

    def test_forecast_uses_period_currencies_and_default_only(self):
        db.set_family_default_currency("BRL")
        self._expense("2026-08-01", 20, currency="EUR")

        result = forecast.build_forecast(2026, 8)["currencies"]

        self.assertEqual(list(result), ["BRL", "EUR"])
        self.assertEqual(result["BRL"]["inflation_status"], "real_not_applicable")
        self.assertEqual(result["EUR"]["inflation_status"], "real_not_applicable")

    def test_outlier_does_not_drag_habitual_category_median(self):
        for month, amount in zip((4, 5, 6, 7, 8), (100, 100, 10000, 100, 100)):
            self._expense(f"2026-{month:02d}-01", amount)

        category = forecast.build_forecast(2026, 8)["currencies"]["ARS"]["habitual"]["categories"][0]

        self.assertEqual(category["range"]["central"], Decimal("100.00"))
        self.assertEqual(category["range"]["high"], Decimal("100.00"))

    def test_missing_ipc_series_is_nominal_not_an_error(self):
        for month in (6, 7, 8):
            self._expense(f"2026-{month:02d}-01", month * 100)

        result = forecast.build_forecast(2026, 8)["currencies"]["ARS"]

        self.assertEqual(result["inflation_status"], "real_unavailable")
        self.assertTrue(result["variable_available"])

    def test_sparse_tail_is_not_omitted(self):
        for month in (4, 5, 6, 7, 8):
            self._expense(f"2026-{month:02d}-01", 100)
        self._expense("2026-06-10", 500, category=self.misc)

        tail = forecast.build_forecast(2026, 8)["currencies"]["ARS"]["tail"]

        self.assertEqual(tail["months_with_tail"], 1)
        self.assertEqual(tail["central"], Decimal("100.00"))
        self.assertEqual(tail["high"], Decimal("500.00"))

    def test_inflation_projection_ignores_actual_target_index(self):
        series = [
            {"year": 2026, "month": 5, "value": Decimal("100")},
            {"year": 2026, "month": 6, "value": Decimal("110")},
            {"year": 2026, "month": 7, "value": Decimal("121")},
            {"year": 2026, "month": 8, "value": Decimal("133.1")},
            {"year": 2026, "month": 9, "value": Decimal("999")},
        ]

        factors, status = forecast._projected_inflation_factors(
            [(2026, 8)], (2026, 8), series
        )

        self.assertEqual(status, "adjusted")
        self.assertEqual(factors[(2026, 8)], Decimal("1.1"))

    def test_target_and_cutoff_are_fixed_by_report_period(self):
        self._expense("2026-08-01", 100)
        self._expense("2026-10-01", 999999)

        result = forecast.build_forecast(2026, 8)

        self.assertEqual(result["source_period"], {"year": 2026, "month": 8})
        self.assertEqual(result["target_period"], {"year": 2026, "month": 9})
        self.assertEqual(result["currencies"]["ARS"]["history_months"], 1)

    def test_persisted_forecast_is_immutable_and_backtest_uses_actual_target(self):
        for month in (6, 7, 8):
            self._expense(f"2026-{month:02d}-01", 100)
        original = forecast.build_forecast(2026, 8)
        report_id = db.create_report(2026, 8, None, "test", "{}", None, "fp", False)
        db.save_report_forecast(report_id, original)

        self._expense("2026-09-01", 700)
        stored = db.get_report_forecast(report_id)
        actual = forecast.actuals(stored)

        self.assertEqual(stored["currencies"]["ARS"]["total"]["central"], Decimal("100.00"))
        self.assertEqual(actual["ARS"]["total"], Decimal("700.00"))


if __name__ == "__main__":
    unittest.main()
