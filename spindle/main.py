from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
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
from spindle.adapters.ffmpeg import FfmpegAdapter
from spindle.adapters.handbrake import HandBrakeAdapter
from spindle.adapters.jellyfin import JellyfinAdapter
from spindle.adapters.makemkv import MakeMkvAdapter
from spindle.adapters.mkvpropedit import MkvPropEditAdapter
from spindle.adapters.opensubtitles import OpenSubtitlesAdapter
from spindle.adapters.optical import OpticalAdapter
from spindle.adapters.tmdb import TmdbAdapter
from spindle.api.router import router as api_router
from spindle.config import ConfigStore, Settings, get_settings
from spindle.db import create_engine, create_session_factory, init_schema
from spindle.errors import NotFoundError, SpindleError, error_code
from spindle.models.task import TaskKind
from spindle.pipeline.stages import (
    IdentifyStage,
    NotifyStage,
    OrganizeStage,
    RipStage,
    Stage,
    SubtitleStage,
    TranscodeStage,
)
from spindle.services.cleanup import CleanupService
from spindle.services.detector import DriveWatcher
from spindle.services.events import EventBus
from spindle.services.metadata import MetadataService
from spindle.services.notifier import NotificationService
from spindle.services.queue import Dispatcher
from spindle.utils.logging import configure_logging

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)

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
    config_store = ConfigStore(session_factory, event_bus)
    app.state.config_store = config_store

    http_client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
    app.state.http_client = http_client
    _build_adapters_and_services(app, settings, http_client)

    cleanup_service = CleanupService(
        session_factory,
        event_bus,
        config_store,
        work_dir=settings.work_dir,
        library_dir=settings.library_dir,
    )
    app.state.cleanup_service = cleanup_service
    cleanup_task = asyncio.create_task(cleanup_service.run_loop())

    dispatcher = Dispatcher(
        _build_stages(app),
        session_factory,
        event_bus,
        config_store,
        work_dir=settings.work_dir,
        data_dir=settings.data_dir,
        library_dir=settings.library_dir,
    )
    app.state.dispatcher = dispatcher

    drive_watcher = DriveWatcher(
        app.state.optical, session_factory, event_bus, config_store, app.state.tmdb
    )
    app.state.drive_watcher = drive_watcher

    background_tasks: list[asyncio.Task[None]] = [cleanup_task]
    if settings.run_background_workers:
        background_tasks.append(asyncio.create_task(dispatcher.run()))
        background_tasks.append(asyncio.create_task(drive_watcher.run()))

    logger.info("spindle.startup", version=__version__, dev_mode=settings.dev_mode)
    yield

    if settings.run_background_workers:
        await dispatcher.stop()
    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await http_client.aclose()
    await engine.dispose()
    logger.info("spindle.shutdown")


def _build_adapters_and_services(
    app: FastAPI, settings: Settings, http_client: httpx.AsyncClient
) -> None:
    """Every external tool/API gets exactly one adapter instance on
    `app.state`, per CLAUDE.md rule 3 — routes and stages alike borrow these
    rather than constructing their own."""
    app.state.makemkv = MakeMkvAdapter()
    app.state.handbrake = HandBrakeAdapter()
    app.state.ffmpeg = FfmpegAdapter()
    app.state.optical = OpticalAdapter()
    app.state.mkvpropedit = MkvPropEditAdapter()
    app.state.tmdb = TmdbAdapter(
        http_client,
        api_key=settings.tmdb_api_key.get_secret_value() if settings.tmdb_api_key else None,
        cache_dir=settings.data_dir / "cache" / "tmdb",
    )
    app.state.opensubtitles = OpenSubtitlesAdapter(
        http_client,
        api_key=(
            settings.opensubtitles_api_key.get_secret_value()
            if settings.opensubtitles_api_key
            else None
        ),
    )
    app.state.jellyfin = JellyfinAdapter(
        http_client,
        base_url=settings.jellyfin_url,
        token=settings.jellyfin_token.get_secret_value() if settings.jellyfin_token else None,
    )
    app.state.metadata = MetadataService(app.state.mkvpropedit, app.state.tmdb)
    app.state.notifier = NotificationService(http_client)


def _build_stages(app: FastAPI) -> dict[TaskKind, Stage]:
    state = app.state
    return {
        TaskKind.IDENTIFY: IdentifyStage(state.tmdb),
        TaskKind.RIP: RipStage(state.makemkv, state.handbrake, state.ffmpeg, state.optical),
        TaskKind.TRANSCODE: TranscodeStage(
            state.handbrake, state.ffmpeg, user_preset_dir=state.settings.data_dir / "presets"
        ),
        TaskKind.SUBTITLES: SubtitleStage(state.opensubtitles, state.ffmpeg, state.metadata),
        TaskKind.ORGANIZE: OrganizeStage(state.ffmpeg, state.jellyfin),
        TaskKind.NOTIFY: NotifyStage(state.notifier),
    }


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
