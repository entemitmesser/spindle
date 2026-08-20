"""Disc typing, fingerprinting and TMDB confidence scoring.

Pure functions where possible (`classify`, `score`) so they're trivial to
unit test; `compute_fingerprint` is the one function that talks to hardware,
via an injected `OpticalAdapter`, and always degrades to `None` on failure
per the "fail into needs_review, not a stack trace" rule — a disc that can't
be fingerprinted just doesn't get fingerprint-based reuse, it isn't fatal.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import structlog
from rapidfuzz import fuzz

from spindle.adapters.optical import DiscStatus, OpticalAdapter
from spindle.adapters.tmdb import TmdbAdapter
from spindle.models.disc import DiscType
from spindle.models.job import MediaKind
from spindle.utils.naming import DiscLabelHints

logger = structlog.get_logger(__name__)

_POSTER_BASE_URL = "https://image.tmdb.org/t/p/w500"
_POPULARITY_SCALE = 100.0
_MAX_CANDIDATES = 5

_UHD_SIZE_THRESHOLD_BYTES = 60 * 1024**3

_YEAR_EXACT = 1.0
_YEAR_CLOSE = 0.5
_YEAR_UNKNOWN = 0.3
_RUNTIME_FULL_TOLERANCE = 0.05  # within +/-5%, runtime_agreement is 1.0
_RUNTIME_ZERO_AT = 0.5  # beyond +/-50% difference, runtime_agreement is 0.0
_SCORE_WEIGHTS = {"label": 0.5, "year": 0.2, "runtime": 0.2, "popularity": 0.1}


def classify(
    *, disc_status: DiscStatus, root_entries: list[str], size_bytes: int | None
) -> DiscType:
    upper_entries = {e.upper() for e in root_entries}
    if disc_status == DiscStatus.AUDIO:
        return DiscType.AUDIO_CD
    if "BDMV" in upper_entries:
        if size_bytes is not None and size_bytes > _UHD_SIZE_THRESHOLD_BYTES:
            return DiscType.UHD_BLURAY
        return DiscType.BLURAY
    if "VIDEO_TS" in upper_entries:
        return DiscType.DVD
    if root_entries:
        return DiscType.DATA
    return DiscType.UNKNOWN


def year_agreement(candidate_year: int | None, disc_year_hint: int | None) -> float:
    if candidate_year is None or disc_year_hint is None:
        return _YEAR_UNKNOWN
    diff = abs(candidate_year - disc_year_hint)
    if diff == 0:
        return _YEAR_EXACT
    if diff == 1:
        return _YEAR_CLOSE
    return 0.0


def runtime_agreement(candidate_minutes: float | None, disc_minutes: float | None) -> float | None:
    """`None` means "unavailable" — the caller must drop and renormalize, not score it as 0."""
    if candidate_minutes is None or disc_minutes is None or disc_minutes <= 0:
        return None
    diff_ratio = abs(candidate_minutes - disc_minutes) / disc_minutes
    if diff_ratio <= _RUNTIME_FULL_TOLERANCE:
        return 1.0
    if diff_ratio >= _RUNTIME_ZERO_AT:
        return 0.0
    span = _RUNTIME_ZERO_AT - _RUNTIME_FULL_TOLERANCE
    return 1.0 - (diff_ratio - _RUNTIME_FULL_TOLERANCE) / span


def score(
    *,
    normalized_label: str,
    candidate_title: str,
    candidate_year: int | None,
    disc_year_hint: int | None,
    candidate_runtime_minutes: float | None,
    disc_runtime_minutes: float | None,
    popularity_prior: float,
) -> float:
    label_score = fuzz.ratio(normalized_label.lower(), candidate_title.lower()) / 100.0
    year_score = year_agreement(candidate_year, disc_year_hint)
    runtime_score = runtime_agreement(candidate_runtime_minutes, disc_runtime_minutes)
    popularity_score = max(0.0, min(1.0, popularity_prior))

    components = {"label": label_score, "year": year_score, "popularity": popularity_score}
    if runtime_score is not None:
        components["runtime"] = runtime_score

    weights = {k: v for k, v in _SCORE_WEIGHTS.items() if k in components}
    total_weight = sum(weights.values())
    return sum(components[k] * (weights[k] / total_weight) for k in weights)


def compute_fingerprint(adapter: OpticalAdapter, dev: str, disc_type: DiscType) -> str | None:
    try:
        digest = _fingerprint_payload(adapter, dev, disc_type)
    except Exception:  # noqa: BLE001 -- fingerprinting must never fail the job
        logger.warning("identifier.fingerprint_failed", dev=dev, disc_type=disc_type)
        return None
    if digest is None:
        return None
    return f"sha1:{hashlib.sha1(digest.encode(), usedforsecurity=False).hexdigest()}"  # noqa: S324


def _fingerprint_payload(adapter: OpticalAdapter, dev: str, disc_type: DiscType) -> str | None:
    if disc_type == DiscType.DVD:
        titles = adapter.read_dvd_title_info(dev)
        if not titles:
            return None
        parts = [f"{t.index}:{t.length_seconds:.3f}" for t in titles]
        return f"dvd:{len(titles)}:" + ",".join(parts)

    if disc_type in (DiscType.BLURAY, DiscType.UHD_BLURAY):
        entries = adapter.list_entries(dev, ("BDMV", "PLAYLIST"))
        mpls = [e for e in entries if not e.is_dir and e.name.upper().endswith(".MPLS")]
        if not mpls:
            return None
        parts = sorted(
            f"{e.name}:{e.size_bytes}:{e.mtime.isoformat() if e.mtime else ''}" for e in mpls
        )
        return "bd:" + ",".join(parts)

    if disc_type == DiscType.AUDIO_CD:
        return _audio_cd_fingerprint(dev)

    return None


def _audio_cd_fingerprint(dev: str) -> str | None:
    try:
        import discid
    except ImportError:
        logger.info("identifier.discid_unavailable", dev=dev)
        return None
    disc = discid.read(dev)
    return f"audio_cd:{disc.id}"


@dataclass(frozen=True)
class IdentifyOutcome:
    media_kind: MediaKind
    title: str | None
    original_title: str | None
    year: int | None
    tmdb_id: int | None
    confidence: float | None
    poster_url: str | None
    accepted: bool
    candidates: list[dict[str, Any]]


def _candidate_title(candidate: dict[str, Any]) -> str:
    return str(candidate.get("title") or candidate.get("name") or "")


def _candidate_year(candidate: dict[str, Any]) -> int | None:
    date = candidate.get("release_date") or candidate.get("first_air_date") or ""
    return int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None


def _candidate_poster_url(candidate: dict[str, Any]) -> str | None:
    path = candidate.get("poster_path")
    return f"{_POSTER_BASE_URL}{path}" if path else None


_YEAR_IN_TITLE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _year_hint_from_title(normalized_title: str) -> int | None:
    """Best-effort year pulled from the title text itself.

    `normalize_disc_label` doesn't extract a year hint (only disc_number,
    season and edition are part of its contract) but many real labels do
    carry one, e.g. "THE_LION_KING_1994_D1" -> "The Lion King 1994". Scoring
    without it means `year_agreement` always falls back to its 0.3 default,
    so it's worth a cheap regex scan here even though it's scoring-only and
    never strips the year back out of the title.
    """
    match = _YEAR_IN_TITLE.search(normalized_title)
    return int(match.group(1)) if match else None


async def identify_disc(
    tmdb: TmdbAdapter,
    *,
    normalized_title: str,
    hints: DiscLabelHints,
    disc_runtime_minutes: float | None,
    confidence_threshold: float,
) -> IdentifyOutcome:
    """Search TMDB, score candidates, and decide accept vs. `needs_review`.

    Multi-disc hints (`disc_number` or `season`) mean this could be a TV box
    set, where automatic episode mapping is out of scope for v1: prefer a TV
    search and always park in `needs_review`, regardless of score.
    """
    prefer_series = hints.disc_number is not None or hints.season is not None
    media_kind = MediaKind.SERIES if prefer_series else MediaKind.MOVIE

    if prefer_series:
        results = await tmdb.search_tv(normalized_title)
    else:
        results = await tmdb.search_movie(normalized_title)
        if not results:
            results = await tmdb.search_tv(normalized_title)
            media_kind = MediaKind.SERIES if results else media_kind

    if not results:
        return IdentifyOutcome(
            media_kind=media_kind,
            title=None,
            original_title=None,
            year=None,
            tmdb_id=None,
            confidence=None,
            poster_url=None,
            accepted=False,
            candidates=[],
        )

    disc_year_hint = _year_hint_from_title(normalized_title)
    scored = sorted(
        (
            (
                score(
                    normalized_label=normalized_title,
                    candidate_title=_candidate_title(candidate),
                    candidate_year=_candidate_year(candidate),
                    disc_year_hint=disc_year_hint,
                    # TMDB search results don't carry runtime (only /details
                    # does); the term is dropped and weights renormalized.
                    candidate_runtime_minutes=None,
                    disc_runtime_minutes=disc_runtime_minutes,
                    popularity_prior=min(
                        (candidate.get("popularity") or 0.0) / _POPULARITY_SCALE, 1.0
                    ),
                ),
                candidate,
            )
            for candidate in results
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )

    candidates_payload = [
        {
            "tmdb_id": candidate.get("id"),
            "title": _candidate_title(candidate),
            "year": _candidate_year(candidate),
            "poster_url": _candidate_poster_url(candidate),
            "confidence": candidate_score,
        }
        for candidate_score, candidate in scored[:_MAX_CANDIDATES]
    ]

    best_score, best = scored[0]
    accepted = best_score >= confidence_threshold and not prefer_series
    if not accepted:
        return IdentifyOutcome(
            media_kind=media_kind,
            title=None,
            original_title=None,
            year=None,
            tmdb_id=None,
            confidence=best_score,
            poster_url=None,
            accepted=False,
            candidates=candidates_payload,
        )

    return IdentifyOutcome(
        media_kind=media_kind,
        title=_candidate_title(best),
        original_title=str(best.get("original_title") or best.get("original_name") or ""),
        year=_candidate_year(best),
        tmdb_id=best.get("id"),
        confidence=best_score,
        poster_url=_candidate_poster_url(best),
        accepted=True,
        candidates=candidates_payload,
    )
