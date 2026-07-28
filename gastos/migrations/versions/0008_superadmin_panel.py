"""Add superadmin observability, costs and per-family quota overrides.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels = None
depends_on = None


def _tenant_policy(table: str) -> None:
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN family_id "
        "SET DEFAULT NULLIF(current_setting('app.family_id', true), '')::integer"
    )
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
        "family_quota_overrides",
        sa.Column(
            "family_id",
            sa.Integer(),
            sa.ForeignKey("families.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("routine_daily_limit", sa.Integer(), nullable=True),
        sa.Column("summary_monthly_limit", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "routine_daily_limit IS NULL OR routine_daily_limit > 0",
            name="ck_family_quota_routine_positive",
        ),
        sa.CheckConstraint(
            "summary_monthly_limit IS NULL OR summary_monthly_limit > 0",
            name="ck_family_quota_summary_positive",
        ),
    )
    op.create_table(
        "system_errors",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column(
            "family_id",
            sa.Integer(),
            sa.ForeignKey("families.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("error_type", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["family_id", "user_id"],
            ["memberships.family_id", "memberships.user_id"],
            name="fk_system_errors_family_member",
        ),
    )
    op.create_index(
        "ix_system_errors_family_created",
        "system_errors",
        ["family_id", "created_at"],
    )
    op.create_table(
        "infrastructure_cost_settings",
        sa.Column("provider", sa.Text(), primary_key=True),
        sa.Column("unit_label", sa.Text(), nullable=False),
        sa.Column("unit_rate_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("monthly_volume", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("unit_rate_usd >= 0", name="ck_infra_cost_rate_nonnegative"),
        sa.CheckConstraint("monthly_volume >= 0", name="ck_infra_cost_volume_nonnegative"),
    )
    op.execute(
        """
        INSERT INTO infrastructure_cost_settings
            (provider, unit_label, unit_rate_usd, monthly_volume, notes)
        VALUES
            ('Anthropic', 'costo medido por API', 0, 0, 'El panel muestra además el costo estimado desde llm_calls.'),
            ('OpenAI', 'costo medido por API', 0, 0, 'El panel muestra además el costo estimado desde llm_calls.'),
            ('Resend', 'emails enviados', 0, 0, NULL),
            ('Cloudflare R2', 'GB-mes + operaciones', 0, 0, NULL)
        """
    )

    for table in ("family_quota_overrides", "system_errors"):
        _tenant_policy(table)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO gastos_app")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO gastos_superadmin")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON infrastructure_cost_settings "
        "TO gastos_superadmin"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
        "TO gastos_app, gastos_superadmin"
    )


def downgrade() -> None:
    for table in ("system_errors", "family_quota_overrides"):
        op.execute(f"DROP POLICY IF EXISTS {table}_family_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("infrastructure_cost_settings")
    op.drop_table("system_errors")
    op.drop_table("family_quota_overrides")
