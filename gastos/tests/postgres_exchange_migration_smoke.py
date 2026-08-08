"""Destructive 0013 -> 0014 data-preservation check for a scratch database only."""
import os
from decimal import Decimal

from alembic import command
from alembic.config import Config
import psycopg
from psycopg.rows import dict_row


def main():
    database_url = os.environ["DATABASE_URL"]
    project_dir = os.path.dirname(os.path.dirname(__file__))
    cfg = Config(os.path.join(project_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(project_dir, "migrations"))
    # The script is explicitly scratch-only: rebuild the schema so unrelated
    # seeded tenant data cannot make historical downgrades fail on old uniques.
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
    command.upgrade(cfg, "0013")

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.family_id', '1', false)")
        conn.execute(
            "INSERT INTO cambios_dolar"
            " (fecha, monto_usd, cotizacion, monto_ars, usuario, tipo)"
            " VALUES ('2026-08-01', 10.25, 1234.56, 12654.24, 'Venta', 'venta'),"
            " ('2026-08-02', 10.25, 1234.56, 12654.24, 'Compra', 'compra')"
        )
        conn.commit()

    command.upgrade(cfg, "0014")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.family_id', '1', false)")
        rows = conn.execute(
            "SELECT * FROM cambios_dolar ORDER BY fecha"
        ).fetchall()
        columns = {
            row["column_name"] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name='cambios_dolar'"
            ).fetchall()
        }

    assert len(rows) == 2
    assert {"tipo", "monto_usd", "cotizacion", "monto_ars"}.isdisjoint(columns)
    sale, purchase = rows
    assert sale["currency_given"] == "USD" and sale["currency_received"] == "ARS"
    assert sale["amount_given"] == Decimal("10.25")
    assert sale["amount_received"] == Decimal("12654.24")
    assert sale["rate_received_per_given"] == Decimal("1234.560000000000000000")
    assert purchase["currency_given"] == "ARS" and purchase["currency_received"] == "USD"
    assert purchase["amount_given"] == Decimal("12654.24")
    assert purchase["amount_received"] == Decimal("10.25")
    recovered_purchase_rate = (purchase["amount_given"] / purchase["amount_received"]).quantize(Decimal("0.01"))
    assert recovered_purchase_rate == Decimal("1234.56")
    print("postgres_exchange_migration_smoke_ok")


if __name__ == "__main__":
    main()
