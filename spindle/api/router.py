from __future__ import annotations

from fastapi import APIRouter

from spindle.api.routes import auth, drives, events, jobs, metadata, queue, settings, system

router = APIRouter()
router.include_router(system.router, prefix="/system", tags=["system"])
router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(drives.router, prefix="/drives", tags=["drives"])
router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
router.include_router(queue.router, prefix="/queue", tags=["queue"])
router.include_router(settings.router, prefix="/settings", tags=["settings"])
router.include_router(metadata.router, prefix="/metadata", tags=["metadata"])
router.include_router(events.router, prefix="/events", tags=["events"])
