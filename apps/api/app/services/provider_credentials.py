"""Encrypted per-user OpenRouter credentials."""

from __future__ import annotations

import base64
import binascii
import secrets
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import ProviderCredential
from ..settings import settings


def _cipher() -> AESGCM:
    try:
        key = base64.urlsafe_b64decode(settings.provider_key_encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError, binascii.Error):
        key = b""
    if len(key) != 32:
        raise ApiError(503, "credential_storage_unavailable", "Хранилище ключей провайдера не настроено.")
    return AESGCM(key)


def encrypt_key(*, user_id: uuid.UUID, api_key: str) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + _cipher().encrypt(nonce, api_key.encode("utf-8"), str(user_id).encode("ascii"))


def decrypt_key(credential: ProviderCredential) -> str:
    ciphertext = credential.encrypted_key
    try:
        return _cipher().decrypt(
            ciphertext[:12], ciphertext[12:], str(credential.user_id).encode("ascii")
        ).decode("utf-8")
    except Exception as exc:
        raise ApiError(503, "credential_unavailable", "Ключ провайдера недоступен.") from exc


async def get_credential(db: AsyncSession, *, user_id: uuid.UUID) -> ProviderCredential | None:
    return await db.get(ProviderCredential, user_id)


async def save_credential(db: AsyncSession, *, user_id: uuid.UUID, api_key: str) -> ProviderCredential:
    credential = await db.get(ProviderCredential, user_id)
    encrypted = encrypt_key(user_id=user_id, api_key=api_key)
    if credential is None:
        credential = ProviderCredential(
            user_id=user_id, encrypted_key=encrypted, suffix=api_key[-4:]
        )
        db.add(credential)
    else:
        credential.encrypted_key = encrypted
        credential.suffix = api_key[-4:]
    await db.flush()
    return credential
