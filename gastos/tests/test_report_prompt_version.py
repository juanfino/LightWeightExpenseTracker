import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import report  # noqa: E402
import report_ai  # noqa: E402


def _reverse_mapping_order(value):
    if isinstance(value, dict):
        return {
            key: _reverse_mapping_order(child)
            for key, child in reversed(list(value.items()))
        }
    if isinstance(value, list):
        return [_reverse_mapping_order(child) for child in value]
    return value


class PromptVersionTests(unittest.TestCase):
    def test_is_stable_and_carries_short_and_full_fingerprint(self):
        first = report_ai.prompt_version()
        second = report_ai.prompt_version()

        self.assertEqual(first, second)
        short, full = first.split(":sha256:")
        self.assertRegex(short, r"^report-v1-[0-9a-f]{12}$")
        self.assertRegex(full, r"^[0-9a-f]{64}$")
        self.assertTrue(full.startswith(short.rsplit("-", 1)[1]))

    def test_prompt_text_change_changes_fingerprint(self):
        changed = copy.deepcopy(report_ai._ANALYZE_CALL_CONFIG)
        changed["system"] += "\nA test-only prompt change."

        self.assertNotEqual(
            report_ai.prompt_version(),
            report_ai._derive_prompt_version(report_ai._CLASSIFY_CALL_CONFIG, changed),
        )

    def test_model_change_does_not_change_fingerprint(self):
        baseline = report_ai.prompt_version()

        with patch.dict(os.environ, {"REPORT_ANTHROPIC_MODEL": "different-model"}):
            self.assertEqual(report_ai.prompt_version(), baseline)

    def test_schema_mapping_order_does_not_change_fingerprint(self):
        classify = _reverse_mapping_order(report_ai._CLASSIFY_CALL_CONFIG)
        analyze = _reverse_mapping_order(report_ai._ANALYZE_CALL_CONFIG)

        self.assertEqual(
            report_ai.prompt_version(),
            report_ai._derive_prompt_version(classify, analyze),
        )

    def test_legacy_prompt_version_remains_readable(self):
        row = {
            "id": 1,
            "year": 2026,
            "month": 7,
            "generated_at": "2026-08-01T00:00:00+00:00",
            "model": "claude-opus-4-8",
            "prompt_version": "3",
            "dossier_json": '{"period":{"year":2026,"month":7}}',
            "output_json": None,
            "fingerprint": "facts",
            "llm_ok": False,
        }

        loaded = report._load_report_row(row)

        self.assertEqual(loaded["prompt_version"], "3")
        self.assertEqual(loaded["dossier"]["period"]["month"], 7)
        self.assertIsNone(loaded["output"])


if __name__ == "__main__":
    unittest.main()
