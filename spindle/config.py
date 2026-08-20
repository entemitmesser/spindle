from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spindle.errors import ConfigurationError
from spindle.models.setting import Setting
from spindle.models.types import utc_now
from spindle.services.events import EventBus, EventIn


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPINDLE_", env_file=".env")

    data_dir: Path = Path("/data")
    work_dir: Path = Path("/work")
    library_dir: Path = Path("/library")
    host: str = "0.0.0.0"  # noqa: S104 -- trusted-LAN default, documented in architecture.md §8
    port: int = 8478
    log_level: str = "INFO"
    log_json: bool = False
    dev_mode: bool = False
    run_migrations: bool = True
    tmdb_api_key: SecretStr | None = None
    opensubtitles_api_key: SecretStr | None = None
    jellyfin_url: str | None = None
    jellyfin_token: SecretStr | None = None
    makemkv_key: SecretStr | None = None

    @model_validator(mode="after")
    def _ensure_dirs_writable(self) -> Settings:
        for path in (self.data_dir, self.work_dir):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ConfigurationError(f"Cannot create directory {path}: {exc}") from exc
            probe = path / ".spindle_write_test"
            try:
                probe.touch()
                probe.unlink()
            except OSError as exc:
                raise ConfigurationError(f"Directory {path} is not writable: {exc}") from exc
        return self

    def secret_values(self) -> list[str]:
        secrets = (
            self.tmdb_api_key,
            self.opensubtitles_api_key,
            self.jellyfin_token,
            self.makemkv_key,
        )
        return [s.get_secret_value() for s in secrets if s is not None]


def get_settings() -> Settings:
    return Settings()


class _RuntimeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RipConfig(_RuntimeModel):
    backend: Literal["makemkv", "handbrake"] = "makemkv"
    min_title_seconds: int = 900
    max_title_seconds: int = 21600
    title_selection: Literal["longest", "all", "ask"] = "longest"
    rip_extras: bool = False
    keep_raw_after_transcode: bool = False


class TranscodeConfig(_RuntimeModel):
    enabled: bool = True
    preset_movie: str = "Spindle 1080p H.265"
    preset_uhd: str = "Spindle 2160p H.265 10-bit"
    hardware_encoder: Literal["auto", "none", "qsv", "nvenc", "vaapi"] = "auto"
    quality: int = 22
    audio_langs: list[str] = ["deu", "eng"]
    keep_original_audio: bool = True
    workers: int = 1


class SubtitleConfig(_RuntimeModel):
    enabled: bool = True
    langs: list[str] = ["deu", "eng"]
    keep_forced_from_disc: bool = True
    external_provider: Literal["opensubtitles", "none"] = "opensubtitles"
    hearing_impaired: Literal["include", "exclude", "only"] = "exclude"


class LibraryConfig(_RuntimeModel):
    movies_dir: str = "Movies"
    series_dir: str = "Shows"
    movie_dir_template: str = "{title} ({year})"
    movie_file_template: str = "{title} ({year}) - {source}-{resolution}"
    series_dir_template: str = "{title} ({year})/Season {season:02d}"
    series_file_template: str = "{title} - S{season:02d}E{episode:02d}"
    include_tmdb_id: bool = False
    on_conflict: Literal["review", "overwrite", "keep_both"] = "review"


class BehaviourConfig(_RuntimeModel):
    auto_start: bool = True
    auto_eject: bool = True
    identify_confidence_threshold: float = 0.75
    min_free_bytes: int = 80 * 1024**3
    completed_retention_days: int = 7
    max_retries: int = 2


class NotificationConfig(_RuntimeModel):
    enabled: bool = False
    webhook_url: str | None = None
    notify_on: list[Literal["needs_review", "completed", "failed"]] = [
        "needs_review",
        "completed",
        "failed",
    ]


class RuntimeConfig(_RuntimeModel):
    rip: RipConfig = RipConfig()
    transcode: TranscodeConfig = TranscodeConfig()
    subtitles: SubtitleConfig = SubtitleConfig()
    library: LibraryConfig = LibraryConfig()
    behaviour: BehaviourConfig = BehaviourConfig()
    notifications: NotificationConfig = NotificationConfig()


def _deep_merge(base: dict[str, Any], partial: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in partial.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ConfigStore:
    """Caches the single `runtime` Setting row as a validated `RuntimeConfig`.

    `get()` returns a frozen, deep-copied snapshot so callers can never mutate
    the cache. `update()` deep-merges a partial dict onto the current config,
    validates it, persists it, and emits exactly one `settings.updated` event.
    """

    _KEY = "runtime"

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], event_bus: EventBus
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._cache: RuntimeConfig | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> RuntimeConfig:
        async with self._lock:
            if self._cache is None:
                self._cache = await self._load_or_seed()
            return self._cache.model_copy(deep=True)

    async def update(self, partial: dict[str, Any]) -> RuntimeConfig:
        async with self._lock:
            current = self._cache if self._cache is not None else await self._load_or_seed()
            merged = _deep_merge(current.model_dump(mode="json"), partial)
            updated = RuntimeConfig.model_validate(merged)
            await self._persist(updated)
            self._cache = updated
            snapshot = updated.model_copy(deep=True)

        await self._event_bus.emit(
            EventIn(type="settings.updated", data={"keys": list(partial.keys())})
        )
        return snapshot

    async def _load_or_seed(self) -> RuntimeConfig:
        async with self._session_factory() as session:
            row = await session.get(Setting, self._KEY)
            if row is not None:
                return RuntimeConfig.model_validate(row.value)

        config = RuntimeConfig()
        await self._persist(config)
        return config

    async def _persist(self, config: RuntimeConfig) -> None:
        async with self._session_factory() as session:
            row = await session.get(Setting, self._KEY)
            if row is None:
                session.add(Setting(key=self._KEY, value=config.model_dump(mode="json")))
            else:
                row.value = config.model_dump(mode="json")
                row.updated_at = utc_now()
            await session.commit()
