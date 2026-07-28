"""Add one-use Telegram account linking tokens.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_link_tokens",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_telegram_link_tokens_user", "telegram_link_tokens", ["user_id", "created_at"])
    op.create_index("ix_telegram_link_tokens_expires", "telegram_link_tokens", ["expires_at"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON telegram_link_tokens TO gastos_superadmin")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gastos_superadmin")


def downgrade() -> None:
    op.drop_index("ix_telegram_link_tokens_expires", table_name="telegram_link_tokens")
    op.drop_index("ix_telegram_link_tokens_user", table_name="telegram_link_tokens")
    op.drop_table("telegram_link_tokens")
