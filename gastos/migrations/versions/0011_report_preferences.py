"""Add per-family report narrative preferences and report snapshots.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "family_report_preferences",
        sa.Column(
            "family_id",
            sa.Integer(),
            sa.ForeignKey("families.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("emphasis_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("tone", sa.Text(), nullable=False, server_default="neutral"),
        sa.Column("length", sa.Text(), nullable=False, server_default="medium"),
        sa.Column("focus", sa.Text(), nullable=False, server_default=""),
        sa.Column("allow_suggestions", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("tone IN ('neutral', 'warm', 'direct')", name="ck_report_preferences_tone"),
        sa.CheckConstraint("length IN ('short', 'medium', 'long')", name="ck_report_preferences_length"),
        sa.CheckConstraint("char_length(focus) <= 400", name="ck_report_preferences_focus_length"),
        sa.ForeignKeyConstraint(
            ["family_id", "updated_by_user_id"],
            ["memberships.family_id", "memberships.user_id"],
            name="fk_report_preferences_family_member",
        ),
    )
    op.add_column("reports", sa.Column("preferences_json", sa.Text(), nullable=True))
    op.execute(
        "ALTER TABLE family_report_preferences ALTER COLUMN family_id "
        "SET DEFAULT NULLIF(current_setting('app.family_id', true), '')::integer"
    )
    op.execute("ALTER TABLE family_report_preferences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE family_report_preferences FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY family_report_preferences_family_isolation
        ON family_report_preferences
        USING (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
        WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON family_report_preferences "
        "TO gastos_app, gastos_superadmin"
    )


def downgrade() -> None:
    op.drop_column("reports", "preferences_json")
    op.execute(
        "DROP POLICY IF EXISTS family_report_preferences_family_isolation "
        "ON family_report_preferences"
    )
    op.execute("ALTER TABLE family_report_preferences DISABLE ROW LEVEL SECURITY")
    op.drop_table("family_report_preferences")
