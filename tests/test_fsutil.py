from __future__ import annotations

from pathlib import Path

from spindle.utils.fsutil import job_dir_name, raw_dir, transcode_dir


def test_job_dir_name_pads_to_six_digits() -> None:
    assert job_dir_name(123) == "job-000123"


def test_raw_dir_and_transcode_dir_are_siblings_under_work_root() -> None:
    root = Path("/work")
    assert raw_dir(root, 123) == Path("/work/raw/job-000123")
    assert transcode_dir(root, 123) == Path("/work/transcode/job-000123")
