"""FastAPI entrypoint for the platform API.

Milestone 1 (infrastructure) exposes only liveness/readiness. Identity, keys,
catalog, ledger, gateway and payments are added by later modules; this file
stays the composition root.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from . import db, redis_client
from .settings import settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await db.dispose_database()
    await redis_client.close_redis()


app = FastAPI(
    title="rubai platform API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.service_name, "env": settings.app_env}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process answers requests. Never touches dependencies."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(response: Response) -> dict[str, object]:
    """Readiness: database and Redis must both answer before we serve traffic."""
    database = await db.check_database()
    redis_ok = await redis_client.check_redis()
    ready = database and redis_ok
    response.status_code = 200 if ready else 503
    return {"status": "ok" if ready else "degraded", "database": database, "redis": redis_ok}
