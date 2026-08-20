"""`JobContext`, per architecture.md §1: the object every `Stage.run` receives.

Stages mutate `job`/`disc` in place and never open their own DB session —
the (future) dispatcher owns the transaction boundary and commits after each
stage returns.
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
