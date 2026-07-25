"""Initial PostgreSQL schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Temporary compatibility functions keep the Phase-0 query inventory
    # readable while each call site is moved to native PostgreSQL expressions.
    op.execute("""
        CREATE FUNCTION strftime(pattern text, value timestamptz)
        RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
          SELECT to_char(
            value AT TIME ZONE 'America/Argentina/Buenos_Aires',
            CASE pattern
              WHEN '%Y' THEN 'YYYY' WHEN '%m' THEN 'MM'
              WHEN '%d' THEN 'DD' WHEN '%Y-%m' THEN 'YYYY-MM'
              ELSE pattern
            END
          )
        $$
    """)
    op.execute("""
        CREATE FUNCTION datetime(value timestamptz, modifier text)
        RETURNS timestamptz LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
          SELECT value + modifier::interval
        $$
    """)
    op.execute("""
        CREATE FUNCTION datetime(value text, modifier text)
        RETURNS timestamptz LANGUAGE sql STABLE PARALLEL SAFE AS $$
          SELECT (CASE WHEN value = 'now' THEN now() ELSE value::timestamptz END)
                 + modifier::interval
        $$
    """)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("telegram_id", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False, server_default="#6366f1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("color", sa.Text(), nullable=False, server_default="#6366f1"),
        sa.Column("icon", sa.Text(), nullable=False, server_default="💰"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "subcategories",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
    )
    op.create_table(
        "keywords",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("keyword", sa.Text(), nullable=False, unique=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subcategory_id", sa.Integer(), sa.ForeignKey("subcategories.id")),
    )
    op.create_table(
        "fixed_expenses",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("concept", sa.Text(), nullable=False),
        sa.Column("estimated_amount", sa.Numeric(14, 2)),
        sa.Column("currency", sa.Text(), nullable=False, server_default="ARS"),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="SET NULL")),
        sa.Column("subcategory_id", sa.Integer(), sa.ForeignKey("subcategories.id")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("currency IN ('ARS', 'USD')"),
    )
    op.create_table(
        "expenses",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id")),
        sa.Column("subcategory_id", sa.Integer(), sa.ForeignKey("subcategories.id")),
        sa.Column("concept", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="ARS"),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("fixed_expense_id", sa.Integer(), sa.ForeignKey("fixed_expenses.id", ondelete="SET NULL")),
        sa.Column("fixed_expense_year", sa.Integer()),
        sa.Column("fixed_expense_month", sa.Integer()),
        sa.CheckConstraint("currency IN ('ARS', 'USD')"),
    )
    op.create_table(
        "cambios_dolar",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("fecha", sa.DateTime(timezone=True), nullable=False),
        sa.Column("monto_usd", sa.Numeric(14, 2), nullable=False),
        sa.Column("cotizacion", sa.Numeric(14, 2), nullable=False),
        sa.Column("monto_ars", sa.Numeric(14, 2), nullable=False),
        sa.Column("usuario", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False, server_default="venta"),
    )
    op.create_table(
        "ipc_series",
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Integer(), primary_key=True),
        sa.Column("value", sa.Numeric(20, 12), nullable=False),
        sa.Column("is_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("model", sa.Text()),
        sa.Column("prompt_version", sa.Text()),
        sa.Column("dossier_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text()),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("llm_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "expense_classifications",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expense_id", sa.Integer(), sa.ForeignKey("expenses.id"), nullable=False),
        sa.Column("concept", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="ARS"),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("currency IN ('ARS', 'USD')"),
        sa.CheckConstraint("label IN ('recurring', 'exceptional')"),
    )
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gastos_readonly') THEN
            CREATE ROLE gastos_readonly NOLOGIN;
          END IF;
        END $$
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO gastos_readonly")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO gastos_readonly")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO gastos_readonly")
    op.execute("GRANT gastos_readonly TO CURRENT_USER")


def downgrade() -> None:
    op.execute("REVOKE gastos_readonly FROM CURRENT_USER")
    for table in (
        "expense_classifications", "reports", "ipc_series", "cambios_dolar",
        "expenses", "fixed_expenses", "keywords", "subcategories",
        "categories", "users",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS datetime(text, text)")
    op.execute("DROP FUNCTION IF EXISTS datetime(timestamptz, text)")
    op.execute("DROP FUNCTION IF EXISTS strftime(text, timestamptz)")
    op.execute("DROP ROLE IF EXISTS gastos_readonly")
