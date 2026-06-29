from __future__ import annotations

import pathlib
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes.api import router as api_router

_PKG_DIR = pathlib.Path(__file__).parent.parent  # syswatch_server/
_STATIC_DIR = _PKG_DIR / "static"  # syswatch_server/static/
_INDEX_HTML = _STATIC_DIR / "index.html"


def create_app(
    cfg: Any,
    db_pool: Any,
    ca: Any,
    servicer: Any,
    metrics: Any,
    alert_sender: Any,
    auth: Any,
) -> FastAPI:
    """
    Assemble and return the FastAPI application.

    Args:
        cfg:          Top-level Config dataclass.
        db_pool:      asyncpg.Pool
        ca:           CertificateAuthority
        servicer:     MetricServicer (for live connected-agent status)
        metrics:      MetricsRegistry
        alert_sender: AlertSender
        auth:         AuthManager

    Returns:
        Configured FastAPI app ready to pass to uvicorn.
    """
    app = FastAPI(
        title="syswatch",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )

    assets_dir = _STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    app.include_router(api_router, prefix="/api")

    app.state.cfg = cfg
    app.state.db_pool = db_pool
    app.state.ca = ca
    app.state.servicer = servicer
    app.state.metrics = metrics
    app.state.alert_sender = alert_sender
    app.state.auth = auth

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        if _INDEX_HTML.exists():
            return FileResponse(_INDEX_HTML)
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(
            "Frontend not built. Run:\n  cd frontend && npm install && npm run build",
            status_code=503,
        )

    return app


def instrument_fastapi_app(app: FastAPI) -> None:
    """
    Apply OpenTelemetry auto-instrumentation to the FastAPI app.
    Called from main.py after create_app() and after setup_tracing().
    """
    from ..observability.tracing import instrument_fastapi

    instrument_fastapi(app)
