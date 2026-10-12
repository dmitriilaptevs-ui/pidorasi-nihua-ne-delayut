"""Encrypted customer provider credentials and key funding source

Revision ID: 0007_provider_credentials
Revises: 0006_api_requests
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_provider_credentials"
down_revision = "0006_api_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("funding_source", sa.String(length=16), server_default="platform", nullable=False),
    )
    op.create_table(
        "provider_credentials",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=False),
        sa.Column("suffix", sa.String(length=8), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("provider_credentials")
    op.drop_column("api_keys", "funding_source")
