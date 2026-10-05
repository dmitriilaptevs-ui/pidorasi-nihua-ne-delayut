"""Prepayments through a payment provider (sandbox by policy)

Revision ID: 0005_payments
Revises: 0004_ledger
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_payments"
down_revision = "0004_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=16), nullable=False, server_default="yookassa"),
        sa.Column("provider_payment_id", sa.String(length=120), nullable=False),
        sa.Column("idempotence_key", sa.String(length=64), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("confirmation_url", sa.Text(), nullable=True),
        sa.Column("refunded_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"], unique=False)
    op.create_index("ix_payments_provider_payment_id", "payments", ["provider_payment_id"], unique=True)
    op.create_index("ix_payments_idempotence_key", "payments", ["idempotence_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_payments_idempotence_key", table_name="payments")
    op.drop_index("ix_payments_provider_payment_id", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")
