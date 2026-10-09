# Platform API (FastAPI)

FastAPI serves identity, provider credentials, platform keys, catalog, gateway,
wallet, and agent routes. The compose deployment keeps the API on loopback; a
Sites Worker can proxy authenticated site traffic to the owner's computer.

## Endpoints

| Path | Purpose |
| --- | --- |
| `GET /` | Service name and environment |
| `GET /healthz` | Liveness; never touches dependencies |
| `GET /readyz` | Readiness; 503 until PostgreSQL and Redis answer |
| `/api/*`, `/v1/*` | Identity, encrypted OpenRouter BYOK, platform keys, gateway, wallet, and agent requests |

## Local run

```bash
cd apps/api
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 127.0.0.1 --port 8080
python -m pytest -q
```

No secrets belong in this directory. Compose injects settings from
`infra/.env`; OAuth credentials remain in `apps/web/.env.local`, which is read
only by the API container. The web container receives neither file.

Set `PROVIDER_KEY_ENCRYPTION_KEY` to a 32-byte key encoded with URL-safe
Base64 before accepting customer OpenRouter credentials. Each user's BYOK is
encrypted with AES-GCM and bound to that user's id. Platform-funded requests
use the server-side `OPENROUTER_API_KEY`. Both values are API-only and are
never Docker build arguments. See [infra/README.md](../../infra/README.md) for
the private rollout topology.
