"""Phase 9 task 3: `POST /system/backup` and the underlying zip build/restore
round trip (`VACUUM INTO`, never a raw file copy)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spindle.config import ConfigStore, RuntimeConfig
from spindle.errors import ConfigurationError
from spindle.services.backup import build_backup_zip, restore_backup_sync
from tests.factories import make_job


async def test_build_backup_zip_contains_db_settings_and_presets(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    config_store: ConfigStore,
    tmp_path: Path,
) -> None:
    await make_job(session, title="Some Movie")
    db_path = tmp_path / "data" / "spindle.db"
    assert db_path.exists()

    presets_dir = tmp_path / "data" / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "custom.json").write_text("{}")

    runtime_config = await config_store.get()
    zip_bytes = await build_backup_zip(
        db_path=db_path, runtime_config=runtime_config, presets_dir=presets_dir
    )

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert "spindle.db" in names
        assert "settings.json" in names
        assert "presets/custom.json" in names
        settings = json.loads(zf.read("settings.json"))
        assert settings["behaviour"]["max_retries"] == RuntimeConfig().behaviour.max_retries


async def test_restore_backup_writes_db_and_presets(tmp_path: Path) -> None:
    src_db = tmp_path / "src.db"
    src_db.write_bytes(b"not a real sqlite file, just bytes for the round trip")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.write(src_db, "spindle.db")
        zf.writestr("presets/custom.json", '{"name": "custom"}')
    archive_path = tmp_path / "backup.zip"
    archive_path.write_bytes(buffer.getvalue())

    restore_dir = tmp_path / "restored"
    restore_backup_sync(archive_path, data_dir=restore_dir)

    assert (restore_dir / "spindle.db").read_bytes() == src_db.read_bytes()
    assert (restore_dir / "presets" / "custom.json").read_text() == '{"name": "custom"}'


async def test_restore_rejects_an_archive_without_spindle_db(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("settings.json", "{}")
    archive_path = tmp_path / "not-a-backup.zip"
    archive_path.write_bytes(buffer.getvalue())

    with pytest.raises(ConfigurationError):
        restore_backup_sync(archive_path, data_dir=tmp_path / "restored")


async def test_post_system_backup_returns_a_downloadable_zip(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await make_job(session, title="Some Movie")

    response = await client.post("/api/v1/system/backup")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert "spindle.db" in zf.namelist()
