from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from spindle.adapters.tmdb import TmdbAdapter
from spindle.config import ConfigStore
from spindle.models.job import JobStatus, MediaKind
from spindle.models.task import TaskStatus
from spindle.pipeline.context import JobContext
from spindle.pipeline.stages import IdentifyStage
from spindle.services.events import EventIn
from tests.factories import make_disc, make_job


def _tmdb(tmp_path: Path, results: list[dict[str, Any]]) -> TmdbAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": results})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TmdbAdapter(client, api_key="test-key", cache_dir=tmp_path / "cache")


async def _make_context(
    session: AsyncSession, config_store: ConfigStore, tmp_path: Path, *, label: str
) -> JobContext:
    disc = await make_disc(session, label=label)
    job = await make_job(session, disc_id=disc.id, source_path=None)

    async def emit(event: EventIn) -> None:
        return None

    return JobContext(
        job=job,
        disc=disc,
        drive=None,
        work_dir=tmp_path,
        config=await config_store.get(),
        emit=emit,
        logger=structlog.get_logger(__name__),
    )


async def test_can_skip_when_job_already_has_tmdb_id(
    session: AsyncSession, config_store: ConfigStore, tmp_path: Path
) -> None:
    ctx = await _make_context(session, config_store, tmp_path, label="INCEPTION")
    ctx.job.tmdb_id = 27205
    stage = IdentifyStage(_tmdb(tmp_path, []))
    assert await stage.can_skip(ctx) is True


async def test_cannot_skip_when_job_has_no_tmdb_id(
    session: AsyncSession, config_store: ConfigStore, tmp_path: Path
) -> None:
    ctx = await _make_context(session, config_store, tmp_path, label="INCEPTION")
    stage = IdentifyStage(_tmdb(tmp_path, []))
    assert await stage.can_skip(ctx) is False


async def test_run_accepts_a_confident_match_and_updates_the_job(
    session: AsyncSession, config_store: ConfigStore, tmp_path: Path
) -> None:
    ctx = await _make_context(session, config_store, tmp_path, label="INCEPTION")
    tmdb = _tmdb(
        tmp_path,
        [
            {
                "id": 27205,
                "title": "Inception",
                "original_title": "Inception",
                "release_date": "2010-07-16",
                "popularity": 95.0,
                "poster_path": "/x.jpg",
            }
        ],
    )
    stage = IdentifyStage(tmdb)

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert result.meta == {"accepted": True}
    assert ctx.job.title == "Inception"
    assert ctx.job.tmdb_id == 27205
    assert ctx.job.media_kind == MediaKind.MOVIE
    assert ctx.job.status != JobStatus.NEEDS_REVIEW


async def test_run_parks_low_confidence_match_in_needs_review_with_candidates(
    session: AsyncSession, config_store: ConfigStore, tmp_path: Path
) -> None:
    ctx = await _make_context(session, config_store, tmp_path, label="SOME_OBSCURE_LABEL")
    tmdb = _tmdb(
        tmp_path,
        [
            {"id": 1, "title": "Totally Unrelated Film", "release_date": "1975-01-01"},
            {"id": 2, "title": "Another Different Movie", "release_date": "1960-01-01"},
        ],
    )
    stage = IdentifyStage(tmdb)

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert result.meta == {"accepted": False}
    assert ctx.job.status == JobStatus.NEEDS_REVIEW
    assert ctx.job.tmdb_id is None
    assert len(ctx.job.meta["candidates"]) == 2


async def test_run_with_no_usable_label_parks_for_review_without_calling_tmdb(
    session: AsyncSession, config_store: ConfigStore, tmp_path: Path
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tmdb = TmdbAdapter(client, api_key="test-key", cache_dir=tmp_path / "cache")

    ctx = await _make_context(session, config_store, tmp_path, label="___")
    stage = IdentifyStage(tmdb)

    result = await stage.run(ctx)

    assert result.status == TaskStatus.SUCCEEDED
    assert ctx.job.status == JobStatus.NEEDS_REVIEW
    assert calls == 0
