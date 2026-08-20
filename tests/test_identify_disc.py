from __future__ import annotations

from pathlib import Path

import httpx

from spindle.adapters.tmdb import TmdbAdapter
from spindle.models.job import MediaKind
from spindle.services.identifier import identify_disc
from spindle.utils.naming import DiscLabelHints


def _tmdb(
    tmp_path: Path,
    results: list[dict[str, object]],
    *,
    tv_results: list[dict[str, object]] | None = None,
) -> TmdbAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/tv":
            return httpx.Response(200, json={"results": tv_results or []})
        return httpx.Response(200, json={"results": results})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return TmdbAdapter(client, api_key="test-key", cache_dir=tmp_path / "cache")


async def test_correct_match_on_messy_label_is_accepted(tmp_path: Path) -> None:
    tmdb = _tmdb(
        tmp_path,
        [
            {
                "id": 562,
                "title": "Die Hard",
                "original_title": "Die Hard",
                "release_date": "1988-07-15",
                "popularity": 95.0,
                "poster_path": "/abc.jpg",
            }
        ],
    )
    outcome = await identify_disc(
        tmdb,
        normalized_title="Die Harde",
        hints=DiscLabelHints(),
        disc_runtime_minutes=None,
        confidence_threshold=0.75,
    )
    assert outcome.accepted is True
    assert outcome.title == "Die Hard"
    assert outcome.tmdb_id == 562
    assert outcome.year == 1988
    assert outcome.media_kind == MediaKind.MOVIE
    assert outcome.poster_url == "https://image.tmdb.org/t/p/w500/abc.jpg"


async def test_ambiguous_label_needs_review_with_candidates(tmp_path: Path) -> None:
    tmdb = _tmdb(
        tmp_path,
        [
            {
                "id": 1,
                "title": "Totally Unrelated Film",
                "release_date": "1975-01-01",
                "popularity": 1.0,
            },
            {
                "id": 2,
                "title": "Another Different Movie",
                "release_date": "1960-01-01",
                "popularity": 1.0,
            },
        ],
    )
    outcome = await identify_disc(
        tmdb,
        normalized_title="Some Obscure Label",
        hints=DiscLabelHints(),
        disc_runtime_minutes=None,
        confidence_threshold=0.75,
    )
    assert outcome.accepted is False
    assert outcome.title is None
    assert outcome.tmdb_id is None
    assert len(outcome.candidates) == 2
    assert {c["tmdb_id"] for c in outcome.candidates} == {1, 2}


async def test_no_results_needs_review_with_no_candidates(tmp_path: Path) -> None:
    tmdb = _tmdb(tmp_path, [])
    outcome = await identify_disc(
        tmdb,
        normalized_title="Nonexistent Movie Title",
        hints=DiscLabelHints(),
        disc_runtime_minutes=None,
        confidence_threshold=0.75,
    )
    assert outcome.accepted is False
    assert outcome.candidates == []


async def test_disc_number_hint_forces_tv_search_and_needs_review(tmp_path: Path) -> None:
    tmdb = _tmdb(
        tmp_path,
        results=[],
        tv_results=[
            {
                "id": 1396,
                "name": "Breaking Bad",
                "first_air_date": "2008-01-20",
                "popularity": 200.0,
            }
        ],
    )
    outcome = await identify_disc(
        tmdb,
        normalized_title="Breaking Bad",
        hints=DiscLabelHints(disc_number=2),
        disc_runtime_minutes=None,
        confidence_threshold=0.75,
    )
    assert outcome.media_kind == MediaKind.SERIES
    # Even a perfect label/popularity match is parked for review — automatic
    # episode mapping is out of scope, per the phase doc's TV gotcha.
    assert outcome.accepted is False
    assert outcome.candidates[0]["tmdb_id"] == 1396


async def test_movie_search_falls_back_to_tv_when_empty(tmp_path: Path) -> None:
    tmdb = _tmdb(
        tmp_path,
        results=[],
        tv_results=[
            {"id": 99, "name": "Some Show", "first_air_date": "2010-01-01", "popularity": 10.0}
        ],
    )
    outcome = await identify_disc(
        tmdb,
        normalized_title="Some Show",
        hints=DiscLabelHints(),
        disc_runtime_minutes=None,
        confidence_threshold=0.75,
    )
    assert outcome.media_kind == MediaKind.SERIES
    assert outcome.candidates[0]["tmdb_id"] == 99
