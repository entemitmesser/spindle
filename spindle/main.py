from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from spindle.__about__ import APP_NAME, __version__
from spindle.api.router import router as api_router
from spindle.config import ConfigStore, Settings, get_settings
from spindle.db import create_engine, create_session_factory, init_schema
from spindle.errors import NotFoundError, SpindleError, error_code
from spindle.services.events import EventBus
from spindle.utils.logging import configure_logging

logger = structlog.get_logger(__name__)

WEB_DIST_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings)
    app.state.started_at = time.monotonic()

    engine = create_engine(settings)
    await init_schema(engine, settings)
    session_factory = create_session_factory(engine)
    event_bus = EventBus(session_factory)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.event_bus = event_bus
    app.state.config_store = ConfigStore(session_factory, event_bus)

    retention_task = asyncio.create_task(event_bus.run_retention_loop())

    logger.info("spindle.startup", version=__version__, dev_mode=settings.dev_mode)
    yield

    retention_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await retention_task
    await engine.dispose()
    logger.info("spindle.shutdown")


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SpindleError)
    async def handle_spindle_error(_request: Request, exc: SpindleError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error_code(type(exc)), "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": "http_error", "message": str(exc.detail), "detail": {}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "The request could not be validated.",
                "detail": {"errors": jsonable_encoder(exc.errors())},
            },
        )

    @app.exception_handler(ValidationError)
    async def handle_pydantic_validation_error(
        _request: Request, exc: ValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "The request could not be validated.",
                "detail": {"errors": jsonable_encoder(exc.errors())},
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("spindle.unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "An unexpected error occurred.",
                "detail": {},
            },
        )


def _mount_frontend(app: FastAPI) -> None:
    if not WEB_DIST_DIR.is_dir():
        return

    assets_dir = WEB_DIST_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise NotFoundError(f"Not found: /{full_path}")
        candidate = WEB_DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST_DIR / "index.html")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.started_at = time.monotonic()

    _register_exception_handlers(app)

    if settings.dev_mode:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router, prefix="/api/v1")
    _mount_frontend(app)

    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "spindle.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
