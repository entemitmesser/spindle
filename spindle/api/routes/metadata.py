"""`/api/v1/metadata`, per architecture.md §4 — the TMDB proxy behind the
job "fix this" dialog. A plain user-initiated lookup, so it calls
`TmdbAdapter` directly rather than `services/identifier.py` (which scores
candidates against a *disc*, not a free-text query)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from spindle.adapters.tmdb import TmdbAdapter
from spindle.errors import NotFoundError
from spindle.models.job import MediaKind
from spindle.schemas.metadata import (
    MetadataDetail,
    MetadataSearchResponse,
    detail_from_tmdb,
    search_result_from_tmdb,
)

router = APIRouter()


@router.get("/search", operation_id="search_metadata")
async def search_metadata(
    request: Request, q: str, media_kind: MediaKind = MediaKind.MOVIE
) -> MetadataSearchResponse:
    tmdb: TmdbAdapter = request.app.state.tmdb
    if media_kind == MediaKind.SERIES:
        results = await tmdb.search_tv(q)
        kind = MediaKind.SERIES
    else:
        results = await tmdb.search_movie(q)
        kind = MediaKind.MOVIE

    items = [search_result_from_tmdb(r, media_kind=kind) for r in results]
    return MetadataSearchResponse(items=[i for i in items if i is not None])


@router.get("/{tmdb_id}", operation_id="get_metadata")
async def get_metadata(
    request: Request, tmdb_id: int, media_kind: MediaKind = MediaKind.MOVIE
) -> MetadataDetail:
    tmdb: TmdbAdapter = request.app.state.tmdb
    kind = "tv" if media_kind == MediaKind.SERIES else "movie"
    details = await tmdb.details(tmdb_id, media_kind=kind)
    detail = detail_from_tmdb(details) if details is not None else None
    if detail is None:
        raise NotFoundError(f"No TMDB {kind} found for id {tmdb_id}.")
    return detail
