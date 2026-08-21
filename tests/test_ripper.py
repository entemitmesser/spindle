from __future__ import annotations

from spindle.config import RipConfig
from spindle.models.job import MediaKind
from spindle.services.ripper import TitleCandidate, select_titles

_GB = 1024**3


def _candidate(
    title_id: int, *, duration: int, chapters: int = 20, size: int = 10 * _GB
) -> TitleCandidate:
    return TitleCandidate(
        title_id=title_id, duration_seconds=duration, chapter_count=chapters, size_bytes=size
    )


def test_filters_out_titles_outside_the_configured_duration_window() -> None:
    config = RipConfig(min_title_seconds=900, max_title_seconds=21600)
    candidates = [
        _candidate(1, duration=30),  # menu loop, too short
        _candidate(2, duration=7200),
        _candidate(3, duration=30 * 3600),  # bonus disc image, too long
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert [c.title_id for c in result.selected] == [2]


def test_near_duplicate_durations_keep_the_one_with_more_chapters() -> None:
    config = RipConfig(title_selection="all")
    candidates = [
        _candidate(1, duration=7200, chapters=24, size=30 * _GB),
        _candidate(2, duration=7200, chapters=8, size=30 * _GB),  # fewer chapters, same duration
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert [c.title_id for c in result.selected] == [1]


def test_near_duplicate_durations_break_ties_on_size() -> None:
    config = RipConfig(title_selection="all")
    candidates = [
        _candidate(1, duration=7200, chapters=24, size=20 * _GB),
        _candidate(2, duration=7200, chapters=24, size=30 * _GB),  # same chapters, larger size wins
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert [c.title_id for c in result.selected] == [2]


def test_structure_titles_with_100_plus_chapters_are_dropped() -> None:
    config = RipConfig(title_selection="all")
    candidates = [_candidate(0, duration=7200, chapters=24, size=30 * _GB)]
    candidates += [
        _candidate(i, duration=3600 + i * 5, chapters=150, size=500 * 1024**2) for i in range(1, 41)
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert [c.title_id for c in result.selected] == [0]


def test_low_bitrate_duplicate_at_the_same_duration_is_dropped() -> None:
    config = RipConfig(title_selection="all")
    candidates = [
        _candidate(1, duration=7200, chapters=24, size=30 * _GB),
        _candidate(2, duration=7205, chapters=24, size=2 * _GB),  # <30% of title 1's size
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert [c.title_id for c in result.selected] == [1]


def test_size_outlier_filter_ignores_candidates_with_no_size_data() -> None:
    config = RipConfig(title_selection="all")
    candidates = [
        _candidate(1, duration=7200, chapters=24, size=0),
        _candidate(2, duration=7205, chapters=24, size=0),
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert {c.title_id for c in result.selected} == {1, 2}


def test_longest_mode_picks_the_single_longest_survivor() -> None:
    config = RipConfig(title_selection="longest")
    candidates = [
        _candidate(1, duration=5400, chapters=20),
        _candidate(2, duration=7200, chapters=24),
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert result.needs_review is False
    assert [c.title_id for c in result.selected] == [2]


def test_longest_mode_parks_for_review_when_top_two_are_within_2_percent() -> None:
    config = RipConfig(title_selection="longest")
    candidates = [
        _candidate(1, duration=7200, chapters=24),
        _candidate(2, duration=7250, chapters=22),  # within 2% of 7200
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert result.needs_review is True
    assert result.selected == ()
    assert len(result.candidates_meta) == 2


def test_all_mode_returns_every_survivor_sorted_by_title_id() -> None:
    config = RipConfig(title_selection="all")
    candidates = [
        _candidate(2, duration=1800, chapters=10),
        _candidate(1, duration=1900, chapters=10),
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert result.needs_review is False
    assert [c.title_id for c in result.selected] == [1, 2]


def test_ask_mode_always_needs_review_with_candidates_listed() -> None:
    config = RipConfig(title_selection="ask")
    candidates = [_candidate(1, duration=7200, chapters=24)]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert result.needs_review is True
    assert result.selected == ()
    assert len(result.candidates_meta) == 1


def test_series_media_kind_forces_all_regardless_of_configured_mode() -> None:
    config = RipConfig(title_selection="longest")
    candidates = [
        _candidate(1, duration=1800, chapters=8),
        _candidate(2, duration=1850, chapters=8),
    ]

    result = select_titles(candidates, config=config, media_kind=MediaKind.SERIES)

    assert result.needs_review is False
    assert [c.title_id for c in result.selected] == [1, 2]


def test_no_survivors_needs_review_with_a_clear_reason() -> None:
    config = RipConfig(min_title_seconds=900, max_title_seconds=21600)
    candidates = [_candidate(1, duration=10)]

    result = select_titles(candidates, config=config, media_kind=MediaKind.MOVIE)

    assert result.needs_review is True
    assert result.reason is not None
    assert result.selected == ()
