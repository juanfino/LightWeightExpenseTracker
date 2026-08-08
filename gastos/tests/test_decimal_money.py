import json
import sys
import unittest
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import inflation  # noqa: E402
import money  # noqa: E402
import report_ai  # noqa: E402


class MoneyTests(unittest.TestCase):
    def test_money_uses_half_up_rounding(self):
        self.assertEqual(money.amount("2.345"), Decimal("2.35"))
        self.assertEqual(money.amount("2.344"), Decimal("2.34"))

    def test_json_boundary_emits_numbers_not_strings(self):
        encoded = money.json_dumps({"amount": Decimal("8500.00"), "created_at": datetime(2026, 8, 8, 12, 30)})

        self.assertEqual(json.loads(encoded), {"amount": 8500.0, "created_at": "2026-08-08 12:30:00"})
        self.assertNotIn('"8500.00"', encoded)

    def test_formatting_separates_currency_metadata_from_reader_separators(self):
        usd = {"code": "USD", "symbol": "US$", "decimal_places": 2}

        self.assertEqual(money.format_amount(Decimal("5580.50"), usd), "US$ 5.580,50")
        self.assertEqual(money.format_amount(Decimal("5580.00"), usd), "US$ 5.580")

    def test_formatting_honors_non_two_decimal_currency(self):
        zero_decimal = {"code": "CLP", "symbol": "CLP$", "decimal_places": 0}

        self.assertEqual(money.format_amount(Decimal("1234.50"), zero_decimal), "CLP$ 1.235")

    @patch("inflation.db.get_ipc_value")
    def test_deflate_keeps_index_precision_and_quantizes_money(self, get_ipc_value):
        get_ipc_value.side_effect = [
            {"value": Decimal("123.456789012345")},
            {"value": Decimal("127.160492682715")},
        ]

        result = inflation.deflate(Decimal("100.00"), 2026, 1, 2026, 2)

        self.assertEqual(result, Decimal("103.00"))


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = (
            '{"classifications":[{"expense_id":1,"label":"recurring","confidence":0.9}]}'
            if kwargs["max_tokens"] == 16000
            else '{"headline":"ok","summary":"ok","findings":[],"questions":[]}'
        )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=response)])


class ReportPayloadTests(unittest.TestCase):
    def setUp(self):
        self.messages = _FakeMessages()
        self.client = SimpleNamespace(messages=self.messages)
        self.patches = [
            patch("report_ai._client", return_value=self.client),
            patch("report_ai.llm_limits.summary_call", side_effect=lambda: nullcontext()),
            patch("report_ai.llm_usage.started", return_value=0),
            patch("report_ai.llm_usage.record"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_classification_payload_amounts_are_json_numbers(self):
        dossier = {
            "recurrence_evidence": {
                "ARS:super": {"occurrences": [{"amount": Decimal("8500.00")}]}},
            "hard_facts": {"months_available": 3},
        }
        variable = [{"expense_id": 1, "concept": "Super", "amount": Decimal("8500.00"), "currency": "ARS"}]

        result = report_ai.classify_expenses(dossier, variable, [])

        self.assertEqual(result[0]["expense_id"], 1)
        payload = json.loads(self.messages.calls[0]["messages"][0]["content"])
        self.assertIsInstance(payload["expenses"][0]["amount"], float)
        self.assertIsInstance(payload["recurrence_evidence"]["ARS:super"]["occurrences"][0]["amount"], float)

    def test_narration_payload_amounts_are_json_numbers(self):
        dossier = {"currencies": {"ARS": {"base": {"total": Decimal("8500.00")}}}}

        result = report_ai.analyze(dossier)

        self.assertEqual(result["headline"], "ok")
        payload = json.loads(self.messages.calls[0]["messages"][0]["content"])
        self.assertIsInstance(payload["dossier"]["currencies"]["ARS"]["base"]["total"], float)


if __name__ == "__main__":
    unittest.main()
