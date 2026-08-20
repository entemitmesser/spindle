from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from spindle.errors import ConfigurationError


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
