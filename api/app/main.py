from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db as db_module
from app.config import get_settings
from app.deps import require_credentials_set
from app.jobs.scheduler import build_scheduler
from app.routers import (
    alerts,
    auth_local,
    boxes,
    dashboard,
    employees,
    exports,
    imports,
    productivity,
    requests,
    stream,
    users,
    warehouses,
)
from app.services.local_admin import ensure_local_admin


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.api_log_level)

    # Provision the break-glass local admin on first boot (idempotent).
    # Use a late lookup so the test suite (which monkey-patches SessionLocal)
    # picks up its own session factory.
    db = db_module.SessionLocal()
    try:
        ensure_local_admin(db)
    finally:
        db.close()

    scheduler = build_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Warehouse Box Tracker API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

settings = get_settings()
if settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

api_router = APIRouter(prefix="/api")


@api_router.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


api_router.include_router(auth_local.router)

# Everything past this gate is blocked (HTTP 428) until a bootstrapped local
# admin has chosen their own username + password.
gated = [Depends(require_credentials_set)]
api_router.include_router(users.router, dependencies=gated)
api_router.include_router(warehouses.router, dependencies=gated)
api_router.include_router(boxes.router, dependencies=gated)
api_router.include_router(imports.router, dependencies=gated)
api_router.include_router(alerts.router, dependencies=gated)
api_router.include_router(dashboard.router, dependencies=gated)
api_router.include_router(employees.router, dependencies=gated)
api_router.include_router(productivity.router, dependencies=gated)
api_router.include_router(requests.router, dependencies=gated)
api_router.include_router(exports.router, dependencies=gated)
# The SSE stream takes its access token via `?access_token=` (EventSource cannot
# set headers), so it does its own auth + credentials-must-change check inline
# instead of going through the Authorization-header gate.
api_router.include_router(stream.router)

app.include_router(api_router)
