"""Phase F: provider_usage — append-only provider accounting ledger.

Records which provider/model actually carried each launch (demo or project),
which runtime source selected it (pool / paid_fallback / byok / default) and
the terminal outcome when known. Purpose: operator observability of where usage
(including any paid fallback) went. The table never gates spend — budget gating
stays in provider_guard's fail-closed gate; this is the after-the-fact record.

``ok`` is NULL when the attempt is recorded but the launch has not finalized;
outcome updates key on the unique board slug.

Revision ID: 006
Revises: 005
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("runtime_source", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("ok", sa.Integer(), nullable=True),
        sa.Column("runtime_s", sa.Integer(), nullable=True),
        sa.Column("tasks", sa.Integer(), nullable=True),
        sa.Column("slug", sa.Text(), nullable=True),
    )
    op.create_index("ix_provider_usage_day", "provider_usage", ["day"])
    op.create_index("ix_provider_usage_surface", "provider_usage", ["surface"])
    op.create_index("ix_provider_usage_slug", "provider_usage", ["slug"])


def downgrade() -> None:
    op.drop_index("ix_provider_usage_day", table_name="provider_usage")
    op.drop_index("ix_provider_usage_surface", table_name="provider_usage")
    op.drop_index("ix_provider_usage_slug", table_name="provider_usage")
    op.drop_table("provider_usage")