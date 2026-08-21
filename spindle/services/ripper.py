"""Title selection, per phase-03-rip-engine.md task 3.

Deliberately decoupled from `MakeMkvTitleInfo`/`HandBrakeTitleInfo` — takes a
small `TitleCandidate` shape instead, so the algorithm doesn't care which
backend the scan came from. HandBrake's `--scan --json` doesn't report a
per-title byte size, so a HandBrake-derived candidate list has
`size_bytes=0` throughout; the size-based filters below degrade gracefully
rather than misfiring on that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spindle.config import RipConfig
from spindle.models.job import MediaKind

_DUPLICATE_DURATION_TOLERANCE_SECONDS = 1
_STRUCTURE_CHAPTER_THRESHOLD = 100
_SIZE_OUTLIER_DURATION_TOLERANCE = 0.02  # 2%, same tolerance used elsewhere in the app
_SIZE_OUTLIER_MIN_RATIO = 0.3
_CONFIDENCE_DURATION_TOLERANCE = 0.02  # 2%


@dataclass(frozen=True)
class TitleCandidate:
    title_id: int
    duration_seconds: int
    chapter_count: int
    size_bytes: int


@dataclass(frozen=True)
class TitleSelectionResult:
    selected: tuple[TitleCandidate, ...]
    needs_review: bool
    reason: str | None
    candidates_meta: tuple[dict[str, Any], ...]


def select_titles(
    candidates: list[TitleCandidate], *, config: RipConfig, media_kind: MediaKind
) -> TitleSelectionResult:
    survivors = [
        c
        for c in candidates
        if config.min_title_seconds <= c.duration_seconds <= config.max_title_seconds
    ]
    survivors = _drop_duration_duplicates(survivors)
    survivors = _drop_structure_titles(survivors)
    survivors = _drop_size_outliers(survivors)

    meta = tuple(_candidate_meta(c) for c in sorted(survivors, key=lambda c: c.title_id))

    if not survivors:
        return TitleSelectionResult(
            selected=(),
            needs_review=True,
            reason="No plausible main title found on this disc.",
            candidates_meta=meta,
        )

    # Series box sets can't be automatically mapped to episodes (out of
    # scope for v1) so every title is ripped and sorted out by the user.
    selection_mode = "all" if media_kind == MediaKind.SERIES else config.title_selection

    if selection_mode == "ask":
        return TitleSelectionResult(
            selected=(),
            needs_review=True,
            reason="Multiple candidate titles; user selection required.",
            candidates_meta=meta,
        )

    if selection_mode == "all":
        ordered = tuple(sorted(survivors, key=lambda c: c.title_id))
        return TitleSelectionResult(
            selected=ordered, needs_review=False, reason=None, candidates_meta=meta
        )

    return _select_longest(survivors, meta)


def _select_longest(
    survivors: list[TitleCandidate], meta: tuple[dict[str, Any], ...]
) -> TitleSelectionResult:
    ranked = sorted(survivors, key=lambda c: c.duration_seconds, reverse=True)
    best = ranked[0]
    if len(ranked) > 1:
        second = ranked[1]
        tolerance = best.duration_seconds * _CONFIDENCE_DURATION_TOLERANCE
        if abs(best.duration_seconds - second.duration_seconds) <= tolerance:
            # Silent wrong-title picks are the worst failure mode in this
            # product — a disc you have to re-rip a week later because
            # nobody noticed. Park it rather than guess.
            return TitleSelectionResult(
                selected=(),
                needs_review=True,
                reason="Two candidate titles have near-identical duration; picking "
                "the wrong one means a silent re-rip later.",
                candidates_meta=meta,
            )
    return TitleSelectionResult(
        selected=(best,), needs_review=False, reason=None, candidates_meta=meta
    )


def _drop_duration_duplicates(candidates: list[TitleCandidate]) -> list[TitleCandidate]:
    """Titles within 1s of another's duration are playlist duplicates of the
    same content — keep the one with more chapters, then the larger size."""
    survivors: list[TitleCandidate] = []
    for candidate in sorted(candidates, key=lambda c: c.duration_seconds):
        match_index = next(
            (
                i
                for i, kept in enumerate(survivors)
                if abs(kept.duration_seconds - candidate.duration_seconds)
                <= _DUPLICATE_DURATION_TOLERANCE_SECONDS
            ),
            None,
        )
        if match_index is None:
            survivors.append(candidate)
            continue
        kept = survivors[match_index]
        if (candidate.chapter_count, candidate.size_bytes) > (kept.chapter_count, kept.size_bytes):
            survivors[match_index] = candidate
    return survivors


def _drop_structure_titles(candidates: list[TitleCandidate]) -> list[TitleCandidate]:
    """Drop the anti-rip "structure" trick: 100+ chapters. MakeMKV's robot
    mode doesn't expose per-chapter timestamps without a second query per
    title, so chapter *count* alone is the signal here — legitimate main
    features rarely exceed a few dozen chapters; structure-trick titles use
    hundreds of near-uniform ones."""
    return [c for c in candidates if c.chapter_count < _STRUCTURE_CHAPTER_THRESHOLD]


def _drop_size_outliers(candidates: list[TitleCandidate]) -> list[TitleCandidate]:
    """Drop titles much smaller than another candidate of about the same
    duration — a low-bitrate duplicate stream, not the main feature.
    Candidates with `size_bytes == 0` (no size data, e.g. a HandBrake scan)
    are never dropped — there's nothing to compare."""
    survivors = []
    for candidate in candidates:
        if candidate.size_bytes == 0:
            survivors.append(candidate)
            continue
        tolerance = candidate.duration_seconds * _SIZE_OUTLIER_DURATION_TOLERANCE
        cluster_largest = max(
            (
                other.size_bytes
                for other in candidates
                if abs(other.duration_seconds - candidate.duration_seconds) <= tolerance
            ),
            default=candidate.size_bytes,
        )
        if candidate.size_bytes >= cluster_largest * _SIZE_OUTLIER_MIN_RATIO:
            survivors.append(candidate)
    return survivors


def _candidate_meta(candidate: TitleCandidate) -> dict[str, Any]:
    return {
        "title_id": candidate.title_id,
        "duration_seconds": candidate.duration_seconds,
        "chapter_count": candidate.chapter_count,
        "size_bytes": candidate.size_bytes,
    }
