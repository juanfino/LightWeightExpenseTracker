import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

import auth
import db
import llm_limits
import pgcompat
from support import authenticated_client, reset_database


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "requires PostgreSQL")
class Phase5IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
        os.environ.setdefault("AUTH_SECRET_KEY", "test-secret")
        os.environ.setdefault("TELEGRAM_BOT_USERNAME", "test_expenses_bot")
        db.init_db()

    def setUp(self):
        reset_database({"100": "Owner"})
        with auth.platform_transaction() as raw:
            raw.execute("UPDATE users SET email = %s WHERE telegram_id = '100'", ("owner@example.com",))
        self.user = db.get_user_by_telegram_id("100")
        pgcompat.set_family_id(self.user["family_id"])
        pgcompat.set_user_id(self.user["id"])

    def test_link_token_is_single_use_and_status_polls(self):
        auth.unlink_telegram(self.user["id"])
        token = auth.create_telegram_link_token(self.user["id"])
        linked = auth.consume_telegram_link_token(token, "555")
        self.assertEqual(linked["id"], self.user["id"])
        self.assertTrue(auth.telegram_link_status(self.user["id"]))
        with self.assertRaisesRegex(ValueError, "venció|utilizado"):
            auth.consume_telegram_link_token(token, "556")

        import dashboard
        client, _headers = authenticated_client(dashboard, "555")
        self.assertEqual(client.get("/vincular-telegram").status_code, 200)
        self.assertEqual(client.get("/api/telegram-link/status").json, {"connected": True})
        auth.unlink_telegram(self.user["id"])
        page = client.get("/vincular-telegram")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"https://t.me/test_expenses_bot?start=", page.data)
        self.assertIn(b"data:image/png;base64,", page.data)

    def test_link_refuses_telegram_owned_by_another_user(self):
        with auth.platform_transaction() as raw:
            other_id = raw.execute(
                "INSERT INTO users (email, name, telegram_id) VALUES (%s, %s, %s) RETURNING id",
                ("other@example.com", "Other", "999"),
            ).fetchone()[0]
            raw.execute(
                "INSERT INTO memberships (user_id, family_id, role, active) VALUES (%s, 1, 'member', true)",
                (other_id,),
            )
        auth.unlink_telegram(self.user["id"])
        token = auth.create_telegram_link_token(self.user["id"])
        with self.assertRaisesRegex(ValueError, "otra cuenta"):
            auth.consume_telegram_link_token(token, "999")

    def test_routine_quota_counts_family_rows(self):
        for _ in range(llm_limits.ROUTINE_DAILY_LIMIT):
            db.record_llm_call("intent", "test", 0, 0, 0, 1, True)
        with self.assertRaises(llm_limits.QuotaExceeded):
            with llm_limits.routine_call():
                pass

    def test_family_semaphores_do_not_block_each_other(self):
        entered = []
        release = threading.Event()

        def occupy(family_id):
            pgcompat.set_family_id(family_id)
            pgcompat.set_user_id(None)
            with llm_limits.routine_call():
                entered.append(family_id)
                if family_id == 1:
                    release.wait(2)

        with auth.platform_transaction() as raw:
            family_b = raw.execute(
                "INSERT INTO families (name) VALUES ('B') RETURNING id"
            ).fetchone()[0]
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(occupy, 1)
            second = pool.submit(occupy, 1)
            deadline = time.time() + 2
            while entered.count(1) < 2 and time.time() < deadline:
                time.sleep(0.01)
            other = pool.submit(occupy, family_b)
            deadline = time.time() + 1
            while family_b not in entered and time.time() < deadline:
                time.sleep(0.01)
            self.assertIn(family_b, entered)
            release.set()
            first.result()
            second.result()
            other.result()
