"""Phase 5: CCPA/CPRA ADMT compliance schema.

Adds users.admt_opt_out (right to opt out of ADMT-assisted launches) and the
admt_disclosures ledger used for:
  - pre-use notice acknowledgments (acknowledged_at)
  - human-review requests and their admin resolution (requested_at, status,
    reviewer_notes, reviewed_at)
  - logic-access snapshots (admt_type + logic_summary)

Revision ID: 004
Revises: 003
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("admt_opt_out", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "admt_disclosures",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("disclosed_at", sa.BigInteger(), nullable=False),
        sa.Column("acknowledged_at", sa.BigInteger(), nullable=True),
        sa.Column("admt_type", sa.Text(), nullable=True),
        sa.Column("logic_summary", sa.Text(), nullable=True),
        sa.Column("human_review_status", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.BigInteger(), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_admt_disclosures_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_admt_disclosures_project_id_projects", ondelete="CASCADE"),
    )
    op.create_index("ix_admt_disclosures_user_id", "admt_disclosures", ["user_id"])
    op.create_index("ix_admt_disclosures_status", "admt_disclosures", ["human_review_status"])


def downgrade() -> None:
    op.drop_index("ix_admt_disclosures_status", table_name="admt_disclosures")
    op.drop_index("ix_admt_disclosures_user_id", table_name="admt_disclosures")
    op.drop_table("admt_disclosures")
    op.drop_column("users", "admt_opt_out")