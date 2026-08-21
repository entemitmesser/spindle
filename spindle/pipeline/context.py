"""`JobContext`, per architecture.md §1: the object every `Stage.run` receives.

Stages mutate `job`/`disc` in place and never open their own DB session —
the (future) dispatcher owns the transaction boundary and commits after each
stage returns.

`library_dir`/`data_dir` are optional (default `None`): only `OrganizeStage`
(phase 6) needs the library root and the `$SPINDLE_DATA` root (for its
`data_dir/replaced/` quarantine on an `overwrite` conflict) — every other
stage works purely out of `work_dir`. Optional with a `None` default keeps
every existing hand-built `JobContext` in the test suite compiling.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

from spindle.config import RuntimeConfig
from spindle.models.disc import Disc
from spindle.models.drive import Drive
from spindle.models.job import Job
from spindle.services.events import EventIn

EmitFn = Callable[[EventIn], Awaitable[None]]


@dataclass
class JobContext:
    job: Job
    disc: Disc | None
    drive: Drive | None
    work_dir: Path
    config: RuntimeConfig
    emit: EmitFn
    logger: structlog.stdlib.BoundLogger
    library_dir: Path | None = None
    data_dir: Path | None = None
