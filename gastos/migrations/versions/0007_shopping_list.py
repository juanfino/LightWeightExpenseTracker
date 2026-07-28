"""Add the shared tenant shopping list.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shopping_items",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("family_id", sa.Integer(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("bought_by_user_id", sa.Integer(), nullable=True),
        sa.Column("bought_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'bought')", name="ck_shopping_items_status"),
        sa.UniqueConstraint("family_id", "id", name="uq_shopping_items_family_id_id"),
        sa.ForeignKeyConstraint(
            ["family_id", "category_id"], ["categories.family_id", "categories.id"],
            name="fk_shopping_items_family_category", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["family_id", "created_by_user_id"], ["memberships.family_id", "memberships.user_id"],
            name="fk_shopping_items_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["family_id", "bought_by_user_id"], ["memberships.family_id", "memberships.user_id"],
            name="fk_shopping_items_bought_by",
        ),
    )
    op.create_index("ix_shopping_items_family_status", "shopping_items", ["family_id", "status"])
    op.execute(
        "ALTER TABLE shopping_items ALTER COLUMN family_id "
        "SET DEFAULT NULLIF(current_setting('app.family_id', true), '')::integer"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON shopping_items TO gastos_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gastos_app")
    op.execute("GRANT SELECT ON shopping_items TO gastos_superadmin")
    op.execute("ALTER TABLE shopping_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shopping_items FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY shopping_items_family_isolation ON shopping_items
        USING (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
        WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS shopping_items_family_isolation ON shopping_items")
    op.execute("ALTER TABLE shopping_items DISABLE ROW LEVEL SECURITY")
    op.drop_table("shopping_items")
