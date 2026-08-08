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

    def test_email_identity_then_onboarding_creates_family_defaults_and_session(self):
        page = self.client.get("/login")
        csrf = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        sent = {}

        def capture(email, code):
            sent.update(email=email, code=code)

        with (
            patch.object(auth, "verify_turnstile", return_value=True),
            patch.object(auth, "send_otp", side_effect=capture),
        ):
            response = self.client.post(
                "/login",
                data={"csrf_token": csrf, "email": "nueva@example.com"},
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
        # A brand-new identity has no membership yet — it lands on onboarding,
        # not straight on the requested `next`.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/onboarding")

        onboarding_page = self.client.get("/onboarding")
        self.assertEqual(onboarding_page.status_code, 200)
        self.assertIn("Creá tu espacio familiar".encode(), onboarding_page.data)
        onboarding_csrf = re.search(
            rb'name="csrf_token" value="([^"]+)"', onboarding_page.data
        ).group(1).decode()
        response = self.client.post(
            "/onboarding",
            data={
                "csrf_token": onboarding_csrf,
                "action": "create",
                "name": "Nueva Persona",
                "family_name": "Familia Nueva",
            },
        )
        # The original `next` from the identity step survives onboarding.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        dashboard_page = self.client.get("/dashboard")
        self.assertEqual(dashboard_page.status_code, 200)
        self.assertIn(b"Primeros pasos", dashboard_page.data)
        self.assertIn(b"Cargar tu primer gasto", dashboard_page.data)
        self.assertIn(b"Conectar Telegram", dashboard_page.data)
        self.assertIn(b"Invitar a alguien", dashboard_page.data)

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

    def test_login_screen_responds_identically_to_known_and_unknown_emails(self):
        auth.create_user_without_family("conocido@example.com", "Conocido")
        csrf = re.search(
            rb'name="csrf_token" value="([^"]+)"', self.client.get("/login").data
        ).group(1).decode()
        with (
            patch.object(auth, "verify_turnstile", return_value=True),
            patch.object(auth, "send_otp", return_value=None),
        ):
            known = self.client.post(
                "/login", data={"csrf_token": csrf, "email": "conocido@example.com"},
            )
            unknown = self.client.post(
                "/login", data={"csrf_token": csrf, "email": "jamas-registrado@example.com"},
            )
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertNotIn(b"No existe una cuenta", known.data)
        self.assertNotIn(b"No existe una cuenta", unknown.data)

    def test_family_less_session_persists_and_resolves_to_onboarding(self):
        user_id = auth.create_user_without_family("huerfano@example.com", "")
        token, _csrf = auth.create_session(user_id, "test", "127.0.0.1")
        client = dashboard.app.test_client()
        client.set_cookie(auth.SESSION_COOKIE, token)

        # Regardless of which URL they aimed at...
        for path in ("/dashboard", "/login", "/registro", "/"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], "/onboarding")

        self.assertEqual(client.get("/api/summary").status_code, 401)

        # ...and it survives being read again later (closed tab, same cookie).
        onboarding_page = client.get("/onboarding")
        self.assertEqual(onboarding_page.status_code, 200)

    def test_member_with_family_is_redirected_away_from_onboarding(self):
        client, _headers = authenticated_client(dashboard)
        response = client.get("/onboarding")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")

    def test_invited_user_via_google_joins_the_intended_family_without_a_family_field(self):
        owner = db.get_user_by_telegram_id("100")
        token, _created = auth.create_invitation(1, owner["id"])

        interstitial = self.client.get(f"/unirme/{token}")
        self.assertEqual(interstitial.status_code, 200)
        self.assertIn(b"Continuar", interstitial.data)

        google = MagicMock()
        google.authorize_access_token.return_value = {}
        google.get.return_value.json.return_value = {
            "email": "invitado@example.com", "email_verified": True,
            "name": "Invitada Google", "sub": "google-sub-invite-1",
        }
        with patch.object(dashboard.oauth, "google", google, create=True):
            response = self.client.get("/auth/google/callback")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/onboarding")

        onboarding_page = self.client.get("/onboarding")
        self.assertEqual(onboarding_page.status_code, 200)
        self.assertIn(b"Invitada Google", onboarding_page.data)
        self.assertNotIn(b'name="family_name"', onboarding_page.data)
        onboarding_csrf = re.search(
            rb'name="csrf_token" value="([^"]+)"', onboarding_page.data
        ).group(1).decode()
        response = self.client.post(
            "/onboarding",
            data={"csrf_token": onboarding_csrf, "action": "join", "name": "Invitada Google"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")

        with auth.platform_transaction() as raw:
            row = raw.execute(
                """
                SELECT m.family_id, m.role, m.active
                FROM users u JOIN memberships m ON m.user_id = u.id
                WHERE u.email = 'invitado@example.com'
                """
            ).fetchone()
        self.assertEqual(tuple(row), (1, "member", True))

    def test_onboarding_card_disappears_when_every_step_is_complete(self):
        client, _headers = authenticated_client(dashboard)
        family = {
            "id": 1,
            "name": "Familia de prueba",
            "members": [
                {"active": True},
                {"active": True},
            ],
        }
        with (
            patch.object(db, "has_expenses", return_value=True),
            patch.object(auth, "telegram_link_status", return_value=True),
            patch.object(auth, "get_family_management", return_value=family),
            patch.object(auth, "list_family_invitations", return_value=[]),
        ):
            page = client.get("/dashboard")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"Primeros pasos", page.data)

    def test_public_landing_explains_the_first_expense_path(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("gasté quince mil en el súper".encode(), page.data)
        self.assertIn(b"Supermercado", page.data)
        self.assertIn("cobré el sueldo, 800 lucas".encode(), page.data)
        self.assertIn(b"Crear mi cuenta", page.data)

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
