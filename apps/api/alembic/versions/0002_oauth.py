"""OAuth identities and one-use handshakes

Revision ID: 0002_oauth
Revises: 0001_identity
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_oauth"
down_revision = "0001_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=True)
    op.add_column("users", sa.Column("display_name", sa.String(length=80), nullable=True))
    op.add_column("users", sa.Column("oauth_provider", sa.String(length=16), nullable=True))
    op.add_column("users", sa.Column("oauth_subject", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "uq_users_oauth_identity", "users", ["oauth_provider", "oauth_subject"]
    )

    op.create_table(
        "oauth_handshakes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("variant", sa.String(length=16), nullable=False, server_default="canvas"),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("bind_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_oauth_handshakes_state_hash", "oauth_handshakes", ["state_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_oauth_handshakes_state_hash", table_name="oauth_handshakes")
    op.drop_table("oauth_handshakes")
    op.drop_constraint("uq_users_oauth_identity", "users", type_="unique")
    op.drop_column("users", "oauth_subject")
    op.drop_column("users", "oauth_provider")
    op.drop_column("users", "display_name")
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=False)
