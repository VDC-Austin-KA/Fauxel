"""FastAPI application factory."""
from __future__ import annotations

import shutil
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request

from . import __version__
from .auth import public_window_open, require_auth
from .config import Settings
from .db import make_pool
from .migrate import apply_migrations
from .routers import admin, aps, captures, openbim, progress, projects, qc, training, uploads
from .storage import make_storage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.auto_migrate:
            apply_migrations(settings.database_url, settings.migrations_dir)
        app.state.pool.open()
        try:
            yield
        finally:
            app.state.pool.close()

    app = FastAPI(title="Fauxel API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.pool = make_pool(settings.database_url)
    app.state.storage = make_storage(settings)

    open_router = APIRouter(prefix="/api/v1")

    @open_router.get("/health")
    def health(request: Request):
        try:
            with request.app.state.pool.connection() as conn:
                db = conn.execute("SELECT 1").fetchone() is not None
        except Exception:
            db = False
        try:
            storage = bool(request.app.state.storage.ping())
        except Exception:
            storage = False
        return {"status": "ok" if db and storage else "degraded", "db": db, "storage": storage,
                "gpu": shutil.which("nvidia-smi") is not None,
                "public_read_until": settings.public_read_until.isoformat() if public_window_open(settings) else None}

    app.include_router(open_router)
    for r in (projects.router, captures.router, progress.router, qc.router,
              uploads.router, training.router, aps.router, openbim.router):
        app.include_router(r, prefix="/api/v1", dependencies=[Depends(require_auth)])
    app.include_router(aps.public_router, prefix="/api/v1")  # OAuth callback + webhook verify their own state/signature
    app.include_router(admin.router, prefix="/api/v1")  # guarded by auth.require_admin (bootstrap: no key exists yet)
    return app


def app_factory() -> FastAPI:  # uvicorn fauxel_api.app:app_factory --factory
    return create_app()
