"""P2.1: age/ToS gate (COPPA 13+ / GDPR-UK 16+, AADC).

Adds users.tos_accepted_at: the trusted timestamp persisted when an account
agrees to the Terms & Privacy Policy at registration and confirms minimum age.
A NULL value means consent was never captured (legacy accounts); the app treats
a missing value as "not consented" for any policy that requires it.

Revision ID: 005
Revises: 004
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("tos_accepted_at", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "tos_accepted_at")