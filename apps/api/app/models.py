"""Identity tables: users, durable sessions and one-time email tokens.

Money-adjacent modules (keys, ledger, gateway) build on `users.id` as the
account identity. Emails are stored lowercase; uniqueness is enforced by the
database, not only by the application.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable: OAuth-only accounts may have no email until they add one.
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # password | vk | yandex — how the account was originally created.
    signup_method: Mapped[str] = mapped_column(String(16), default="password")
    # External identity for OAuth signups; unique per provider.
    oauth_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    oauth_subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="user")
    status: Mapped[str] = mapped_column(String(16), default="active")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    method: Mapped[str] = mapped_column(String(16), default="password")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailToken(Base):
    __tablename__ = "email_tokens"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # verify_email | reset_password
    kind: Mapped[str] = mapped_column(String(24))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthHandshake(Base):
    """One-use OAuth handshake: state, PKCE verifier and browser binding.

    Persisted so a restart between start and callback cannot lose a login and
    so replay protection works across processes (row-locked consumption).
    """

    __tablename__ = "oauth_handshakes"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(16))
    variant: Mapped[str] = mapped_column(String(16), default="canvas")
    code_verifier: Mapped[str] = mapped_column(String(128))
    bind_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiKey(Base):
    """Platform API key. Only the SHA-256 digest and a display prefix persist."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    prefix: Mapped[str] = mapped_column(String(24))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    monthly_limit_kopecks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CatalogModel(Base):
    __tablename__ = "catalog_models"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    openrouter_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(100))
    context_length: Mapped[int] = mapped_column(Integer, default=0)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CatalogPricing(Base):
    """Immutable pricing version. A sync never edits an existing row."""

    __tablename__ = "catalog_pricing"
    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_catalog_pricing_version"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("catalog_models.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    input_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    output_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    cached_usd_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    markup: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    input_rub_per_mtok: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    output_rub_per_mtok: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    cached_rub_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
