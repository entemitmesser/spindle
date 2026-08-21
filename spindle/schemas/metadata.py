"""`GET /metadata/*` — the TMDB proxy behind the job "fix this" dialog
(architecture.md §4). Thin remaps of TMDB's search/details payloads; the
scoring logic itself stays in `services/identifier.py`, which these routes
don't call (identification is disc-driven, this is a plain user-initiated
lookup)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from spindle.models.job import MediaKind

_POSTER_BASE_URL = "https://image.tmdb.org/t/p/w500"


class MetadataSearchResult(BaseModel):
    tmdb_id: int
    title: str
    year: int | None
    poster_url: str | None
    media_kind: MediaKind


class MetadataSearchResponse(BaseModel):
    items: list[MetadataSearchResult]


class MetadataDetail(BaseModel):
    tmdb_id: int
    title: str
    year: int | None
    poster_url: str | None
    overview: str | None


def _title(candidate: dict[str, Any]) -> str:
    return str(candidate.get("title") or candidate.get("name") or "")


def _year(candidate: dict[str, Any]) -> int | None:
    date = candidate.get("release_date") or candidate.get("first_air_date") or ""
    return int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None


def _poster_url(candidate: dict[str, Any]) -> str | None:
    path = candidate.get("poster_path")
    return f"{_POSTER_BASE_URL}{path}" if path else None


def search_result_from_tmdb(
    candidate: dict[str, Any], *, media_kind: MediaKind
) -> MetadataSearchResult | None:
    tmdb_id = candidate.get("id")
    if tmdb_id is None:
        return None
    return MetadataSearchResult(
        tmdb_id=int(tmdb_id),
        title=_title(candidate),
        year=_year(candidate),
        poster_url=_poster_url(candidate),
        media_kind=media_kind,
    )


def detail_from_tmdb(details: dict[str, Any]) -> MetadataDetail | None:
    tmdb_id = details.get("id")
    if tmdb_id is None:
        return None
    return MetadataDetail(
        tmdb_id=int(tmdb_id),
        title=_title(details),
        year=_year(details),
        poster_url=_poster_url(details),
        overview=details.get("overview"),
    )
