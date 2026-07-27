"""Add application identity and authentication tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Telegram is optional for web-created identities. Phase 5 adds the
    # self-service linking flow.
    op.alter_column("users", "telegram_id", existing_type=sa.Text(), nullable=True)
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "otp_codes",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("flow", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("family_name", sa.Text(), nullable=True),
        sa.CheckConstraint("flow IN ('login', 'register')"),
    )
    op.create_index("ix_otp_codes_email_created", "otp_codes", ["email", "created_at"])

    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_user_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_identity"),
        sa.UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),
        sa.CheckConstraint("provider IN ('google')"),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sessions, otp_codes, oauth_identities TO gastos_superadmin")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gastos_superadmin")


def downgrade() -> None:
    op.drop_table("oauth_identities")
    op.drop_index("ix_otp_codes_email_created", table_name="otp_codes")
    op.drop_table("otp_codes")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_column("users", "last_login_at")
    op.alter_column("users", "telegram_id", existing_type=sa.Text(), nullable=False)
