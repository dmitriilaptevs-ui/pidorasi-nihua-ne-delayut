"""FastAPI entrypoint for the platform API.

Milestone 1 (infrastructure) exposes only liveness/readiness. Identity, keys,
catalog, ledger, gateway and payments are added by later modules; this file
stays the composition root.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from . import db, redis_client
from .api.admin import admin_router
from .api.identity import router as identity_router
from .api.oauth import router as oauth_router
from .errors import ApiError, api_error_handler
from .settings import settings

# Container logs carry service messages only; secrets are never logged. The
# console mail transport prints verification links in development only.
# force=True matters when a log config (e.g. uvicorn --log-config) already
# installed root handlers before this module is imported.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", force=True)
logging.getLogger("rubai").setLevel(logging.INFO)


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

app.add_exception_handler(ApiError, api_error_handler)
app.include_router(identity_router)
app.include_router(oauth_router)
app.include_router(admin_router)


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
