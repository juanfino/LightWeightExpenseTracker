import sys
import unittest
from datetime import datetime
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import dossier  # noqa: E402
import report  # noqa: E402
from support import authenticated_client, reset_database  # noqa: E402


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

    def test_supported_currencies_come_from_seeded_reference_data(self):
        self.assertEqual(set(db.SUPPORTED_CURRENCIES), {"ARS", "USD", "BRL", "EUR"})
        for code in ("ars", "USD", " brl ", "EUR"):
            self.assertEqual(db.normalize_currency(code), code.strip().upper())
        with self.assertRaises(ValueError):
            db.normalize_currency("ZZZ")

    def test_brl_round_trips_as_decimal(self):
        expense_id = db.create_expense_full(
            self.user["id"], None, "Brasil", "1234.56", "2026-07-03", currency="BRL"
        )

        stored = db.get_expense_by_id(expense_id)
        self.assertEqual(stored["currency"], "BRL")
        self.assertEqual(str(stored["amount"]), "1234.56")

    def test_implicit_business_currency_uses_family_default(self):
        db.set_family_default_currency("BRL")

        expense_id = db.create_expense_full(
            self.user["id"], None, "Brasil", 10, "2026-07-03"
        )
        fixed_id = db.create_fixed_expense("Internet", 20, None)

        self.assertEqual(db.get_expense_by_id(expense_id)["currency"], "BRL")
        self.assertEqual(db.get_fixed_expense_by_id(fixed_id)["currency"], "BRL")

    def test_dashboard_secondary_totals_include_every_period_currency(self):
        import dashboard

        db.set_family_default_currency("BRL")
        now = datetime.now()
        db.create_expense_full(
            self.user["id"], None, "Europa", 42,
            f"{now.year:04d}-{now.month:02d}-01", currency="EUR",
        )
        client, _headers = authenticated_client(dashboard, "123")

        response = client.get(
            f"/api/monthly?year={now.year}&month={now.month}&currency=EUR"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["currency_totals"],
            [{"currency": "BRL", "total": 0.0}, {"currency": "EUR", "total": 42.0}],
        )

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

