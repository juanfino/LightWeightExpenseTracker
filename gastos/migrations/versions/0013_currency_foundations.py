"""Add global currency reference data and family defaults.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels = None
depends_on = None


_CURRENCY_TABLES = (
    ("expenses", "expenses_currency_check", "fk_expenses_currency"),
    ("fixed_expenses", "fixed_expenses_currency_check", "fk_fixed_expenses_currency"),
    ("incomes", "ck_incomes_currency", "fk_incomes_currency"),
    (
        "expense_classifications",
        "expense_classifications_currency_check",
        "fk_expense_classifications_currency",
    ),
)


def upgrade() -> None:
    op.create_table(
        "currencies",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("decimal_places", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("code = upper(code)", name="ck_currencies_uppercase_code"),
        sa.CheckConstraint(
            "decimal_places BETWEEN 0 AND 6",
            name="ck_currencies_decimal_places",
        ),
    )
    op.bulk_insert(
        sa.table(
            "currencies",
            sa.column("code", sa.Text()),
            sa.column("symbol", sa.Text()),
            sa.column("decimal_places", sa.SmallInteger()),
        ),
        [
            {"code": "ARS", "symbol": "$", "decimal_places": 2},
            {"code": "USD", "symbol": "US$", "decimal_places": 2},
            {"code": "BRL", "symbol": "R$", "decimal_places": 2},
            {"code": "EUR", "symbol": "€", "decimal_places": 2},
        ],
    )

    for table, check_name, fk_name in _CURRENCY_TABLES:
        op.drop_constraint(check_name, table, type_="check")
        op.create_foreign_key(
            fk_name, table, "currencies", ["currency"], ["code"]
        )

    op.drop_constraint("families_currency_check", "families", type_="check")
    op.alter_column("families", "currency", new_column_name="default_currency")
    op.create_foreign_key(
        "fk_families_default_currency",
        "families",
        "currencies",
        ["default_currency"],
        ["code"],
    )
    op.execute("GRANT SELECT ON currencies TO gastos_app, gastos_superadmin")


def downgrade() -> None:
    op.drop_constraint(
        "fk_families_default_currency", "families", type_="foreignkey"
    )
    op.alter_column("families", "default_currency", new_column_name="currency")
    op.create_check_constraint(
        "families_currency_check", "families", "currency IN ('ARS', 'USD')"
    )

    for table, check_name, fk_name in reversed(_CURRENCY_TABLES):
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.create_check_constraint(check_name, table, "currency IN ('ARS', 'USD')")

    op.drop_table("currencies")
