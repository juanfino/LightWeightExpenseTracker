"""Allow every catalogue currency in persisted forecasts.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-09
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_forecast_currency", "report_forecasts", type_="check")
    op.create_foreign_key(
        "fk_report_forecasts_currency", "report_forecasts", "currencies",
        ["currency"], ["code"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_report_forecasts_currency", "report_forecasts", type_="foreignkey"
    )
    op.create_check_constraint(
        "ck_forecast_currency", "report_forecasts", "currency IN ('ARS', 'USD')"
    )
