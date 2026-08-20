from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from spindle.db import ALEMBIC_INI_PATH

_EXPECTED_TABLES = {"drive", "disc", "job", "task", "media_file", "event", "setting"}


def _table_names(db_path: Path) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}
    finally:
        con.close()


def test_upgrade_head_creates_every_table_and_downgrade_base_reverts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPINDLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPINDLE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("SPINDLE_LIBRARY_DIR", str(tmp_path / "library"))

    config = Config(str(ALEMBIC_INI_PATH))
    db_path = tmp_path / "data" / "spindle.db"

    command.upgrade(config, "head")
    assert _table_names(db_path) >= _EXPECTED_TABLES

    command.downgrade(config, "base")
    assert not (_EXPECTED_TABLES & _table_names(db_path))
