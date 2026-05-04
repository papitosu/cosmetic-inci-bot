"""initial schema (free, no billing)

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    skin_type = postgresql.ENUM(
        "unknown", "dry", "oily", "combination", "sensitive", "normal", "acne_prone",
        name="skin_type",
        create_type=False,
    )
    analysis_source = postgresql.ENUM(
        "text", "photo", "product",
        name="analysis_source",
        create_type=False,
    )

    skin_type.create(op.get_bind(), checkfirst=True)
    analysis_source.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger, nullable=False),
        sa.Column("username", sa.String(64)),
        sa.Column("full_name", sa.String(256)),
        sa.Column("skin_type", skin_type, nullable=False, server_default="unknown"),
        sa.Column("language", sa.String(8), nullable=False, server_default="ru"),
        sa.Column("is_banned", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("telegram_id", name="uq_users_telegram_id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", analysis_source, nullable=False),
        sa.Column("raw_input", sa.Text),
        sa.Column("product_title", sa.String(256)),
        sa.Column("ingredients", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("risk_score", sa.Numeric(5, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_analyses_user_id", "analyses", ["user_id"])
    op.create_index("ix_analyses_created_at", "analyses", ["created_at"])
    op.create_index("ix_analyses_user_created", "analyses", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_analyses_user_created", table_name="analyses")
    op.drop_index("ix_analyses_created_at", table_name="analyses")
    op.drop_index("ix_analyses_user_id", table_name="analyses")
    op.drop_table("analyses")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")

    postgresql.ENUM(name="analysis_source").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="skin_type").drop(op.get_bind(), checkfirst=True)
