"""Wallets, double-entry ledger, reserves and reconciliation

Revision ID: 0004_ledger
Revises: 0003_keys_catalog
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_ledger"
down_revision = "0003_keys_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("balance_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sub_kopeck_remainder", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_wallets_user_id"),
    )

    op.create_table(
        "ledger_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=False),
        sa.Column("memo", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("reference", name="uq_ledger_transactions_reference"),
    )

    op.create_table(
        "ledger_postings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ledger_transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_code", sa.String(length=80), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ledger_postings_transaction_id", "ledger_postings", ["transaction_id"], unique=False)
    op.create_index("ix_ledger_postings_account_code", "ledger_postings", ["account_code"], unique=False)

    op.create_table(
        "reserves",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_ref", sa.String(length=120), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="held"),
        sa.Column("settled_kopecks", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("request_ref", name="uq_reserves_request_ref"),
    )
    op.create_index("ix_reserves_wallet_id", "reserves", ["wallet_id"], unique=False)
    op.create_index("ix_reserves_request_ref", "reserves", ["request_ref"], unique=True)

    op.create_table(
        "reconciliation_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "reserve_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reserves.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_ref", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reconciliation_items_request_ref", "reconciliation_items", ["request_ref"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reconciliation_items_request_ref", table_name="reconciliation_items")
    op.drop_table("reconciliation_items")
    op.drop_index("ix_reserves_request_ref", table_name="reserves")
    op.drop_index("ix_reserves_wallet_id", table_name="reserves")
    op.drop_table("reserves")
    op.drop_index("ix_ledger_postings_account_code", table_name="ledger_postings")
    op.drop_index("ix_ledger_postings_transaction_id", table_name="ledger_postings")
    op.drop_table("ledger_postings")
    op.drop_table("ledger_transactions")
    op.drop_table("wallets")
