"""Persist immutable next-month forecasts per report and currency.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_forecasts",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column(
            "family_id", sa.Integer(),
            sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("source_year", sa.Integer(), nullable=False),
        sa.Column("source_month", sa.Integer(), nullable=False),
        sa.Column("target_year", sa.Integer(), nullable=False),
        sa.Column("target_month", sa.Integer(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("method_id", sa.Text(), nullable=False),
        sa.Column("forecast_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("source_month BETWEEN 1 AND 12", name="ck_forecast_source_month"),
        sa.CheckConstraint("target_month BETWEEN 1 AND 12", name="ck_forecast_target_month"),
        sa.CheckConstraint("currency IN ('ARS', 'USD')", name="ck_forecast_currency"),
        sa.UniqueConstraint("family_id", "report_id", "currency", name="uq_forecast_report_currency"),
        sa.ForeignKeyConstraint(
            ["family_id", "report_id"], ["reports.family_id", "reports.id"],
            name="fk_forecast_family_report", ondelete="CASCADE",
        ),
    )
    op.execute(
        "ALTER TABLE report_forecasts ALTER COLUMN family_id "
        "SET DEFAULT NULLIF(current_setting('app.family_id', true), '')::integer"
    )
    op.create_index("ix_report_forecasts_family_target", "report_forecasts", ["family_id", "target_year", "target_month"])
    op.execute("ALTER TABLE report_forecasts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE report_forecasts FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY report_forecasts_family_isolation ON report_forecasts
        USING (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
        WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
        """
    )
    op.execute("GRANT SELECT, INSERT ON report_forecasts TO gastos_app")
    op.execute("GRANT SELECT ON report_forecasts TO gastos_superadmin")
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE report_forecasts_id_seq TO gastos_app, gastos_superadmin"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS report_forecasts_family_isolation ON report_forecasts")
    op.execute("ALTER TABLE report_forecasts DISABLE ROW LEVEL SECURITY")
    op.drop_table("report_forecasts")
