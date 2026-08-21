"""OpenSubtitles REST v1 adapter, per phase-05-metadata-subtitles.md task 2.

Anonymous API-key use works for `search()` but has a low download quota;
`login()` only runs when credentials are configured (raising the quota) and
caches the JWT for the adapter's lifetime. Never retries on 429/406 — the
phase doc's gotcha is explicit that aggressive retries get the API key
banned — those responses become `OpenSubtitlesQuotaExceededError` so
`SubtitleStage` can skip the language cleanly instead of failing the job.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from spindle.__about__ import __version__
from spindle.errors import AdapterError

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.opensubtitles.com/api/v1"
_QUOTA_STATUS_CODES = frozenset({406, 429})


class OpenSubtitlesError(AdapterError):
    """An OpenSubtitles request failed."""


class OpenSubtitlesQuotaExceededError(OpenSubtitlesError):
    """Download quota exhausted or rate-limited — not a hard failure; the
    caller should skip this language/candidate rather than fail the job."""


class OpenSubtitlesAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str | None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._username = username
        self._password = password
        self._jwt: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def login(self) -> None:
        """No-op unless credentials are configured or we're already logged
        in — anonymous key-only use is a valid, lower-quota mode."""
        if not self._api_key or not self._username or not self._password:
            return
        if self._jwt is not None:
            return
        data, _response = await self._request_raw(
            "POST", "/login", json={"username": self._username, "password": self._password}
        )
        token = data.get("token")
        if isinstance(token, str):
            self._jwt = token

    async def search(
        self,
        *,
        languages: list[str],
        moviehash: str | None = None,
        tmdb_id: int | None = None,
        year: int | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Prefer `moviehash` + `tmdb_id`, fall back to `query`. Returns the
        raw `data` list from the API — ranking is `services/subtitles.py`'s
        job, not the adapter's."""
        if not self._api_key or not languages or not (moviehash or tmdb_id or query):
            return []

        params: dict[str, Any] = {"languages": ",".join(languages)}
        if moviehash:
            params["moviehash"] = moviehash
        if tmdb_id is not None:
            params["tmdb_id"] = tmdb_id
        if year is not None:
            params["year"] = year
        if query:
            params["query"] = query

        data, _response = await self._request_raw("GET", "/subtitles", params=params)
        return list(data.get("data", []))

    async def download(self, file_id: int) -> bytes:
        """Returns the subtitle file's bytes. The `/download` endpoint
        returns a short-lived link, not the file itself (phase doc gotcha:
        cache the file, not the link) — that link is fetched immediately and
        discarded."""
        if not self._api_key:
            raise OpenSubtitlesError("No OpenSubtitles API key configured.")

        data, _response = await self._request_raw("POST", "/download", json={"file_id": file_id})
        link = data.get("link")
        if not link:
            message = str(data.get("message") or "OpenSubtitles did not return a download link.")
            if "quota" in message.lower() or "limit" in message.lower():
                raise OpenSubtitlesQuotaExceededError(message, detail=data)
            raise OpenSubtitlesError(message, detail=data)

        try:
            response = await self._client.get(link)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenSubtitlesError(f"Could not download subtitle file: {exc}") from exc
        return response.content

    def _headers(self) -> dict[str, str]:
        headers = {
            "Api-Key": self._api_key or "",
            "User-Agent": f"Spindle/{__version__}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._jwt:
            headers["Authorization"] = f"Bearer {self._jwt}"
        return headers

    async def _request_raw(
        self, method: str, path: str, **kwargs: Any
    ) -> tuple[dict[str, Any], httpx.Response]:
        try:
            response = await self._client.request(
                method, f"{_BASE_URL}{path}", headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise OpenSubtitlesError(f"OpenSubtitles request failed: {path}") from exc

        body: dict[str, Any]
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code in _QUOTA_STATUS_CODES:
            message = str(body.get("message") or "OpenSubtitles quota or rate limit hit.")
            raise OpenSubtitlesQuotaExceededError(
                message, detail={**body, "retry_after": response.headers.get("retry-after")}
            )
        if response.status_code >= 400:
            raise OpenSubtitlesError(
                f"OpenSubtitles request failed with {response.status_code}: {path}",
                detail={"body": response.text[:500]},
            )
        return body, response
