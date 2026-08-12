import re
import shutil
import subprocess
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
TEMPLATES_DIR = APP_DIR / "templates"
DIALOGS_JS = APP_DIR / "static" / "dialogs.js"


class CustomDialogTests(unittest.TestCase):
    def test_no_browser_native_dialog_calls_remain(self):
        native_dialog = re.compile(r"(?<![.\w])(alert|confirm|prompt)\s*\(")
        offenders = []
        for path in [*TEMPLATES_DIR.glob("*.html"), *(APP_DIR / "static").glob("*.js")]:
            for line_number, line in enumerate(path.read_text().splitlines(), 1):
                if native_dialog.search(line):
                    offenders.append(f"{path.name}:{line_number}")

        self.assertEqual(offenders, [])

    def test_base_loads_one_shared_accessible_dialog(self):
        base = (TEMPLATES_DIR / "base.html").read_text()
        self.assertEqual(base.count('id="app-dialog"'), 1)
        self.assertIn('aria-labelledby="app-dialog-title"', base)
        self.assertIn('aria-describedby="app-dialog-message"', base)
        self.assertIn("filename='dialogs.js'", base)

    def test_family_confirmations_use_shared_form_hook(self):
        family = (TEMPLATES_DIR / "family.html").read_text()
        self.assertEqual(family.count("data-confirm-message="), 4)
        self.assertNotIn("onclick=\"return confirm", family)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JavaScript syntax checks")
    def test_dialog_javascript_has_valid_syntax(self):
        subprocess.run(["node", "--check", str(DIALOGS_JS)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
