import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from support import authenticated_client, reset_database  # noqa: E402


class ShoppingListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import dashboard
        cls.dashboard = dashboard

    def setUp(self):
        reset_database({"100": "Juampi", "200": "Cele"})
        self.user = db.get_user_by_telegram_id("100")
        self.other = db.get_user_by_telegram_id("200")

    def test_shared_crud_deduplicates_and_updates_quantity(self):
        item_id, created = db.add_shopping_item(self.user["id"], "Detergente", "1")
        self.assertTrue(created)
        duplicate_id, created = db.add_shopping_item(self.other["id"], "detérgente", "2 botellas")
        self.assertFalse(created)
        self.assertEqual(duplicate_id, item_id)
        self.assertEqual(db.get_shopping_item(item_id)["quantity"], "2 botellas")
        self.assertTrue(db.mark_shopping_item_bought(item_id, self.other["id"]))
        self.assertEqual(db.shopping_pending_count(), 0)
        self.assertTrue(db.readd_shopping_item(item_id, self.user["id"]))

    def test_web_list_is_shared(self):
        client, headers = authenticated_client(self.dashboard, "100")
        response = client.post("/api/shopping-items", json={"name": "Bananas", "quantity": "1 kg"}, headers=headers)
        self.assertEqual(response.status_code, 201)
        item_id = response.get_json()["id"]
        other_client, other_headers = authenticated_client(self.dashboard, "200")
        response = other_client.post(f"/api/shopping-items/{item_id}/bought", headers=other_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.get_shopping_item(item_id)["bought_by_user_id"], self.other["id"])


if __name__ == "__main__":
    unittest.main()
