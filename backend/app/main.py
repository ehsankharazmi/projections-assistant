"""FastAPI application factory and process lifecycle."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.ai.demo import DemoResponder
from app.ai.ledger import Ledger, Pricing
from app.ai.orchestrator import ChatService, recover_interrupted_turns
from app.ai.provider import ClaudeAdapter
from app.api import router
from app.auth import Authenticator
from app.config import Settings, get_settings
from app.context import build_context, set_context
from app.db import database_url, get_engine, init_engine
from app.errors import AppError
from app.migrations import upgrade_database
from app.services.jobs import JobMonitor

log = logging.getLogger("app")
WORKER_HEARTBEAT_STALE_SECONDS = 30


def create_app(settings: Settings | None = None, *, anthropic_client=None, access_verifier=None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    url = database_url(settings.data_dir)
    upgrade_database(url)
    init_engine(url)
    ctx = build_context(settings)
    set_context(ctx)

    ledger = adapter = None
    if settings.ai_mode == "anthropic":
        ledger = Ledger(Pricing.load(settings.ai_pricing_file), settings.ai_daily_budget_usd,
                        settings.ai_monthly_budget_usd)
        adapter = ClaudeAdapter(settings, ledger, client=anthropic_client)
    monitor = JobMonitor(ctx)
    chat = ChatService(ctx, monitor, adapter, DemoResponder())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        interrupted = recover_interrupted_turns()
        if interrupted:
            log.warning("marked %d interrupted conversation turns as failed", interrupted)
        monitor.start()
        yield
        monitor.stop()

    app = FastAPI(title="Conversational Workbook Assistant", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.ctx = ctx
    app.state.monitor = monitor
    app.state.chat = chat
    app.state.ledger = ledger
    app.state.adapter = adapter
    app.state.authenticator = Authenticator(settings, verifier=access_verifier)

    def health() -> dict:
        components = {}
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            components["database"] = "ok"
        except Exception:
            components["database"] = "error"
        heartbeat = Path(settings.jobs_dir) / ".worker-heartbeat"
        if settings.worker_mode == "inprocess":
            components["calculation_worker"] = "ok (in-process)"
        elif heartbeat.exists() and time.time() - heartbeat.stat().st_mtime < WORKER_HEARTBEAT_STALE_SECONDS:
            components["calculation_worker"] = "ok"
        else:
            components["calculation_worker"] = "unavailable"
        components["ai"] = "demo (scripted)" if settings.ai_mode == "demo" else "configured"
        status = "ok" if all(not v.startswith(("error", "unavailable")) for v in components.values()) else "degraded"
        return {"status": status, "components": components}

    app.state.health = health

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        diagnostic_id = uuid.uuid4().hex[:12]
        if exc.status_code >= 500:
            log.error("diagnostic %s: %s %s", diagnostic_id, exc.code, exc.message)
        body = {"error": {"code": exc.code, "message": exc.message, "diagnostic_id": diagnostic_id}}
        if exc.details:
            body["error"]["details"] = exc.details
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        problems = [f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors()[:5]]
        return JSONResponse({"error": {"code": "invalid_request", "message": "The request was not valid.",
                                       "details": {"problems": problems}, "diagnostic_id": uuid.uuid4().hex[:12]}},
                            status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        diagnostic_id = uuid.uuid4().hex[:12]
        log.exception("diagnostic %s: unhandled error on %s %s", diagnostic_id, request.method, request.url.path)
        return JSONResponse({"error": {"code": "internal_error", "message": "Something went wrong. Your workbook "
                                       "is unchanged.", "diagnostic_id": diagnostic_id}}, status_code=500)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    app.include_router(router)
    return app


def run() -> None:  # pragma: no cover - process entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(), host="0.0.0.0", port=8000, proxy_headers=False, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    run()
