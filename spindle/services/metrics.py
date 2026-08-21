"""`GET /metrics`, per phase-09-hardening.md task 4: jobs by status, task
duration/error counters by stage. Hand-rolled Prometheus text exposition
format rather than the `prometheus_client` dependency -- CLAUDE.md's stack
table is deliberately minimal, and this is a handful of `SELECT`s rendered as
text, not something that needs a metrics library.

`MediaFile` rows are never persisted (a long-standing gap tracked since phase
5 -- see docs/progress.md), so bytes-written and drive-busy-time metrics
aren't available; this exposes what the `job`/`task` tables actually have.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from spindle.models.job import Job, JobStatus
from spindle.models.task import Task, TaskKind, TaskStatus


async def render_prometheus_metrics(session: AsyncSession) -> str:
    lines: list[str] = []

    job_rows = (await session.execute(select(col(Job.status)))).scalars().all()
    job_counts: dict[JobStatus, int] = defaultdict(int)
    for status in job_rows:
        job_counts[status] += 1

    lines += [
        "# HELP spindle_jobs_total Number of jobs by status.",
        "# TYPE spindle_jobs_total gauge",
    ]
    for status in JobStatus:
        lines.append(f'spindle_jobs_total{{status="{status.value}"}} {job_counts.get(status, 0)}')

    tasks = (await session.execute(select(Task))).scalars().all()
    status_counts: dict[tuple[TaskKind, TaskStatus], int] = defaultdict(int)
    error_counts: dict[TaskKind, int] = defaultdict(int)
    duration_sum: dict[TaskKind, float] = defaultdict(float)
    duration_count: dict[TaskKind, int] = defaultdict(int)
    for task in tasks:
        status_counts[(task.kind, task.status)] += 1
        if task.status == TaskStatus.FAILED:
            error_counts[task.kind] += 1
        if task.started_at is not None and task.finished_at is not None:
            duration_sum[task.kind] += (task.finished_at - task.started_at).total_seconds()
            duration_count[task.kind] += 1

    lines += [
        "",
        "# HELP spindle_tasks_total Number of tasks by kind and status.",
        "# TYPE spindle_tasks_total counter",
    ]
    for kind in TaskKind:
        for task_status in TaskStatus:
            lines.append(
                f'spindle_tasks_total{{kind="{kind.value}",status="{task_status.value}"}} '
                f"{status_counts.get((kind, task_status), 0)}"
            )

    lines += [
        "",
        "# HELP spindle_task_errors_total Number of failed tasks by kind.",
        "# TYPE spindle_task_errors_total counter",
    ]
    for kind in TaskKind:
        lines.append(
            f'spindle_task_errors_total{{kind="{kind.value}"}} {error_counts.get(kind, 0)}'
        )

    lines += [
        "",
        "# HELP spindle_task_duration_seconds_sum Cumulative task duration in seconds, by kind.",
        "# TYPE spindle_task_duration_seconds_sum counter",
    ]
    for kind in TaskKind:
        lines.append(
            f'spindle_task_duration_seconds_sum{{kind="{kind.value}"}} '
            f"{duration_sum.get(kind, 0.0):.3f}"
        )

    lines += [
        "",
        "# HELP spindle_task_duration_seconds_count Number of tasks counted above, by kind.",
        "# TYPE spindle_task_duration_seconds_count counter",
    ]
    for kind in TaskKind:
        lines.append(
            f'spindle_task_duration_seconds_count{{kind="{kind.value}"}} '
            f"{duration_count.get(kind, 0)}"
        )

    return "\n".join(lines) + "\n"
