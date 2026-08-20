from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI

from spindle.main import create_app


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SPINDLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPINDLE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("SPINDLE_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("SPINDLE_DEV_MODE", "true")
    yield create_app()
