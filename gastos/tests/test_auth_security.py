import os
import sys
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
import dashboard  # noqa: E402
import db  # noqa: E402
import pgcompat  # noqa: E402
from support import authenticated_client, reset_database  # noqa: E402


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        reset_database({"100": "Tester"})
        dashboard.app.config.update(TESTING=True)
        self.client = dashboard.app.test_client()

    def test_all_non_public_routes_require_authentication(self):
        public = {
            "/", "/login", "/registro", "/privacy", "/terms",
            "/privacidad", "/terminos",
            "/auth/google", "/auth/google/callback", "/auth/otp/verify",
            "/unirme/<token>",
        }
        for rule in dashboard.app.url_map.iter_rules():
            if rule.rule.startswith("/static/") or rule.rule in public:
                continue
            path = re.sub(r"<int:[^>]+>", "1", rule.rule)
            path = re.sub(r"<(?:path|string):[^>]+>", "test", path)
            path = re.sub(r"<[^>]+>", "test", path)
            method = next((m for m in ("GET", "POST", "PUT", "DELETE") if m in rule.methods), None)
            if not method:
                continue
            with self.subTest(route=rule.rule, method=method):
                response = self.client.open(path, method=method)
                if rule.rule.startswith(("/api/", "/admin/")):
                    self.assertEqual(response.status_code, 401)
                else:
                    self.assertEqual(response.status_code, 302)
                    self.assertIn("/login", response.headers["Location"])

    def test_mutation_rejects_missing_or_bad_csrf(self):
        client, headers = authenticated_client(dashboard)
        self.assertEqual(client.post("/api/categories/add", json={"name": "X"}).status_code, 403)
        self.assertEqual(
            client.post(
                "/api/categories/add", json={"name": "X"},
                headers={"X-CSRF-Token": "incorrecto"},
            ).status_code,
            403,
        )
        self.assertNotEqual(headers["X-CSRF-Token"], "incorrecto")

    def test_session_is_stored_hashed_and_logout_revokes_it(self):
        user = db.get_user_by_telegram_id("100")
        token, csrf = auth.create_session(user["id"], "test", "127.0.0.1")
        with auth.platform_transaction() as raw:
            row = raw.execute(
                "SELECT token_hash, csrf_token FROM sessions WHERE user_id = %s",
                (user["id"],),
            ).fetchone()
        self.assertNotEqual(row[0], token)
        self.assertEqual(row[1], csrf)

        self.client.set_cookie(auth.SESSION_COOKIE, token)
        response = self.client.post("/logout", data={"csrf_token": csrf})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(auth.resolve_session(token))

    def test_otp_is_single_use_and_limited_to_five_attempts(self):
        code = auth.issue_otp("test@example.com", flow="login")
        for _ in range(5):
            self.assertIsNone(auth.consume_otp("test@example.com", "000000" if code != "000000" else "111111"))
        self.assertIsNone(auth.consume_otp("test@example.com", code))

        second = auth.issue_otp("test@example.com", flow="login")
        self.assertIsNotNone(auth.consume_otp("test@example.com", second))
        self.assertIsNone(auth.consume_otp("test@example.com", second))

    def test_email_registration_creates_family_defaults_and_session(self):
        page = self.client.get("/registro")
        csrf = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        sent = {}

        def capture(email, code):
            sent.update(email=email, code=code)

        with (
            patch.object(auth, "verify_turnstile", return_value=True),
            patch.object(auth, "send_otp", side_effect=capture),
        ):
            response = self.client.post(
                "/registro",
                data={
                    "csrf_token": csrf,
                    "name": "Nueva Persona",
                    "family_name": "Familia Nueva",
                    "email": "nueva@example.com",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sent["email"], "nueva@example.com")

        verify_csrf = re.search(
            rb'name="csrf_token" value="([^"]+)"', response.data
        ).group(1).decode()
        response = self.client.post(
            "/auth/otp/verify",
            data={
                "csrf_token": verify_csrf,
                "email": sent["email"],
                "code": sent["code"],
                "next": "/dashboard",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

        with auth.platform_transaction() as raw:
            row = raw.execute(
                """
                SELECT u.id, m.family_id, f.name
                FROM users u
                JOIN memberships m ON m.user_id = u.id
                JOIN families f ON f.id = m.family_id
                WHERE u.email = 'nueva@example.com'
                """
            ).fetchone()
        self.assertEqual(row[2], "Familia Nueva")
        pgcompat.set_family_id(row[1])
        self.assertTrue(db.get_all_categories())

    def test_google_login_always_requests_account_selection(self):
        google = MagicMock()
        google.authorize_redirect.return_value = dashboard.redirect("/google")
        with patch.object(dashboard.oauth, "google", google, create=True):
            response = self.client.get("/auth/google")

        self.assertEqual(response.status_code, 302)
        google.authorize_redirect.assert_called_once_with(
            "http://localhost/auth/google/callback",
            prompt="select_account",
        )


class TurnstileContractTests(unittest.TestCase):
    def test_turnstile_uses_canonical_siteverify_contract(self):
        response = MagicMock()
        response.json.return_value = {"success": True}
        with patch.dict(os.environ, {"TURNSTILE_SECRET": "test-secret"}, clear=False):
            with patch.object(auth.requests, "post", return_value=response) as post:
                self.assertTrue(auth.verify_turnstile("browser-token", "203.0.113.8"))
        post.assert_called_once_with(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": "test-secret",
                "response": "browser-token",
                "remoteip": "203.0.113.8",
            },
            timeout=8,
        )
        response.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
