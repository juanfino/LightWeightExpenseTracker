"""Add tenant-scoped incomes and their independent taxonomy.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels = None
depends_on = None


def _tenant_table(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_family_isolation ON {table}
        USING (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
        WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
        """
    )


def upgrade() -> None:
    op.create_table(
        "income_categories",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("family_id", sa.Integer(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("icon", sa.Text(), nullable=False, server_default="💵"),
        sa.Column("color", sa.Text(), nullable=False, server_default="#22c55e"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("family_id", "name", name="uq_income_categories_family_name"),
        sa.UniqueConstraint("family_id", "id", name="uq_income_categories_family_id_id"),
    )
    op.create_table(
        "incomes",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("family_id", sa.Integer(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("concept", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="ARS"),
        sa.Column("income_category_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount > 0", name="ck_incomes_amount_positive"),
        sa.CheckConstraint("currency IN ('ARS', 'USD')", name="ck_incomes_currency"),
        sa.ForeignKeyConstraint(
            ["family_id", "user_id"], ["memberships.family_id", "memberships.user_id"],
            name="fk_incomes_family_member",
        ),
        sa.ForeignKeyConstraint(
            ["family_id", "income_category_id"],
            ["income_categories.family_id", "income_categories.id"],
            name="fk_incomes_family_category",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("family_id", "id", name="uq_incomes_family_id_id"),
    )
    op.create_index("ix_income_categories_family_id", "income_categories", ["family_id"])
    op.create_index("ix_incomes_family_date", "incomes", ["family_id", "date"])
    op.execute(
        "ALTER TABLE income_categories ALTER COLUMN family_id "
        "SET DEFAULT NULLIF(current_setting('app.family_id', true), '')::integer"
    )
    op.execute(
        "ALTER TABLE incomes ALTER COLUMN family_id "
        "SET DEFAULT NULLIF(current_setting('app.family_id', true), '')::integer"
    )
    op.execute(
        """
        INSERT INTO income_categories (family_id, name, icon, color)
        SELECT f.id, seed.name, seed.icon, seed.color
        FROM families f
        CROSS JOIN (VALUES
          ('Sueldo', '💼', '#22c55e'),
          ('Freelance / Honorarios', '🧑‍💻', '#06b6d4'),
          ('Alquiler', '🏠', '#f59e0b'),
          ('Venta', '🏷️', '#8b5cf6'),
          ('Reintegro', '↩️', '#3b82f6'),
          ('Intereses / Inversiones', '📈', '#14b8a6'),
          ('Regalo', '🎁', '#ec4899'),
          ('Otros', '💵', '#6b7280')
        ) AS seed(name, icon, color)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON income_categories, incomes TO gastos_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gastos_app")
    op.execute("GRANT SELECT ON income_categories, incomes TO gastos_superadmin")
    _tenant_table("income_categories")
    _tenant_table("incomes")


def downgrade() -> None:
    for table in ("incomes", "income_categories"):
        op.execute(f"DROP POLICY IF EXISTS {table}_family_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("incomes")
    op.drop_table("income_categories")
