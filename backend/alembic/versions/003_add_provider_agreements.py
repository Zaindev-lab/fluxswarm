"""Add provider_agreements (Phase 3 BYOK agreement gate).

Revision ID: 003
Revises: 002
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_agreements",
        sa.Column("user_id", sa.BIGINT(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("agreed_at", sa.BIGINT(), nullable=False),
        sa.Column("version", sa.Text(), server_default="1.0", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "provider"),
        sa.UniqueConstraint("user_id", "provider", name="uq_provider_agreements_user_provider"),
    )


def downgrade() -> None:
    op.drop_table("provider_agreements")