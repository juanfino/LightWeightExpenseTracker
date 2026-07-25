"""Add family tenancy, RLS and LLM usage accounting.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels = None
depends_on = None

DOMAIN_TABLES = (
    "categories",
    "subcategories",
    "keywords",
    "expenses",
    "fixed_expenses",
    "cambios_dolar",
    "ipc_series",
    "reports",
    "expense_classifications",
    "llm_calls",
)


def upgrade() -> None:
    op.create_table(
        "families",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "timezone",
            sa.Text(),
            nullable=False,
            server_default="America/Argentina/Buenos_Aires",
        ),
        sa.Column("currency", sa.Text(), nullable=False, server_default="ARS"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("currency IN ('ARS', 'USD')"),
    )

    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_users_email", "users", ["email"])

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("family_id", sa.Integer(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('owner', 'member')"),
        sa.UniqueConstraint("user_id", name="uq_memberships_user_id"),
    )
    op.create_foreign_key(
        "fk_families_created_by_user",
        "families",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # The existing household is the deterministic bootstrap tenant.
    op.execute(
        """
        INSERT INTO families (id, name)
        OVERRIDING SYSTEM VALUE
        VALUES (1, 'Familia Finochietto')
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute("SELECT setval(pg_get_serial_sequence('families', 'id'), GREATEST(1, (SELECT max(id) FROM families)))")
    op.execute(
        """
        INSERT INTO memberships (user_id, family_id, role)
        SELECT id, 1, CASE WHEN row_number() OVER (ORDER BY id) = 1 THEN 'owner' ELSE 'member' END
        FROM users
        ON CONFLICT (user_id) DO NOTHING
        """
    )
    op.execute(
        "UPDATE families SET created_by_user_id = (SELECT min(user_id) FROM memberships WHERE family_id = 1) WHERE id = 1"
    )

    for table in DOMAIN_TABLES[:-1]:
        op.add_column(
            table,
            sa.Column("family_id", sa.Integer(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=True),
        )
        op.execute(f"UPDATE {table} SET family_id = 1 WHERE family_id IS NULL")
        op.alter_column(table, "family_id", nullable=False)
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN family_id "
            "SET DEFAULT NULLIF(current_setting('app.family_id', true), '')::integer"
        )
        op.create_index(f"ix_{table}_family_id", table, ["family_id"])

    # Natural keys are tenant-local, not global.
    op.drop_constraint("categories_name_key", "categories", type_="unique")
    op.create_unique_constraint("uq_categories_family_name", "categories", ["family_id", "name"])
    op.drop_constraint("keywords_keyword_key", "keywords", type_="unique")
    op.create_unique_constraint("uq_keywords_family_keyword", "keywords", ["family_id", "keyword"])
    op.create_unique_constraint(
        "uq_subcategories_family_category_name",
        "subcategories",
        ["family_id", "category_id", "name"],
    )
    op.drop_constraint("ipc_series_pkey", "ipc_series", type_="primary")
    op.create_primary_key("pk_ipc_series", "ipc_series", ["family_id", "year", "month"])

    # Composite tenant keys prevent cross-family references even when a caller
    # submits a valid id belonging to another family.
    for table in (
        "categories", "subcategories", "fixed_expenses", "expenses", "reports",
    ):
        op.create_unique_constraint(f"uq_{table}_family_id_id", table, ["family_id", "id"])
    op.create_unique_constraint(
        "uq_memberships_family_user", "memberships", ["family_id", "user_id"]
    )
    op.create_foreign_key(
        "fk_subcategories_family_category",
        "subcategories", "categories",
        ["family_id", "category_id"], ["family_id", "id"], ondelete="CASCADE",
    )
    for table in ("keywords", "fixed_expenses", "expenses"):
        op.create_foreign_key(
            f"fk_{table}_family_category",
            table, "categories",
            ["family_id", "category_id"], ["family_id", "id"],
        )
    for table in ("keywords", "fixed_expenses", "expenses"):
        op.create_foreign_key(
            f"fk_{table}_family_subcategory",
            table, "subcategories",
            ["family_id", "subcategory_id"], ["family_id", "id"],
        )
    op.create_foreign_key(
        "fk_expenses_family_fixed",
        "expenses", "fixed_expenses",
        ["family_id", "fixed_expense_id"], ["family_id", "id"],
    )
    op.create_foreign_key(
        "fk_expenses_family_member",
        "expenses", "memberships",
        ["family_id", "user_id"], ["family_id", "user_id"],
    )
    op.create_foreign_key(
        "fk_classifications_family_report",
        "expense_classifications", "reports",
        ["family_id", "report_id"], ["family_id", "id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_classifications_family_expense",
        "expense_classifications", "expenses",
        ["family_id", "expense_id"], ["family_id", "id"],
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("family_id", sa.Integer(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd_estimate", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
    )
    op.create_index("ix_llm_calls_family_created", "llm_calls", ["family_id", "created_at"])
    op.create_foreign_key(
        "fk_llm_calls_family_member",
        "llm_calls", "memberships",
        ["family_id", "user_id"], ["family_id", "user_id"],
    )

    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gastos_app') THEN
            CREATE ROLE gastos_app NOLOGIN NOBYPASSRLS;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gastos_superadmin') THEN
            CREATE ROLE gastos_superadmin NOLOGIN BYPASSRLS;
          END IF;
        END $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO gastos_app, gastos_superadmin")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gastos_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gastos_app")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO gastos_superadmin")
    op.execute("GRANT INSERT, UPDATE ON users, memberships, families TO gastos_superadmin")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gastos_superadmin")
    op.execute("GRANT gastos_app, gastos_superadmin TO CURRENT_USER")
    for table in DOMAIN_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_family_isolation ON {table}
            USING (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
            WITH CHECK (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
            """
        )
    op.execute("ALTER TABLE families ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE families FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY families_current ON families
        USING (id = NULLIF(current_setting('app.family_id', true), '')::integer)
        """
    )
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memberships_current ON memberships
        USING (family_id = NULLIF(current_setting('app.family_id', true), '')::integer)
        """
    )
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY users_current ON users
        USING (
          EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.user_id = users.id
              AND m.family_id = NULLIF(current_setting('app.family_id', true), '')::integer
          )
        )
        """
    )


def downgrade() -> None:
    for table, policy in (
        ("users", "users_current"),
        ("memberships", "memberships_current"),
        ("families", "families_current"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in reversed(DOMAIN_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_family_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("llm_calls")
    for table, constraint in (
        ("expense_classifications", "fk_classifications_family_expense"),
        ("expense_classifications", "fk_classifications_family_report"),
        ("expenses", "fk_expenses_family_member"),
        ("expenses", "fk_expenses_family_fixed"),
        ("expenses", "fk_expenses_family_subcategory"),
        ("expenses", "fk_expenses_family_category"),
        ("fixed_expenses", "fk_fixed_expenses_family_subcategory"),
        ("fixed_expenses", "fk_fixed_expenses_family_category"),
        ("keywords", "fk_keywords_family_subcategory"),
        ("keywords", "fk_keywords_family_category"),
        ("subcategories", "fk_subcategories_family_category"),
    ):
        op.drop_constraint(constraint, table, type_="foreignkey")
    for table in ("categories", "subcategories", "fixed_expenses", "expenses", "reports"):
        op.drop_constraint(f"uq_{table}_family_id_id", table, type_="unique")
    op.drop_constraint("uq_memberships_family_user", "memberships", type_="unique")
    op.drop_constraint("uq_subcategories_family_category_name", "subcategories", type_="unique")
    op.drop_constraint("uq_keywords_family_keyword", "keywords", type_="unique")
    op.create_unique_constraint("keywords_keyword_key", "keywords", ["keyword"])
    op.drop_constraint("uq_categories_family_name", "categories", type_="unique")
    op.create_unique_constraint("categories_name_key", "categories", ["name"])
    op.drop_constraint("pk_ipc_series", "ipc_series", type_="primary")
    op.create_primary_key("ipc_series_pkey", "ipc_series", ["year", "month"])
    for table in reversed(DOMAIN_TABLES[:-1]):
        op.drop_index(f"ix_{table}_family_id", table_name=table)
        op.drop_column(table, "family_id")
    op.drop_table("memberships")
    op.drop_constraint("fk_families_created_by_user", "families", type_="foreignkey")
    op.drop_table("families")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "email")
