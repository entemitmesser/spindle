"""TMDB search/details, per architecture.md §7: cache 7 days, degrade to empty
results (never raise) when no API key is configured so identify can fall back
to `needs_review` with a clear message instead of crashing.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

from spindle.errors import AdapterError

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.themoviedb.org/3"
_CACHE_TTL_SECONDS = 7 * 24 * 3600


class TmdbAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str | None,
        cache_dir: Path,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._cache_dir = cache_dir

    async def search_movie(self, query: str, *, year: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query}
        if year is not None:
            params["year"] = year
        result = await self._get("/search/movie", params)
        return list(result.get("results", [])) if result else []

    async def search_tv(self, query: str, *, year: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query}
        if year is not None:
            params["first_air_date_year"] = year
        result = await self._get("/search/tv", params)
        return list(result.get("results", [])) if result else []

    async def details(self, tmdb_id: int, *, media_kind: str = "movie") -> dict[str, Any] | None:
        return await self._get(f"/{media_kind}/{tmdb_id}", {})

    async def external_ids(
        self, tmdb_id: int, *, media_kind: str = "movie"
    ) -> dict[str, Any] | None:
        return await self._get(f"/{media_kind}/{tmdb_id}/external_ids", {})

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if not self._api_key:
            logger.info("tmdb.no_api_key", path=path)
            return None

        cache_key = self._cache_key(path, params)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        try:
            response = await self._client.get(
                f"{_BASE_URL}{path}",
                params={**params, "api_key": self._api_key},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdapterError(f"TMDB request failed: {path}", detail={"error": str(exc)}) from exc

        data: dict[str, Any] = response.json()
        self._write_cache(cache_key, data)
        return data

    def _cache_key(self, path: str, params: dict[str, Any]) -> str:
        payload = json.dumps({"path": path, "params": params}, sort_keys=True)
        return hashlib.sha1(payload.encode(), usedforsecurity=False).hexdigest()  # noqa: S324

    def _cache_path(self, cache_key: str) -> Path:
        return self._cache_dir / f"{cache_key}.json"

    def _read_cache(self, cache_key: str) -> dict[str, Any] | None:
        path = self._cache_path(cache_key)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            envelope = json.loads(raw)
        except ValueError:
            return None
        if time.time() - envelope["cached_at"] > _CACHE_TTL_SECONDS:
            return None
        data: dict[str, Any] = envelope["data"]
        return data

    def _write_cache(self, cache_key: str, data: dict[str, Any]) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        envelope = {"cached_at": time.time(), "data": data}
        self._cache_path(cache_key).write_text(json.dumps(envelope), encoding="utf-8")
