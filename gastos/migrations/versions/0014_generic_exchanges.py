"""Generalize dollar-specific operations into directional exchanges.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cambios_dolar", sa.Column("amount_given", sa.Numeric(14, 2)))
    op.add_column("cambios_dolar", sa.Column("currency_given", sa.Text()))
    op.add_column("cambios_dolar", sa.Column("amount_received", sa.Numeric(14, 2)))
    op.add_column("cambios_dolar", sa.Column("currency_received", sa.Text()))
    op.add_column("cambios_dolar", sa.Column("rate_received_per_given", sa.Numeric(30, 18)))

    op.execute(
        """
        UPDATE cambios_dolar
        SET amount_given = CASE WHEN tipo = 'compra' THEN monto_ars ELSE monto_usd END,
            currency_given = CASE WHEN tipo = 'compra' THEN 'ARS' ELSE 'USD' END,
            amount_received = CASE WHEN tipo = 'compra' THEN monto_usd ELSE monto_ars END,
            currency_received = CASE WHEN tipo = 'compra' THEN 'USD' ELSE 'ARS' END,
            rate_received_per_given = CASE
                WHEN tipo = 'compra' THEN monto_usd / NULLIF(monto_ars, 0)
                ELSE cotizacion
            END
        """
    )
    for column in (
        "amount_given", "currency_given", "amount_received", "currency_received",
        "rate_received_per_given",
    ):
        op.alter_column("cambios_dolar", column, nullable=False)
    op.create_foreign_key(
        "fk_cambios_currency_given", "cambios_dolar", "currencies", ["currency_given"], ["code"]
    )
    op.create_foreign_key(
        "fk_cambios_currency_received", "cambios_dolar", "currencies", ["currency_received"], ["code"]
    )
    op.create_check_constraint(
        "ck_cambios_distinct_currencies", "cambios_dolar", "currency_given <> currency_received"
    )
    op.create_check_constraint(
        "ck_cambios_positive_amounts", "cambios_dolar",
        "amount_given > 0 AND amount_received > 0 AND rate_received_per_given > 0",
    )
    for column in ("tipo", "monto_usd", "cotizacion", "monto_ars"):
        op.drop_column("cambios_dolar", column)


def downgrade() -> None:
    op.add_column("cambios_dolar", sa.Column("monto_usd", sa.Numeric(14, 2)))
    op.add_column("cambios_dolar", sa.Column("cotizacion", sa.Numeric(14, 2)))
    op.add_column("cambios_dolar", sa.Column("monto_ars", sa.Numeric(14, 2)))
    op.add_column("cambios_dolar", sa.Column("tipo", sa.Text(), server_default="venta"))
    op.execute(
        """
        UPDATE cambios_dolar SET
          tipo = CASE WHEN currency_given = 'ARS' AND currency_received = 'USD' THEN 'compra' ELSE 'venta' END,
          monto_usd = CASE WHEN currency_given = 'USD' THEN amount_given ELSE amount_received END,
          monto_ars = CASE WHEN currency_given = 'ARS' THEN amount_given ELSE amount_received END,
          cotizacion = CASE WHEN currency_given = 'USD' THEN rate_received_per_given
                            ELSE 1 / rate_received_per_given END
        WHERE (currency_given = 'USD' AND currency_received = 'ARS')
           OR (currency_given = 'ARS' AND currency_received = 'USD')
        """
    )
    op.execute("DELETE FROM cambios_dolar WHERE monto_usd IS NULL")
    for column in ("monto_usd", "cotizacion", "monto_ars", "tipo"):
        op.alter_column("cambios_dolar", column, nullable=False)
    op.drop_constraint("ck_cambios_positive_amounts", "cambios_dolar", type_="check")
    op.drop_constraint("ck_cambios_distinct_currencies", "cambios_dolar", type_="check")
    op.drop_constraint("fk_cambios_currency_received", "cambios_dolar", type_="foreignkey")
    op.drop_constraint("fk_cambios_currency_given", "cambios_dolar", type_="foreignkey")
    for column in (
        "rate_received_per_given", "currency_received", "amount_received",
        "currency_given", "amount_given",
    ):
        op.drop_column("cambios_dolar", column)
