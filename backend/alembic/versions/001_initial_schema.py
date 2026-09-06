"""Create FluxSwarm PostgreSQL schema (base tables)

Revision ID: 001
Revises:
Create Date: 2026-09-04

Mirrors the SQLite schema from :mod:`db` (ten tables). JSON payload columns map
to PostgreSQL ``JSON``; epoch-seconds REAL timestamps become BIGINT. Constraint
columns deliberately WITHOUT their secondary indexes: those are added in
``002_add_indexes`` (unique email / board_slug / event_id, referrer index) so a
single logical migration documents them explicitly.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    user_plan = postgresql.ENUM(
        "demo", "starter", "pro", "scale", name="user_plan", create_type=False
    )
    user_plan.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("pw_hash", sa.Text(), nullable=False),
        sa.Column("plan", user_plan, nullable=False, server_default="demo"),
        sa.Column("credits", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("ref_code", sa.Text(), nullable=False, unique=True),
        sa.Column("referred_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("logged_out_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("credits >= 0", name="ck_users_credits_non_negative"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("board_slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("launch_status", sa.Text(), nullable=True),
        sa.Column("launch_outcome", sa.Text(), nullable=True),
        sa.Column("launch_reason", sa.Text(), nullable=True),
        sa.Column("launch_refunded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("launch_updated_at", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_projects_user_id_users"),
    )
    op.create_table(
        "referrals",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("referrer_code", sa.Text(), nullable=False),
        sa.Column("referred_email", sa.Text(), nullable=False),
        sa.Column("rewarded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "squad_templates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("agents", sa.JSON(), nullable=False),
        sa.Column("price_credits", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name="fk_squad_templates_author_id_users"),
    )
    op.create_table(
        "template_purchases",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("template_id", sa.BigInteger(), nullable=False),
        sa.Column("buyer_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["squad_templates.id"], name="fk_template_purchases_template_id_squad_templates"),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"], name="fk_template_purchases_buyer_id_users"),
    )
    op.create_table(
        "payment_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("gateway", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_payment_events_user_id_users"),
    )
    op.create_table(
        "telegram_links",
        sa.Column("telegram_chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("linked_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_telegram_links_user_id_users"),
    )
    op.create_table(
        "telegram_codes",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_telegram_codes_user_id_users"),
    )
    op.create_table(
        "password_resets",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_password_resets_user_id_users"),
    )
    op.create_table(
        "demo_usage",
        sa.Column("who", sa.Text(), nullable=False),
        sa.Column("day", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("who", "day", name="pk_demo_usage"),
    )

    # Indexes mirrored from db.py's baseline (these are the base-schema ones).
    op.create_index("ix_projects_user_id", "projects", ["user_id"])
    op.create_index("ix_template_purchases_template_id", "template_purchases", ["template_id"])
    op.create_index("ix_template_purchases_buyer_id", "template_purchases", ["buyer_id"])


def downgrade() -> None:
    op.drop_table("demo_usage")
    op.drop_table("password_resets")
    op.drop_table("telegram_codes")
    op.drop_table("telegram_links")
    op.drop_table("payment_events")
    op.drop_table("template_purchases")
    op.drop_table("squad_templates")
    op.drop_table("referrals")
    op.drop_table("projects")
    op.drop_table("users")
    postgresql.ENUM(name="user_plan").drop(op.get_bind(), checkfirst=True)