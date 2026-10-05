# Platform API (FastAPI)

Milestone 1: only liveness/readiness. Identity, keys, catalog, ledger, gateway
and payments arrive in later modules (see [ADR-0001](../../docs/adr/0001-backend-stack.md)).

## Endpoints

| Path | Purpose |
| --- | --- |
| `GET /` | Service name and environment |
| `GET /healthz` | Liveness; never touches dependencies |
| `GET /readyz` | Readiness; 503 until PostgreSQL and Redis answer |

## Local run

```bash
cd apps/api
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 127.0.0.1 --port 8080
python -m pytest -q
```

No secrets belong in this directory. Deployment injects `DATABASE_URL` and
`REDIS_URL` from the environment; see [infra/README.md](../../infra/README.md).
