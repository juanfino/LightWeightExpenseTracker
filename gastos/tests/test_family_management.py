import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
import dashboard  # noqa: E402
import db  # noqa: E402
import pgcompat  # noqa: E402
from support import authenticated_client, reset_database  # noqa: E402


class FamilyManagementTests(unittest.TestCase):
    def setUp(self):
        reset_database({"100": "Owner", "200": "Cele"})
        dashboard.app.config.update(TESTING=True)
        self.owner = db.get_user_by_telegram_id("100")
        self.cele = db.get_user_by_telegram_id("200")

    def _new_user(self, email="invitee@example.com", name="Invitada"):
        return auth.create_user_without_family(email, name)

    def test_invitation_is_member_only_single_use_and_rejects_active_member(self):
        token, created = auth.create_invitation(1, self.owner["id"])
        invitation = auth.get_invitation(token)
        self.assertTrue(invitation["valid"])
        user_id = self._new_user()
        self.assertEqual(auth.accept_invitation(created["id"], user_id), 1)

        with auth.platform_transaction() as raw:
            membership = raw.execute(
                "SELECT role, active FROM memberships WHERE user_id = %s", (user_id,)
            ).fetchone()
        self.assertEqual(tuple(membership), ("member", True))
        with self.assertRaises(auth.InvitationError):
            auth.accept_invitation(created["id"], self._new_user("other@example.com"))

        _token2, created2 = auth.create_invitation(1, self.owner["id"])
        with self.assertRaises(auth.MembershipConflict):
            auth.accept_invitation(created2["id"], self.cele["id"])

    def test_expired_and_revoked_invitations_cannot_be_used(self):
        token, created = auth.create_invitation(1, self.owner["id"])
        self.assertTrue(auth.revoke_invitation(created["id"], 1))
        self.assertFalse(auth.get_invitation(token)["valid"])
        with self.assertRaises(auth.InvitationError):
            auth.accept_invitation(created["id"], self._new_user())

        _token2, created2 = auth.create_invitation(1, self.owner["id"])
        with auth.platform_transaction() as raw:
            raw.execute(
                "UPDATE invitations SET expires_at = %s WHERE id = %s",
                (datetime.now(timezone.utc) - timedelta(seconds=1), created2["id"]),
            )
        with self.assertRaises(auth.InvitationError):
            auth.accept_invitation(created2["id"], self._new_user("expired@example.com"))

    def test_email_invitation_can_be_completed_entirely_through_ui(self):
        token, _created = auth.create_invitation(1, self.owner["id"])
        client = dashboard.app.test_client()
        interstitial = client.get(f"/unirme/{token}")
        self.assertEqual(interstitial.status_code, 200)
        self.assertIn(b"Continuar", interstitial.data)

        login_csrf = re.search(
            rb'name="csrf_token" value="([^"]+)"', client.get("/login").data
        ).group(1).decode()
        sent = {}

        def capture(email, code):
            sent.update(email=email, code=code)

        with (
            patch.object(auth, "verify_turnstile", return_value=True),
            patch.object(auth, "send_otp", side_effect=capture),
        ):
            response = client.post(
                "/login",
                data={"csrf_token": login_csrf, "email": "ui-invite@example.com"},
            )
        self.assertEqual(response.status_code, 200)
        verify_csrf = re.search(
            rb'name="csrf_token" value="([^"]+)"', response.data
        ).group(1).decode()
        response = client.post(
            "/auth/otp/verify",
            data={
                "csrf_token": verify_csrf,
                "email": sent["email"],
                "code": sent["code"],
                "next": "/dashboard",
            },
        )
        # A pending invitation, still no membership — lands on onboarding.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/onboarding")

        onboarding_page = client.get("/onboarding")
        self.assertEqual(onboarding_page.status_code, 200)
        self.assertNotIn(b'name="family_name"', onboarding_page.data)
        onboarding_csrf = re.search(
            rb'name="csrf_token" value="([^"]+)"', onboarding_page.data
        ).group(1).decode()
        response = client.post(
            "/onboarding",
            data={"csrf_token": onboarding_csrf, "action": "join", "name": "Persona invitada"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertEqual(client.get("/familia").status_code, 200)
        with auth.platform_transaction() as raw:
            row = raw.execute(
                """
                SELECT m.role, m.active
                FROM users u JOIN memberships m ON m.user_id = u.id
                WHERE u.email = 'ui-invite@example.com'
                """
            ).fetchone()
        self.assertEqual(tuple(row), ("member", True))

    def test_logical_removal_preserves_expenses_and_allows_a_new_family(self):
        pgcompat.set_family_id(1)
        expense_id = db.create_expense(
            self.cele["id"], None, "Histórico Cele", 123, "Histórico Cele 123"
        )
        auth.remove_family_member(1, self.cele["id"])
        self.assertIsNone(db.get_user_by_telegram_id("200"))
        db._sync_users({"100": "Owner", "200": "Cele"})
        self.assertIsNone(db.get_user_by_telegram_id("200"))

        with auth.platform_transaction() as raw:
            membership = raw.execute(
                "SELECT active, deactivated_at FROM memberships WHERE user_id = %s",
                (self.cele["id"],),
            ).fetchone()
            expense = raw.execute(
                "SELECT user_id FROM expenses WHERE id = %s", (expense_id,)
            ).fetchone()
        self.assertFalse(membership[0])
        self.assertIsNotNone(membership[1])
        self.assertEqual(expense[0], self.cele["id"])

        family_id = auth.create_family_for_existing_user(
            self.cele["id"], "Nueva familia de Cele"
        )
        with auth.platform_transaction() as raw:
            active = raw.execute(
                "SELECT family_id, role FROM memberships WHERE user_id = %s AND active",
                (self.cele["id"],),
            ).fetchone()
        self.assertEqual(tuple(active), (family_id, "owner"))

    def test_ownership_transfer_is_atomic_and_owner_cannot_be_removed(self):
        with self.assertRaises(ValueError):
            auth.remove_family_member(1, self.owner["id"])
        auth.transfer_family_ownership(1, self.owner["id"], self.cele["id"])
        with auth.platform_transaction() as raw:
            rows = raw.execute(
                "SELECT user_id, role FROM memberships WHERE family_id = 1 AND active"
            ).fetchall()
        roles = {row[0]: row[1] for row in rows}
        self.assertEqual(roles[self.owner["id"]], "member")
        self.assertEqual(roles[self.cele["id"]], "owner")
        self.assertEqual(list(roles.values()).count("owner"), 1)

    def test_family_delete_requires_exact_name(self):
        with self.assertRaises(ValueError):
            auth.delete_family(1, self.owner["id"], "familia de prueba")
        with auth.platform_transaction() as raw:
            raw.execute(
                "UPDATE users SET email = 'owner@example.com' WHERE id = %s",
                (self.owner["id"],),
            )
        auth.delete_family(1, self.owner["id"], "Familia de prueba")
        with auth.platform_transaction() as raw:
            self.assertIsNone(
                raw.execute("SELECT id FROM families WHERE id = 1").fetchone()
            )
        with patch.dict(
            os.environ, {"AUTH_BOOTSTRAP_EMAIL": "owner@example.com"}, clear=False
        ):
            db.init_db({"100": "Owner", "200": "Cele"})

    def test_legacy_email_sync_is_null_only_and_superadmin_is_env_only(self):
        db._sync_user_emails({"200": "cele@example.com"})
        with auth.platform_transaction() as raw:
            email = raw.execute(
                "SELECT email FROM users WHERE id = %s", (self.cele["id"],)
            ).fetchone()[0]
        self.assertEqual(email, "cele@example.com")
        with self.assertRaises(RuntimeError):
            db._sync_user_emails({"200": "different@example.com"})

        with auth.platform_transaction() as raw:
            raw.execute(
                "UPDATE users SET email = 'owner@example.com' WHERE id = %s",
                (self.owner["id"],),
            )
        db._bootstrap_superadmin("owner@example.com")
        client, headers = authenticated_client(dashboard, "100")
        response = client.post(
            "/familia",
            data={
                "csrf_token": headers["X-CSRF-Token"],
                "action": "rename",
                "name": "Nuevo nombre",
                "is_superadmin": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        with auth.platform_transaction() as raw:
            flag = raw.execute(
                "SELECT is_superadmin FROM users WHERE id = %s", (self.owner["id"],)
            ).fetchone()[0]
        self.assertTrue(flag)

    def test_only_owner_can_mutate_family(self):
        client, headers = authenticated_client(dashboard, "200")
        response = client.post(
            "/familia",
            data={
                "csrf_token": headers["X-CSRF-Token"],
                "action": "rename",
                "name": "No permitido",
            },
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
