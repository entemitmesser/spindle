"""Backup & restore, per phase-09-hardening.md task 3: a zip of `spindle.db`
(via SQLite `VACUUM INTO`, never a raw file copy -- the live db is being
written under WAL, so copying its bytes directly can capture a torn/
inconsistent snapshot) plus the runtime config and any custom HandBrake
presets. Used by both `POST /system/backup` (in-memory, streamed back as a
download) and `spindle backup`/`spindle restore` (`cli.py`, offline).
"""

from __future__ import annotations

import asyncio
import io
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from spindle.config import RuntimeConfig
from spindle.errors import ConfigurationError

_DB_ENTRY_NAME = "spindle.db"
_SETTINGS_ENTRY_NAME = "settings.json"
_PRESETS_ENTRY_PREFIX = "presets/"


def _vacuum_into_sync(db_path: Path, dest_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("VACUUM INTO ?", (str(dest_path),))
    finally:
        conn.close()


async def build_backup_zip(
    *, db_path: Path, runtime_config: RuntimeConfig, presets_dir: Path
) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        vacuum_path = Path(tmp) / _DB_ENTRY_NAME
        await asyncio.to_thread(_vacuum_into_sync, db_path, vacuum_path)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(vacuum_path, _DB_ENTRY_NAME)
            zf.writestr(_SETTINGS_ENTRY_NAME, runtime_config.model_dump_json(indent=2))
            if presets_dir.is_dir():  # noqa: ASYNC240 -- backups are rare, presets are few
                for preset_file in sorted(presets_dir.glob("*.json")):  # noqa: ASYNC240
                    zf.write(preset_file, f"{_PRESETS_ENTRY_PREFIX}{preset_file.name}")
        return buffer.getvalue()


def restore_backup_sync(archive_path: Path, *, data_dir: Path) -> None:
    """Offline restore (`spindle restore`, task 3): overwrites
    `data_dir/spindle.db` and `data_dir/presets/` from `archive_path`. Only
    ever called with the server stopped -- there's no live-restore endpoint."""
    with zipfile.ZipFile(archive_path) as zf:
        names = zf.namelist()
        if _DB_ENTRY_NAME not in names:
            raise ConfigurationError(
                f"{archive_path} is not a Spindle backup (missing spindle.db)."
            )

        data_dir.mkdir(parents=True, exist_ok=True)
        with zf.open(_DB_ENTRY_NAME) as src, (data_dir / _DB_ENTRY_NAME).open("wb") as dst:
            shutil.copyfileobj(src, dst)

        presets_dir = data_dir / "presets"
        for name in names:
            if not name.startswith(_PRESETS_ENTRY_PREFIX) or name.endswith("/"):
                continue
            presets_dir.mkdir(parents=True, exist_ok=True)
            target = presets_dir / Path(name).name
            with zf.open(name) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
