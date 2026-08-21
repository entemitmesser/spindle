"""Jellyfin library-refresh adapter, per phase-06-library-jellyfin.md task 3.

Everything here is best-effort: architecture.md §7 is explicit that Jellyfin
being down must never fail a completed rip. Every method degrades to a
falsy/empty return instead of raising when the adapter isn't configured
(`base_url`/`token` missing) or a request fails.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_FIND_ITEM_RETRIES = 3
_FIND_ITEM_RETRY_DELAY_SECONDS = 2.0


class JellyfinAdapter:
    def __init__(
        self, client: httpx.AsyncClient, *, base_url: str | None, token: str | None
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/") if base_url else None
        self._token = token

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._token)

    async def ping(self) -> bool:
        """`GET /System/Info/Public`, used by `POST /settings/test/jellyfin`."""
        if not self.is_configured:
            return False
        try:
            response = await self._client.get(
                f"{self._base_url}/System/Info/Public", headers=self._headers()
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    async def refresh_library(self, path_hint: str | None = None) -> bool:
        """Refreshes the library folder containing `path_hint` if it can be
        resolved via `/Library/VirtualFolders`; otherwise refreshes
        everything. Returns whether the refresh request was accepted, not
        whether the scan has finished (Jellyfin scans asynchronously)."""
        if not self.is_configured:
            return False

        library_id = await self._resolve_library_id(path_hint) if path_hint else None
        path = f"/Items/{library_id}/Refresh" if library_id else "/Library/Refresh"
        try:
            response = await self._client.post(f"{self._base_url}{path}", headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("jellyfin.refresh_failed", path=path)
            return False
        return True

    async def find_item(
        self,
        tmdb_id: int,
        *,
        retries: int = _FIND_ITEM_RETRIES,
        retry_delay_seconds: float = _FIND_ITEM_RETRY_DELAY_SECONDS,
    ) -> dict[str, Any] | None:
        """Confirms an item appeared after a refresh, retrying with a fixed
        delay since scanning is async. `None` after exhausting retries is a
        `system.warning` for the caller to emit, never a job failure."""
        if not self.is_configured:
            return None

        for attempt in range(retries):
            items = await self._search_by_tmdb_id(tmdb_id)
            if items:
                return items[0]
            if attempt < retries - 1:
                await asyncio.sleep(retry_delay_seconds)
        return None

    async def _search_by_tmdb_id(self, tmdb_id: int) -> list[dict[str, Any]]:
        try:
            response = await self._client.get(
                f"{self._base_url}/Items",
                params={"AnyProviderIdEquals": f"tmdb.{tmdb_id}", "Recursive": "true"},
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        body: dict[str, Any] = response.json()
        return list(body.get("Items", []))

    async def _resolve_library_id(self, path_hint: str) -> str | None:
        try:
            response = await self._client.get(
                f"{self._base_url}/Library/VirtualFolders", headers=self._headers()
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        folders: list[dict[str, Any]] = response.json()
        for folder in folders:
            locations = folder.get("Locations") or []
            if any(path_hint.startswith(location) for location in locations):
                item_id = folder.get("ItemId")
                return str(item_id) if item_id else None
        return None

    def _headers(self) -> dict[str, str]:
        return {"X-Emby-Token": self._token or "", "Accept": "application/json"}
