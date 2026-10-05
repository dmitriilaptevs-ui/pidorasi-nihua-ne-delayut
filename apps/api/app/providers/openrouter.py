"""OpenRouter adapter.

The provider key never leaves the server. Requests carry only the client's own
platform key, and the upstream payload is built from an allowlist so callers
cannot smuggle arbitrary provider parameters.
"""

from __future__ import annotations

import httpx

from ..settings import settings


class UpstreamConnectError(Exception):
    """The request never reached the provider; releasing funds is safe."""


class UpstreamNotConfigured(Exception):
    """No server-side provider key is configured; nothing was sent."""


class UpstreamError(Exception):
    """Ambiguous upstream failure: the request may have produced provider cost."""


def _client(**kwargs) -> httpx.AsyncClient:
    """Factory kept isolated so tests can inject a mock transport."""
    return httpx.AsyncClient(**kwargs)


class OpenRouterAdapter:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self.base_url = (base_url or settings.openrouter_api_base).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.gateway_timeout_seconds

    def headers(self) -> dict[str, str]:
        if not self.api_key:
            raise UpstreamNotConfigured("provider key is not configured")
        return {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    async def complete(self, payload: dict) -> tuple[int, dict]:
        try:
            headers = self.headers()
        except UpstreamNotConfigured:
            raise
        try:
            async with _client(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise UpstreamConnectError(str(exc)) from None
        except httpx.HTTPError as exc:
            raise UpstreamError(str(exc)) from None
        try:
            data = response.json()
        except ValueError:
            data = {"error": {"message": "upstream returned a non-JSON response", "code": "upstream_invalid"}}
        return response.status_code, data

    async def open_stream(self, payload: dict) -> tuple[httpx.AsyncClient, httpx.Response]:
        """Open a streaming response; the caller must close response and client."""
        headers = self.headers()
        client = _client(timeout=self.timeout, follow_redirects=False)
        request = client.build_request(
            "POST", f"{self.base_url}/chat/completions", json=payload, headers=headers
        )
        try:
            response = await client.send(request, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            await client.aclose()
            raise UpstreamConnectError(str(exc)) from None
        except httpx.HTTPError as exc:
            await client.aclose()
            raise UpstreamError(str(exc)) from None
        return client, response


def upstream_payload(payload: dict, *, stream: bool) -> dict:
    """Build the provider request from an allowlisted subset of the client body."""
    allowed = (
        "model",
        "messages",
        "temperature",
        "top_p",
        "stop",
        "tools",
        "tool_choice",
        "response_format",
        "seed",
        "presence_penalty",
        "frequency_penalty",
        "max_tokens",
    )
    upstream = {key: payload[key] for key in allowed if key in payload}
    upstream["model"] = _upstream_model_id(str(payload["model"]))
    upstream["usage"] = {"include": True}
    if stream:
        upstream["stream"] = True
        upstream["stream_options"] = {"include_usage": True}
    return upstream


def _upstream_model_id(openrouter_id: str) -> str:
    # Catalog ids are already OpenRouter ids (e.g. "acme/chat-1").
    return openrouter_id