class ReportCurrencyDossierTests(unittest.TestCase):
    """The monthly report used to be an ARS report with a USD footnote — dossier.py
    and report.py now build ARS and USD as parallel, same-shape blocks. These guard
    the concrete bugs that shape fixed (see CLAUDE.md / plan): a USD fixed expense
    being invisible, USD amounts leaking into ARS aggregates, and the dollar-coverage
    ratio being computed against an ARS-only denominator."""

    def setUp(self):
        reset_database({"123": "Tester"})
        self.user = db.get_user_by_telegram_id("123")

    def test_usd_fixed_expense_appears_only_in_its_own_currency_block(self):
        usd_fixed = db.create_fixed_expense("Hosting", 20, None, currency="USD")
        usd_expense = db.create_expense_full(
            self.user["id"], None, "Hosting", 20, "2026-07-05", currency="USD"
        )
        self.assertTrue(db.link_expense_to_fixed(usd_expense, usd_fixed, 2026, 7))

        d = dossier.build_dossier(2026, 7)

        usd_fixed_block = d["currencies"]["USD"]["fixed_expenses"]
        self.assertEqual(usd_fixed_block["count_paid"], 1)
        self.assertEqual(usd_fixed_block["total_paid"], 20)

        ars_fixed_block = d["currencies"]["ARS"]["fixed_expenses"]
        self.assertEqual(ars_fixed_block["items"], [])
        self.assertEqual(ars_fixed_block["total_paid"], 0)

    def test_currency_totals_are_disjoint(self):
        db.create_expense_full(self.user["id"], None, "Super", 1000, "2026-07-01")
        db.create_expense_full(
            self.user["id"], None, "Viaje", 25, "2026-07-02", currency="USD"
        )

        d = dossier.build_dossier(2026, 7)

        self.assertEqual(d["currencies"]["ARS"]["base"]["total"], 1000)
        self.assertEqual(d["currencies"]["USD"]["base"]["total"], 25)

    def test_variable_expenses_carry_their_real_currency(self):
        db.create_expense_full(self.user["id"], None, "Super", 1000, "2026-07-01")
        db.create_expense_full(
            self.user["id"], None, "Viaje", 25, "2026-07-02", currency="USD"
        )

        d = dossier.build_dossier(2026, 7)

        self.assertEqual(d["currencies"]["ARS"]["variable_expenses"][0]["currency"], "ARS")
        self.assertEqual(d["currencies"]["USD"]["variable_expenses"][0]["currency"], "USD")

    def test_recurrence_evidence_keeps_currencies_separate(self):
        # Same normalized concept ("hotel"), wildly different scale in each currency —
        # they must not be merged into one recurrence-evidence entry.
        db.create_expense_full(self.user["id"], None, "Hotel", 200000, "2026-07-01")
        db.create_expense_full(
            self.user["id"], None, "Hotel", 200, "2026-07-02", currency="USD"
        )

        d = dossier.build_dossier(2026, 7)

        self.assertIn("ARS:hotel", d["recurrence_evidence"])
        self.assertIn("USD:hotel", d["recurrence_evidence"])
        self.assertEqual(d["recurrence_evidence"]["ARS:hotel"]["currency"], "ARS")
        self.assertEqual(d["recurrence_evidence"]["USD:hotel"]["currency"], "USD")

    def test_build_partitions_sums_each_currency_independently(self):
        ars_id = db.create_expense_full(self.user["id"], None, "Super", 1000, "2026-07-01")
        usd_id = db.create_expense_full(
            self.user["id"], None, "Viaje", 25, "2026-07-02", currency="USD"
        )
        d = dossier.build_dossier(2026, 7)
        all_variable = [
            e for cur in ("ARS", "USD") for e in d["currencies"][cur]["variable_expenses"]
        ]
        classifications = [
            {"expense_id": ars_id, "label": "recurring", "confidence": 0.9},
            {"expense_id": usd_id, "label": "exceptional", "confidence": 0.9},
        ]

        partitions = report._build_partitions(d, all_variable, classifications)

        self.assertEqual(partitions["ARS"]["recurring_total"], 1000)
        self.assertEqual(partitions["ARS"]["exceptional_total"], 0)
        self.assertEqual(partitions["USD"]["exceptional_total"], 25)
        self.assertEqual(partitions["USD"]["recurring_total"], 0)

    def test_usd_contrasts_are_real_not_applicable_not_real_unavailable(self):
        db.create_expense_full(self.user["id"], None, "Viaje", 100, "2026-07-01", currency="USD")
        db.create_expense_full(self.user["id"], None, "Viaje", 80, "2026-06-01", currency="USD")

        d = dossier.build_dossier(2026, 7)
        prev = d["currencies"]["USD"]["contrasts"]["prev_month"]

        self.assertTrue(prev["available"])
        self.assertTrue(prev.get("real_not_applicable"))
        self.assertNotIn("real_unavailable", prev)
        self.assertNotIn("real_current", prev)

    def test_equivalence_prefers_this_months_sale_rate(self):
        db.create_expense_full(self.user["id"], None, "Viaje", 100, "2026-07-01", currency="USD")
        db.registrar_cambio("2026-07-10", 200, 1500, "Tester", tipo="venta")

        d = dossier.build_dossier(2026, 7)

        self.assertTrue(d["equivalence"]["available"])
        self.assertEqual(d["equivalence"]["rate_source"], "ventas_mes")
        self.assertEqual(d["equivalence"]["rate"], 1500)
        self.assertEqual(d["equivalence"]["usd_total_in_ars"], 150000)

    def test_equivalence_falls_back_to_this_months_purchase_rate(self):
        db.create_expense_full(self.user["id"], None, "Viaje", 100, "2026-07-01", currency="USD")
        db.registrar_cambio("2026-07-10", 200, 1400, "Tester", tipo="compra")

        d = dossier.build_dossier(2026, 7)

        self.assertEqual(d["equivalence"]["rate_source"], "compras_mes")
        self.assertEqual(d["equivalence"]["rate"], 1400)

    def test_equivalence_falls_back_to_recent_history(self):
        db.create_expense_full(self.user["id"], None, "Viaje", 100, "2026-07-01", currency="USD")
        db.registrar_cambio("2026-05-15", 50, 1300, "Tester", tipo="venta")

        d = dossier.build_dossier(2026, 7)

        self.assertTrue(d["equivalence"]["available"])
        self.assertEqual(d["equivalence"]["rate_source"], "mes_anterior")
        self.assertEqual(d["equivalence"]["rate"], 1300)
        self.assertEqual(d["equivalence"]["rate_period"], {"year": 2026, "month": 5})

    def test_equivalence_unavailable_without_any_dollar_operation(self):
        db.create_expense_full(self.user["id"], None, "Viaje", 100, "2026-07-01", currency="USD")

        d = dossier.build_dossier(2026, 7)

        self.assertFalse(d["equivalence"]["available"])
        self.assertIsNone(d["equivalence"]["rate"])
        self.assertIsNone(d["equivalence"]["usd_total_in_ars"])

    def test_coverage_ratio_uses_the_combined_ars_equivalent_denominator(self):
        db.create_expense_full(self.user["id"], None, "Super", 1000, "2026-07-01")
        db.create_expense_full(self.user["id"], None, "Viaje", 100, "2026-07-02", currency="USD")
        db.registrar_cambio("2026-07-10", 200, 1500, "Tester", tipo="venta")

        d = dossier.build_dossier(2026, 7)

        # combined = 1000 (ARS) + 100 * 1500 (USD at this month's own rate) = 151000
        # venta.total_ars = 200 * 1500 = 300000 -> ratio = 300000 / 151000
        self.assertEqual(d["dollars"]["coverage_basis"], "pesos + dólares equivalentes")
        self.assertAlmostEqual(d["dollars"]["coverage_ratio"], round(300000 / 151000, 3))

    def test_dossier_derives_three_currencies_and_keeps_default_first(self):
        db.set_family_default_currency("BRL")
        db.create_expense_full(self.user["id"], None, "Brasil", 100, "2026-07-01", currency="BRL")
        db.create_expense_full(self.user["id"], None, "Argentina", 200, "2026-07-02", currency="ARS")
        db.create_expense_full(self.user["id"], None, "Europa", 30, "2026-07-03", currency="EUR")

        d = dossier.build_dossier(2026, 7)

        self.assertEqual(d["default_currency"], "BRL")
        self.assertEqual(d["currency_order"], ["BRL", "ARS", "EUR"])
        self.assertEqual(set(d["currencies"]), {"BRL", "ARS", "EUR"})
        self.assertNotIn("USD", d["currencies"])

    def test_default_only_period_still_has_one_coherent_currency_block(self):
        db.set_family_default_currency("EUR")

        d = dossier.build_dossier(2026, 7)

        self.assertEqual(d["currency_order"], ["EUR"])
        self.assertEqual(d["currencies"]["EUR"]["base"]["total"], 0)

    def test_non_series_currency_is_structurally_real_not_applicable(self):
        db.create_expense_full(self.user["id"], None, "Brasil", 100, "2026-07-01", currency="BRL")
        db.create_expense_full(self.user["id"], None, "Brasil", 80, "2026-06-01", currency="BRL")

        prev = dossier.build_dossier(2026, 7)["currencies"]["BRL"]["contrasts"]["prev_month"]

        self.assertTrue(prev["real_not_applicable"])
        self.assertNotIn("real_unavailable", prev)

    def test_each_foreign_currency_has_own_equivalence_or_unavailable(self):
        db.create_expense_full(self.user["id"], None, "Brasil", 100, "2026-07-01", currency="BRL")
        db.create_expense_full(self.user["id"], None, "Europa", 50, "2026-07-02", currency="EUR")
        db.registrar_cambio("2026-07-10", 10, "BRL", 2000, "ARS", "Tester")

        items = dossier.build_dossier(2026, 7)["equivalence"]["items"]

        self.assertEqual(items["BRL"]["rate"], 200)
        self.assertEqual(items["BRL"]["total_in_default"], 20000)
        self.assertTrue(items["BRL"]["available"])
        self.assertFalse(items["EUR"]["available"])
        self.assertIsNone(items["EUR"]["total_in_default"])

    def test_reverse_exchange_rate_values_foreign_in_default(self):
        db.create_expense_full(self.user["id"], None, "Brasil", 10, "2026-07-01", currency="BRL")
        db.registrar_cambio("2026-07-10", 2000, "ARS", 10, "BRL", "Tester")

        item = dossier.build_dossier(2026, 7)["equivalence"]["items"]["BRL"]

        self.assertEqual(item["rate_source"], "purchase_current_period")
        self.assertEqual(item["rate"], 200)
        self.assertEqual(item["total_in_default"], 2000)


if __name__ == "__main__":
    unittest.main()
