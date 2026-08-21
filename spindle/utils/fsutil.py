"""Job work-directory layout, per `CLAUDE.md` §5 "Files on disk".

`JobContext.work_dir` is the work *root* (`$SPINDLE_WORK`); stages compute
their own kind-specific subdirectory from it so `raw/` and `transcode/` stay
siblings rather than nested under a per-job directory.
"""

from __future__ import annotations

from pathlib import Path


def job_dir_name(job_id: int) -> str:
    return f"job-{job_id:06d}"


def raw_dir(work_root: Path, job_id: int) -> Path:
    return work_root / "raw" / job_dir_name(job_id)


def transcode_dir(work_root: Path, job_id: int) -> Path:
    return work_root / "transcode" / job_dir_name(job_id)
