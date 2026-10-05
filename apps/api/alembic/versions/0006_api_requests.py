"""Per-request history with tokens, cost and tariff snapshot

Revision ID: 0006_api_requests
Revises: 0005_payments
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_api_requests"
down_revision = "0005_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_ref", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="succeeded"),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("fx_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column("markup", sa.Numeric(8, 4), nullable=True),
        sa.Column("price_version", sa.Integer(), nullable=True),
        sa.Column("provider_request_id", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_api_requests_user_id", "api_requests", ["user_id"], unique=False)
    op.create_index("ix_api_requests_request_ref", "api_requests", ["request_ref"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_api_requests_request_ref", table_name="api_requests")
    op.drop_index("ix_api_requests_user_id", table_name="api_requests")
    op.drop_table("api_requests")
