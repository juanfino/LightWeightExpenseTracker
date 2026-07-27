"""Add family invitations, historical memberships and superadmin flag.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "memberships",
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "memberships",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Historical memberships must remain because domain rows reference
    # (family_id, user_id). Only one *active* family is allowed per person.
    op.drop_constraint("uq_memberships_user_id", "memberships", type_="unique")
    op.create_index(
        "uq_memberships_active_user",
        "memberships",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("family_id", sa.Integer(), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("role", sa.Text(), nullable=False, server_default="member"),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role = 'member'", name="ck_invitations_member_only"),
    )
    op.create_index("ix_invitations_family_created", "invitations", ["family_id", "created_at"])
    op.create_index("ix_invitations_expires", "invitations", ["expires_at"])

    op.add_column(
        "otp_codes",
        sa.Column(
            "invitation_id",
            sa.Integer(),
            sa.ForeignKey("invitations.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.drop_constraint("otp_codes_flow_check", "otp_codes", type_="check")
    op.create_check_constraint(
        "otp_codes_flow_check",
        "otp_codes",
        "flow IN ('login', 'register', 'invite')",
    )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON invitations TO gastos_superadmin"
    )
    op.execute("GRANT DELETE ON families TO gastos_superadmin")
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gastos_superadmin"
    )


def downgrade() -> None:
    op.drop_constraint("otp_codes_flow_check", "otp_codes", type_="check")
    op.create_check_constraint(
        "otp_codes_flow_check",
        "otp_codes",
        "flow IN ('login', 'register')",
    )
    op.drop_column("otp_codes", "invitation_id")
    op.drop_index("ix_invitations_expires", table_name="invitations")
    op.drop_index("ix_invitations_family_created", table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("uq_memberships_active_user", table_name="memberships")
    op.create_unique_constraint("uq_memberships_user_id", "memberships", ["user_id"])
    op.drop_column("memberships", "deactivated_at")
    op.drop_column("memberships", "active")
    op.drop_column("users", "is_superadmin")
