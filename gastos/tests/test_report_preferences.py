import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard  # noqa: E402
import db  # noqa: E402
import report  # noqa: E402
from support import authenticated_client, reset_database  # noqa: E402


class ReportPreferencesWebTests(unittest.TestCase):
    def setUp(self):
        reset_database({"100": "Owner", "200": "Member"})
        self.owner, self.owner_headers = authenticated_client(dashboard, "100")
        self.member, self.member_headers = authenticated_client(dashboard, "200")

    def test_missing_row_returns_behavior_preserving_defaults(self):
        response = self.owner.get("/api/resumenes/preferences")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["preferences"], {
            "emphasis": [],
            "tone": "neutral",
            "length": "medium",
            "focus": "",
            "allow_suggestions": False,
        })

    def test_any_member_can_update_shared_preferences(self):
        payload = {
            "emphasis": ["foreign_currency", "outliers"],
            "tone": "direct",
            "length": "short",
            "focus": "Prestá atención al delivery",
            "allow_suggestions": True,
        }
        response = self.member.put(
            "/api/resumenes/preferences", json=payload, headers=self.member_headers
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("próxima generación", response.get_json()["message"])
        self.assertEqual(
            self.owner.get("/api/resumenes/preferences").get_json()["preferences"],
            payload,
        )
        self.assertEqual(db.get_report_preferences()["updated_by_user_id"], 2)

    def test_invalid_and_oversized_input_is_rejected(self):
        for payload in (
            {"tone": "aggressive"},
            {"emphasis": ["income"]},
            {"focus": "x" * 401},
            {"allow_suggestions": "yes"},
        ):
            with self.subTest(payload=payload):
                response = self.owner.put(
                    "/api/resumenes/preferences", json=payload, headers=self.owner_headers
                )
                self.assertEqual(response.status_code, 400)

        response = self.owner.put(
            "/api/resumenes/preferences", json=[], headers=self.owner_headers
        )
        self.assertEqual(response.status_code, 400)

    def test_generation_persists_the_resolved_preference_snapshot(self):
        preferences = {
            "emphasis": ["outliers"],
            "tone": "warm",
            "length": "long",
            "focus": "Delivery",
            "allow_suggestions": True,
        }
        self.owner.put(
            "/api/resumenes/preferences", json=preferences, headers=self.owner_headers
        )
        dossier = {
            "currencies": {
                "ARS": {"variable_expenses": [], "fixed_expenses": {"total_paid": 0}},
                "USD": {"variable_expenses": [], "fixed_expenses": {"total_paid": 0}},
            }
        }
        output = {"headline": "H", "summary": "S", "findings": [], "questions": []}

        with patch.object(report.inflation, "refresh"), \
             patch.object(report.dossier_module, "build_dossier", return_value=dossier), \
             patch.object(report.db, "get_recent_classifications_before", return_value=[]), \
             patch.object(report.report_ai, "classify_expenses", return_value=[]), \
             patch.object(report.report_ai, "analyze", return_value=output) as analyze_mock, \
             patch.object(report, "fingerprint", return_value="facts"):
            generated = report.generate_report(2026, 8)

        self.assertEqual(generated["preferences"], preferences)
        self.assertEqual(generated["output"], output)
        self.assertEqual(generated["prompt_version"], report.report_ai.prompt_version(preferences))
        narrated_dossier = analyze_mock.call_args.args[0]
        self.assertIn("forecast", narrated_dossier)
        self.assertEqual(narrated_dossier["forecast"]["target_period"], {"year": 2026, "month": 9})


if __name__ == "__main__":
    unittest.main()
