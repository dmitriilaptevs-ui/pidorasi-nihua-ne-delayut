"""Text-only Hermes and Pi execution through the isolated local agent runtime."""

from __future__ import annotations

import ipaddress
import json
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_user, get_db, require_origin
from ..errors import ApiError
from ..models import User
from ..services import gateway as gateway_service
from ..services import keys as key_service
from ..throttle import allow

router = APIRouter(prefix="/api/agents", tags=["agents"])

MAX_BODY_BYTES = 16 * 1024
MAX_PROMPT_CHARS = 12_000
MAX_MODEL_CHARS = 200
MAX_API_KEY_CHARS = 120
RUNTIME_TIMEOUT_SECONDS = 135.0
RUN_LIMIT = 5
RUN_WINDOW_SECONDS = 60

AGENTS = [
    {
        "id": "hermes",
        "name": "Hermes",
        "description": "Text assistant powered by Hermes Agent.",
    },
    {
        "id": "pi",
        "name": "Pi",
        "description": "Text assistant powered by Pi coding agent.",
    },
]


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: str = Field(min_length=1, max_length=16)
    api_key: str = Field(min_length=20, max_length=MAX_API_KEY_CHARS)
    model: str = Field(min_length=1, max_length=MAX_MODEL_CHARS)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)


def _runtime_url() -> str | None:
    value = os.getenv("AGENT_RUNTIME_URL", "").strip().rstrip("/")
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            return value
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
            return None
        try:
            address = ipaddress.ip_address(parsed.hostname)
            return value if address.is_loopback else None
        except ValueError:
            return value if parsed.hostname.lower() == "localhost" else None
    except ValueError:
        return None


async def _read_payload(request: Request) -> AgentRunRequest:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                raise ApiError(413, "request_too_large", "Request body is too large.")
        except ValueError:
            raise ApiError(400, "invalid_request", "Request body is invalid.") from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(413, "request_too_large", "Request body is too large.")
    try:
        value = json.loads(body)
        return AgentRunRequest.model_validate(value)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
        raise ApiError(400, "invalid_request", "Provide an engine, API key, model, and prompt.") from None


@router.get("")
async def list_agents(_: User = Depends(current_user)) -> dict[str, list[dict[str, object]]]:
    runtime_url = _runtime_url()
    token = os.getenv("AGENT_RUNTIME_TOKEN", "")
    available: dict[str, bool] = {item["id"]: False for item in AGENTS}
    if runtime_url and token:
        try:
            async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
                response = await client.get(
                    f"{runtime_url}/health",
                    headers={"authorization": f"Bearer {token}"},
                )
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict) and isinstance(data.get("agents"), dict):
                        available.update({key: bool(data["agents"].get(key)) for key in available})
        except (httpx.HTTPError, ValueError):
            pass
    return {"items": [{**item, "available": available[item["id"]]} for item in AGENTS]}


@router.post("/run")
async def run_agent(
    request: Request,
    _: None = Depends(require_origin),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    payload = await _read_payload(request)
    if payload.engine not in {"hermes", "pi"}:
        raise ApiError(400, "invalid_engine", "Choose Hermes or Pi.")

    resolved = await key_service.authenticate_api_key(db, raw_key=payload.api_key)
    if resolved is None or resolved[0].id != user.id:
        raise ApiError(401, "invalid_api_key", "The platform API key is invalid or unavailable.")

    try:
        await gateway_service.resolve_model(db, model_id=payload.model)
    except gateway_service.GatewayError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None

    if not await allow(f"agent-run:{user.id}", RUN_LIMIT, RUN_WINDOW_SECONDS):
        raise ApiError(429, "rate_limited", "Too many agent runs. Try again shortly.")

    runtime_url = _runtime_url()
    token = os.getenv("AGENT_RUNTIME_TOKEN", "")
    if not runtime_url or not token:
        raise ApiError(503, "agent_runtime_unavailable", "Agent runtime is not configured.")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(RUNTIME_TIMEOUT_SECONDS, connect=2.0), trust_env=False
        ) as client:
            response = await client.post(
                f"{runtime_url}/run",
                headers={"authorization": f"Bearer {token}"},
                json={
                    "engine": payload.engine,
                    "api_key": payload.api_key,
                    "model": payload.model,
                    "prompt": payload.prompt,
                },
            )
    except httpx.TimeoutException:
        raise ApiError(504, "agent_timeout", "The agent request timed out.") from None
    except httpx.HTTPError:
        raise ApiError(503, "agent_runtime_unavailable", "Agent runtime is unavailable.") from None

    if response.status_code == 504:
        raise ApiError(504, "agent_timeout", "The agent request timed out.")
    if response.status_code != 200:
        raise ApiError(502, "agent_failed", "The agent could not complete this request.")
    try:
        result = response.json()
    except ValueError:
        raise ApiError(502, "agent_failed", "The agent could not complete this request.") from None
    text = result.get("text") if isinstance(result, dict) else None
    if not isinstance(text, str) or len(text) > 64_000:
        raise ApiError(502, "agent_failed", "The agent returned an invalid response.")
    return {"text": text}
