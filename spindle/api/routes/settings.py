from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from spindle.config import ConfigStore
from spindle.schemas.settings import SettingsResponse

router = APIRouter()


@router.get("")
async def get_settings_endpoint(request: Request) -> SettingsResponse:
    store: ConfigStore = request.app.state.config_store
    return await store.get()


@router.put("")
async def update_settings_endpoint(request: Request, partial: dict[str, Any]) -> SettingsResponse:
    store: ConfigStore = request.app.state.config_store
    return await store.update(partial)
