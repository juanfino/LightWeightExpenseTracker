import io
import sys
import unittest
import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import export_data  # noqa: E402
import pgcompat  # noqa: E402
from support import authenticated_client, reset_database  # noqa: E402


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import dashboard
        cls.dashboard = dashboard

    def setUp(self):
        reset_database({"100": "Juampi"})
        self.user = db.get_user_by_telegram_id("100")
        db.create_expense(self.user["id"], None, "=PELIGRO, ñ", 1234.5, "x")
        db.create_income(self.user["id"], "Sueldo", 3000, "USD", "2026-07-01")
        db.add_shopping_item(self.user["id"], "Detergente", "2")

    def test_csv_is_excel_safe_rfc4180_and_complete(self):
        files = export_data.datasets("America/Argentina/Buenos_Aires")
        self.assertEqual(
            set(files),
            {"movimientos", "gastos_fijos", "dolares", "ingresos", "lista_compras", "taxonomia"},
        )
        movements = files["movimientos"]
        self.assertTrue(movements.startswith(export_data.UTF8_BOM))
        self.assertIn(b"\r\n", movements)
        text = movements.decode("utf-8-sig")
        self.assertIn("'=PELIGRO, ñ", text)
        self.assertIn('"\'=PELIGRO, ñ"', text)

    def test_zip_contains_every_csv(self):
        archive = zipfile.ZipFile(io.BytesIO(export_data.zip_bytes(export_data.datasets("UTC"))))
        self.assertEqual(
            set(archive.namelist()),
            {"movimientos.csv", "gastos_fijos.csv", "dolares.csv", "ingresos.csv",
             "lista_compras.csv", "taxonomia.csv"},
        )

    def test_authenticated_download_endpoints(self):
        client, _ = authenticated_client(self.dashboard, "100")
        response = client.get("/exportar/movimientos.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[:3], export_data.UTF8_BOM)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        response = client.get("/exportar/todo.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[:2], b"PK")

    def test_export_never_contains_another_family(self):
        with pgcompat.current_pool().connection() as raw:
            family_b = raw.execute("INSERT INTO families (name) VALUES ('B') RETURNING id").fetchone()[0]
            user_b = raw.execute(
                "INSERT INTO users (telegram_id, name) VALUES ('200', 'B') RETURNING id"
            ).fetchone()[0]
            raw.execute(
                "INSERT INTO memberships (user_id, family_id, role) VALUES (%s, %s, 'owner')",
                (user_b, family_b),
            )
            raw.commit()
        pgcompat.set_family_id(family_b)
        import seed
        with db.get_conn() as conn:
            seed.create_family_defaults(conn, family_b)
        db.create_expense(user_b, None, "SECRETO B", 99, "x")
        db.create_income(user_b, "INGRESO B", 99, "ARS", "2026-07-01")
        db.add_shopping_item(user_b, "LISTA B")
        pgcompat.set_family_id(1)
        combined = b"".join(export_data.datasets("UTC").values())
        self.assertNotIn(b"SECRETO B", combined)
        self.assertNotIn(b"INGRESO B", combined)
        self.assertNotIn(b"LISTA B", combined)


if __name__ == "__main__":
    unittest.main()
