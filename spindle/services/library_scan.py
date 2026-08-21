"""`spindle scan-library`, per phase-09-hardening.md task 3: adopt files
already present in the library into job history so a manually-copied or
pre-Spindle library isn't invisible to the UI. Best-effort only -- the title
is guessed from the containing directory name, not recovered via
`naming.py`'s templates in reverse (there's no reliable way to invert an
arbitrary user template); good enough to make the file show up, not to
re-derive its TMDB identity.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from spindle.models.job import Job, JobStatus, MediaKind
from spindle.models.task import Task, TaskKind, TaskStatus
from spindle.models.types import utc_now


async def scan_library(
    session_factory: async_sessionmaker[AsyncSession], *, library_dir: Path
) -> int:
    if not library_dir.is_dir():  # noqa: ASYNC240 -- `spindle scan-library` runs offline, no loop to block
        return 0

    async with session_factory() as session:
        known = set((await session.execute(select(col(Job.source_path)))).scalars().all())

        adopted = 0
        for video_path in sorted(library_dir.rglob("*.mkv")):  # noqa: ASYNC240 -- see above
            if str(video_path) in known:
                continue

            is_series = video_path.parent.name.lower().startswith("season ")
            title_dir = video_path.parent.parent if is_series else video_path.parent
            now = utc_now()
            job = Job(
                source_path=str(video_path),
                status=JobStatus.COMPLETED,
                media_kind=MediaKind.SERIES if is_series else MediaKind.MOVIE,
                title=title_dir.name,
                created_at=now,
                started_at=now,
                finished_at=now,
                meta={"adopted": True},
            )
            session.add(job)
            await session.flush()
            assert job.id is not None  # noqa: S101 -- just flushed, always has an id
            for kind in TaskKind:
                session.add(
                    Task(
                        job_id=job.id,
                        kind=kind,
                        status=TaskStatus.SKIPPED,
                        progress=1.0,
                        message="Adopted by `spindle scan-library`.",
                    )
                )
            adopted += 1

        await session.commit()
        return adopted
