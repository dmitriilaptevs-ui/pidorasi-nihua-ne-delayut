"""API keys and the model catalog with versioned RUB pricing

Revision ID: 0003_keys_catalog
Revises: 0002_oauth
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_keys_catalog"
down_revision = "0002_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=24), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("monthly_limit_kopecks", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"], unique=False)
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    op.create_table(
        "catalog_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("openrouter_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("context_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_catalog_models_openrouter_id", "catalog_models", ["openrouter_id"], unique=True)

    op.create_table(
        "catalog_pricing",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "model_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("catalog_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_usd_per_mtok", sa.Numeric(18, 8), nullable=False),
        sa.Column("output_usd_per_mtok", sa.Numeric(18, 8), nullable=False),
        sa.Column("cached_usd_per_mtok", sa.Numeric(18, 8), nullable=True),
        sa.Column("fx_rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("markup", sa.Numeric(8, 4), nullable=False),
        sa.Column("input_rub_per_mtok", sa.Numeric(18, 6), nullable=False),
        sa.Column("output_rub_per_mtok", sa.Numeric(18, 6), nullable=False),
        sa.Column("cached_rub_per_mtok", sa.Numeric(18, 6), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("model_id", "version", name="uq_catalog_pricing_version"),
    )
    op.create_index("ix_catalog_pricing_model_id", "catalog_pricing", ["model_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_catalog_pricing_model_id", table_name="catalog_pricing")
    op.drop_table("catalog_pricing")
    op.drop_index("ix_catalog_models_openrouter_id", table_name="catalog_models")
    op.drop_table("catalog_models")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
