from __future__ import annotations

from fastapi import APIRouter

from spindle.api.routes import system

router = APIRouter()
router.include_router(system.router, prefix="/system", tags=["system"])
