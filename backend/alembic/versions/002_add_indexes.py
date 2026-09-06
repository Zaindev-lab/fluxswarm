"""Add secondary indexes for the 002 migration milestone

Revision ID: 002
Revises: 001
Create Date: 2026-09-04

Adds the documented lookup indexes:
* users.email            (unique)
* projects.board_slug    (unique)
* payment_events.event_id(unique)
* referrals.referrer_code(plain index)

These mirror what the 002 target list names explicitly; created AFTER the base
tables so ``001`` stays the pure schema snapshot.
"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_projects_board_slug", "projects", ["board_slug"], unique=True)
    op.create_index("ix_payment_events_event_id", "payment_events", ["event_id"], unique=True)
    op.create_index("ix_referrals_referrer_code", "referrals", ["referrer_code"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_referrals_referrer_code", table_name="referrals")
    op.drop_index("ix_payment_events_event_id", table_name="payment_events")
    op.drop_index("ix_projects_board_slug", table_name="projects")
    op.drop_index("ix_users_email", table_name="users")