"""Subtitle ranking, sidecar validation and encoding fixes — pure functions
per phase-05-metadata-subtitles.md tasks 2-3, deliberately decoupled from
`OpenSubtitlesAdapter` the same way `services/transcoder.py::choose_encoder`
is decoupled from `HandBrakeAdapter`. `SubtitleStage`
(`spindle/pipeline/stages.py`) owns the adapter orchestration and calls these.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import srt
from charset_normalizer import from_bytes

_MIN_CUES = 10
_MAX_FIRST_CUE_SECONDS = 10 * 60


def wanted_languages(configured: list[str], embedded_full_langs: set[str]) -> list[str]:
    """`SubtitleConfig.langs` minus languages already present as full
    (non-forced) tracks in the transcoded output, order-preserving and
    de-duplicated."""
    seen: set[str] = set()
    wanted: list[str] = []
    for lang in configured:
        if lang in embedded_full_langs or lang in seen:
            continue
        seen.add(lang)
        wanted.append(lang)
    return wanted


@dataclass(frozen=True)
class SubtitleCandidate:
    file_id: int
    language: str
    release: str | None
    download_count: int
    moviehash_match: bool
    is_hearing_impaired: bool
    ai_translated: bool
    machine_translated: bool


def rank_candidates(
    candidates: list[SubtitleCandidate],
    *,
    hearing_impaired: Literal["include", "exclude", "only"],
) -> list[SubtitleCandidate]:
    """Reject AI/machine-translated results outright, filter by the
    `hearing_impaired` config, then rank exact `moviehash` matches above
    everything else and break ties by `download_count` (phase doc task 2)."""
    filtered = [c for c in candidates if not c.ai_translated and not c.machine_translated]
    if hearing_impaired == "exclude":
        filtered = [c for c in filtered if not c.is_hearing_impaired]
    elif hearing_impaired == "only":
        filtered = [c for c in filtered if c.is_hearing_impaired]
    return sorted(filtered, key=lambda c: (c.moviehash_match, c.download_count), reverse=True)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None = None


def validate_srt(text: str) -> ValidationResult:
    """A 3-line "download our app" spam file is a common result (phase doc
    gotcha) — validate with a real parser and a minimum cue count/timing
    sanity check, not just "did it parse"."""
    try:
        cues = list(srt.parse(text))
    except (srt.SRTParseError, ValueError) as exc:
        return ValidationResult(ok=False, reason=f"Not valid SRT: {exc}")

    if len(cues) < _MIN_CUES:
        return ValidationResult(
            ok=False, reason=f"Only {len(cues)} cues, expected at least {_MIN_CUES}."
        )

    first_start = cues[0].start.total_seconds()
    if first_start >= _MAX_FIRST_CUE_SECONDS:
        return ValidationResult(
            ok=False, reason="First cue starts too late; likely a spam/ad file."
        )

    return ValidationResult(ok=True)


def decode_subtitle_bytes(data: bytes) -> str:
    """Subtitle encoding is frequently CP1252/ISO-8859-1 mislabelled as UTF-8
    (phase doc gotcha) — try strict UTF-8 first, fall back to
    charset-normalizer's best guess rather than assuming."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    best = from_bytes(data).best()
    if best is None:
        return data.decode("utf-8", errors="replace")
    return str(best)
