import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import currency_detection  # noqa: E402
import db  # noqa: E402
import exchange  # noqa: E402
import parser  # noqa: E402
import pgcompat  # noqa: E402
from support import reset_database  # noqa: E402


CATALOGUE = [
    {"code": "ARS", "symbol": "$"},
    {"code": "USD", "symbol": "US$"},
    {"code": "BRL", "symbol": "R$"},
    {"code": "EUR", "symbol": "€"},
]


class CurrencyDetectionTests(unittest.TestCase):
    def test_explicit_eur_is_stripped_from_concept(self):
        parsed = parser.parse_message("Hotel 200 EUR", CATALOGUE, "ARS")
        self.assertEqual(parsed["concept"], "Hotel")
        self.assertEqual(parsed["amount"], Decimal("200.00"))
        self.assertEqual(parsed["currency"], "EUR")

    def test_omitted_and_ambiguous_markers_use_family_default(self):
        self.assertEqual(parser.parse_message("Hotel 200", CATALOGUE, "BRL")["currency"], "BRL")
        self.assertEqual(parser.parse_message("Hotel $ 200", CATALOGUE, "BRL")["currency"], "BRL")
        self.assertEqual(parser.parse_message("Hotel 200 pesos", CATALOGUE, "BRL")["currency"], "BRL")

    def test_unknown_currency_next_to_amount_is_not_silently_saved(self):
        with self.assertRaises(currency_detection.UnknownCurrencyError) as raised:
            parser.parse_message("Hotel 200 XYZ", CATALOGUE, "ARS")
        self.assertEqual(raised.exception.token, "XYZ")
        self.assertEqual(parser.parse_message("YPF 200", CATALOGUE, "ARS")["concept"], "Ypf")

    def test_exchange_prefilter_skips_ordinary_foreign_expense(self):
        self.assertFalse(exchange.looks_like_exchange("Hotel 200 EUR", CATALOGUE))
        self.assertTrue(exchange.looks_like_exchange("cambié 100 BRL por 18 EUR", CATALOGUE))


class GenericExchangeDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_database({"100": "Tester"})
        cls.user = db.get_user_by_telegram_id("100")
        pgcompat.set_family_id(cls.user["family_id"])

    def setUp(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM cambios_dolar")

    def test_non_default_pair_round_trips_directionally(self):
        cambio_id = db.registrar_cambio(
            "2026-08-08", "100.00", "BRL", "18.25", "EUR", self.user["name"]
        )
        row = dict(db.get_cambios_historial()[0])
        self.assertEqual(row["id"], cambio_id)
        self.assertEqual(row["amount_given"], Decimal("100.00"))
        self.assertEqual(row["currency_given"], "BRL")
        self.assertEqual(row["amount_received"], Decimal("18.25"))
        self.assertEqual(row["currency_received"], "EUR")
        self.assertEqual(row["rate_received_per_given"], Decimal("0.182500000000000000"))
        self.assertIsNone(exchange.derived_trade_label(row, "ARS"))

    def test_buy_sell_labels_are_derived_from_default(self):
        sold = {"currency_given": "USD", "currency_received": "ARS"}
        bought = {"currency_given": "ARS", "currency_received": "USD"}
        self.assertEqual(exchange.derived_trade_label(sold, "ARS"), "venta")
        self.assertEqual(exchange.derived_trade_label(bought, "ARS"), "compra")
        self.assertIsNone(exchange.derived_trade_label(sold, "BRL"))

    def test_pair_scoped_rate_history_excludes_reverse_direction(self):
        db.registrar_cambio("2026-08-01", 10, "USD", 15000, "ARS", self.user["name"])
        db.registrar_cambio("2026-08-02", 15000, "ARS", 10, "USD", self.user["name"])
        forward = db.get_cambios_cotizacion_historica("USD", "ARS")
        reverse = db.get_cambios_cotizacion_historica("ARS", "USD")
        self.assertEqual(len(forward), 1)
        self.assertEqual(len(reverse), 1)
        self.assertEqual(forward[0]["rate_received_per_given"], Decimal("1500.000000000000000000"))
        self.assertEqual(reverse[0]["rate_received_per_given"], Decimal("0.000666666666666667"))

    def test_default_exchange_pair_never_duplicates_family_default(self):
        db.set_family_default_currency("USD")

        given, received = db.default_exchange_pair()

        self.assertEqual(received, "USD")
        self.assertNotEqual(given, received)
        self.assertIn(given, db.SUPPORTED_CURRENCIES)


if __name__ == "__main__":
    unittest.main()
