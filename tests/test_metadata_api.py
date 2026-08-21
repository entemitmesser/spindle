from __future__ import annotations

import httpx


async def test_search_metadata_movie(client: httpx.AsyncClient, running_app) -> None:
    async def fake_search_movie(query: str, *, year: int | None = None) -> list[dict]:
        return [{"id": 42, "title": "Dune", "release_date": "2021-01-01", "poster_path": "/x.jpg"}]

    running_app.state.tmdb.search_movie = fake_search_movie

    response = await client.get("/api/v1/metadata/search", params={"q": "dune"})

    assert response.status_code == 200
    items = response.json()["items"]
    assert items == [
        {
            "tmdb_id": 42,
            "title": "Dune",
            "year": 2021,
            "poster_url": "https://image.tmdb.org/t/p/w500/x.jpg",
            "media_kind": "movie",
        }
    ]


async def test_search_metadata_series(client: httpx.AsyncClient, running_app) -> None:
    async def fake_search_tv(query: str, *, year: int | None = None) -> list[dict]:
        return [{"id": 7, "name": "The Wire", "first_air_date": "2002-06-02"}]

    running_app.state.tmdb.search_tv = fake_search_tv

    response = await client.get(
        "/api/v1/metadata/search", params={"q": "wire", "media_kind": "series"}
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["media_kind"] == "series"
    assert items[0]["title"] == "The Wire"


async def test_get_metadata_detail_not_found(client: httpx.AsyncClient, running_app) -> None:
    async def fake_details(tmdb_id: int, *, media_kind: str = "movie") -> None:
        return None

    running_app.state.tmdb.details = fake_details

    response = await client.get("/api/v1/metadata/999999")

    assert response.status_code == 404


async def test_get_metadata_detail_returns_movie(client: httpx.AsyncClient, running_app) -> None:
    async def fake_details(tmdb_id: int, *, media_kind: str = "movie") -> dict:
        return {"id": tmdb_id, "title": "Dune", "release_date": "2021-01-01", "overview": "Spice."}

    running_app.state.tmdb.details = fake_details

    response = await client.get("/api/v1/metadata/42")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "tmdb_id": 42,
        "title": "Dune",
        "year": 2021,
        "poster_url": None,
        "overview": "Spice.",
    }
