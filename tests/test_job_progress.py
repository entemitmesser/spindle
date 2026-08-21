"""`spindle/pipeline/stages.py`'s whole-job progress weighting, per
phase-07-api-realtime.md task 1's acceptance criterion: "Whole-job progress
is monotonic across a simulated full pipeline run."
"""

from __future__ import annotations

from spindle.models.task import Task, TaskKind, TaskStatus
from spindle.pipeline.stages import (
    STAGE_WEIGHTS,
    current_task_eta_seconds,
    current_task_kind,
    job_progress,
)


def _tasks(**overrides: TaskStatus) -> list[Task]:
    return [
        Task(job_id=1, kind=kind, status=overrides.get(kind, TaskStatus.PENDING))
        for kind in TaskKind
    ]


def test_stage_weights_sum_to_one() -> None:
    assert sum(STAGE_WEIGHTS.values()) == 1.0
    assert set(STAGE_WEIGHTS) == set(TaskKind)


def test_job_progress_is_zero_for_a_fresh_job() -> None:
    assert job_progress(_tasks()) == 0.0


def test_job_progress_is_one_when_every_task_is_terminal() -> None:
    tasks = _tasks(**dict.fromkeys(TaskKind, TaskStatus.SUCCEEDED))
    assert job_progress(tasks) == 1.0


def test_job_progress_counts_a_skipped_task_as_full_credit() -> None:
    tasks = _tasks(**dict.fromkeys(TaskKind, TaskStatus.SKIPPED))
    assert job_progress(tasks) == 1.0


def test_job_progress_is_monotonic_across_a_simulated_full_pipeline_run() -> None:
    """Walks every `TaskKind` through pending -> running (0%, 50%, 100%) ->
    succeeded, in pipeline order, and asserts the whole-job progress never
    decreases at any step — the dashboard bar must never visibly rewind."""
    tasks = {kind: Task(job_id=1, kind=kind, status=TaskStatus.PENDING) for kind in TaskKind}
    progress_values: list[float] = [job_progress(tasks.values())]

    for kind in TaskKind:
        task = tasks[kind]
        task.status = TaskStatus.RUNNING
        for fraction in (0.0, 0.5, 1.0):
            task.progress = fraction
            progress_values.append(job_progress(tasks.values()))
        task.status = TaskStatus.SUCCEEDED
        task.progress = 1.0
        progress_values.append(job_progress(tasks.values()))

    assert progress_values == sorted(progress_values)
    assert progress_values[0] == 0.0
    assert progress_values[-1] == 1.0


def test_current_task_kind_is_first_non_terminal_task_in_pipeline_order() -> None:
    tasks = _tasks(identify=TaskStatus.SUCCEEDED, rip=TaskStatus.RUNNING)
    assert current_task_kind(tasks) == TaskKind.RIP


def test_current_task_kind_is_none_once_every_task_is_terminal() -> None:
    tasks = _tasks(**dict.fromkeys(TaskKind, TaskStatus.SUCCEEDED))
    assert current_task_kind(tasks) is None


def test_current_task_eta_seconds_reads_the_running_task() -> None:
    tasks = _tasks(identify=TaskStatus.SUCCEEDED, rip=TaskStatus.RUNNING)
    tasks[1].eta_seconds = 120
    assert current_task_eta_seconds(tasks) == 120


def test_current_task_eta_seconds_is_none_without_a_running_task() -> None:
    assert current_task_eta_seconds(_tasks()) is None
