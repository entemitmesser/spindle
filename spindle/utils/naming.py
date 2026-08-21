from __future__ import annotations

import re
import string
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from spindle.errors import ValidationFailedError

_SEPARATORS = re.compile(r"[._]+")
_WORD_RUN = re.compile(r"[A-Za-z']+")
_ROMAN = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)

_DISC_MARKER = re.compile(r"^(?:DISC|DISK|D)0*([1-9]\d?)$", re.IGNORECASE)
_SEASON_MARKER = re.compile(r"^S0*([1-9]\d?)$", re.IGNORECASE)
_RESOLUTION = re.compile(r"^(?:480|576|720|1080|2160)P?$|^4K$|^UHD$|^HD$|^SD$", re.IGNORECASE)

_REGION_CODES = frozenset(
    {"DE", "US", "UK", "FR", "ES", "IT", "NL", "EU", "JP", "AU", "CA", "NTSC", "PAL"}
)
_REGION_LETTER = re.compile(r"^R([1-6])$", re.IGNORECASE)
_REGION_SUFFIX = re.compile(r"^REGION0*([A-D1-6])$", re.IGNORECASE)

# Longest phrases first so e.g. "EXTENDED CUT" is matched before bare "EXTENDED".
_EDITION_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("DIRECTORS", "CUT"), "Director's Cut"),
    (("DIRECTOR'S", "CUT"), "Director's Cut"),
    (("SPECIAL", "EDITION"), "Special Edition"),
    (("ANNIVERSARY", "EDITION"), "Anniversary Edition"),
    (("EXTENDED", "EDITION"), "Extended Edition"),
    (("EXTENDED", "CUT"), "Extended Cut"),
    (("THEATRICAL", "CUT"), "Theatrical Cut"),
    (("FINAL", "CUT"), "Final Cut"),
    (("UNRATED",), "Unrated"),
    (("EXTENDED",), "Extended"),
    (("THEATRICAL",), "Theatrical"),
    (("REMASTERED",), "Remastered"),
    (("UNCUT",), "Uncut"),
)


@dataclass(frozen=True)
class DiscLabelHints:
    disc_number: int | None = None
    season: int | None = None
    edition: str | None = None


def _titlecase_token(token: str) -> str:
    if len(token) <= 4 and _ROMAN.fullmatch(token):
        return token.upper()

    def _cap_word(match: re.Match[str]) -> str:
        parts = match.group(0).split("'")
        return "'".join(p[:1].upper() + p[1:].lower() if p else p for p in parts)

    return _WORD_RUN.sub(_cap_word, token)


def _extract_first(tokens: list[str], pattern: re.Pattern[str]) -> tuple[list[str], int | None]:
    for i, token in enumerate(tokens):
        match = pattern.match(token)
        if match:
            return tokens[:i] + tokens[i + 1 :], int(match.group(1))
    return tokens, None


def _strip_edition(tokens: list[str]) -> tuple[list[str], str | None]:
    upper = [t.upper() for t in tokens]
    for phrase, canonical in _EDITION_PHRASES:
        width = len(phrase)
        for i in range(len(tokens) - width + 1):
            if tuple(upper[i : i + width]) == phrase:
                return tokens[:i] + tokens[i + width :], canonical
    return tokens, None


def _strip_trailing_region(tokens: list[str]) -> list[str]:
    tokens = list(tokens)
    while tokens:
        if (
            len(tokens) >= 2
            and tokens[-2].upper() == "REGION"
            and re.fullmatch(r"[A-D1-6]", tokens[-1].upper())
        ):
            tokens = tokens[:-2]
            continue
        last = tokens[-1].upper()
        if last in _REGION_CODES or _REGION_LETTER.match(last) or _REGION_SUFFIX.match(last):
            tokens = tokens[:-1]
            continue
        break
    return tokens


def normalize_disc_label(label: str) -> tuple[str, DiscLabelHints]:
    """Turn a shouty disc volume label into a clean title plus extracted hints.

    Pipeline (order matters): split on separators, pull out disc/season
    markers, drop resolution noise, pull out an edition phrase, drop a
    trailing region code, title-case what remains.
    """
    tokens = [t for t in _SEPARATORS.sub(" ", label).split() if t]

    tokens, disc_number = _extract_first(tokens, _DISC_MARKER)
    tokens, season = _extract_first(tokens, _SEASON_MARKER)
    tokens = [t for t in tokens if not _RESOLUTION.match(t)]
    tokens, edition = _strip_edition(tokens)
    tokens = _strip_trailing_region(tokens)

    title = " ".join(_titlecase_token(t) for t in tokens).strip()
    return title, DiscLabelHints(disc_number=disc_number, season=season, edition=edition)


# -- Library naming templates, per architecture.md §6 -----------------------
#
# `render_template`/`sanitize_segment`/`resolve_destination` are unrelated to
# the disc-label normalizer above: they turn a *library naming template*
# (user-configurable, `LibraryConfig` in `spindle/config.py`) into a safe
# filesystem path for `OrganizeStage`. Kept dependency-free (stdlib only) so
# `LibraryConfig` can call `validate_template` at settings-save time without
# a circular import.

NAMING_VARIABLES: frozenset[str] = frozenset(
    {
        "title",
        "original_title",
        "year",
        "season",
        "episode",
        "episode_title",
        "tmdb_id",
        "imdb_id",
        "source",
        "resolution",
        "video_codec",
        "audio_codec",
        "edition",
    }
)

# Covers the whole fixed variable set with plausible values so
# `validate_template` can catch an unknown variable or a bad format spec
# (e.g. `{title:02d}`) at settings-save time, without a real job on hand.
_DUMMY_NAMING_VARIABLES: dict[str, Any] = {
    "title": "Title",
    "original_title": "Original Title",
    "year": 2000,
    "season": 1,
    "episode": 1,
    "episode_title": "Episode Title",
    "tmdb_id": 1,
    "imdb_id": "tt0000000",
    "source": "Bluray",
    "resolution": "1080p",
    "video_codec": "h265",
    "audio_codec": "aac",
    "edition": "Extended Edition",
}


class _RestrictedFormatter(string.Formatter):
    """A `string.Formatter` that only allows the fixed `NAMING_VARIABLES`
    set, as plain field names — never `eval`, never attribute/index access
    (`{title.upper}`, `{title[0]}`), never positional/auto-numbered fields
    (`{}`, `{0}`) and never conversions (`{title!r}`)."""

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> tuple[Any, Any]:
        if not field_name.isidentifier():
            raise ValidationFailedError(f"Invalid naming template field: {{{field_name}}}")
        return cast("tuple[Any, Any]", super().get_field(field_name, args, kwargs))

    def get_value(self, key: object, args: Any, kwargs: Mapping[str, Any]) -> Any:
        if not isinstance(key, str):
            raise ValidationFailedError("Naming templates cannot use positional fields.")
        if key not in NAMING_VARIABLES:
            raise ValidationFailedError(f"Unknown naming template variable: {{{key}}}")
        if key not in kwargs:
            raise ValidationFailedError(f"Naming template variable {{{key}}} has no value here.")
        return kwargs[key]

    def convert_field(self, value: Any, conversion: str | None) -> Any:
        if conversion is not None:
            raise ValidationFailedError("Naming templates cannot use conversions (e.g. {x!r}).")
        return value


_FORMATTER = _RestrictedFormatter()


def render_template(template: str, variables: Mapping[str, Any]) -> str:
    """Renders `template` against `variables` with the restricted formatter.
    Raises `ValidationFailedError` for an unknown/malformed field, a bad
    format spec, or a referenced variable that isn't in `variables`."""
    try:
        return _FORMATTER.vformat(template, (), variables)
    except ValidationFailedError:
        raise
    except (ValueError, IndexError, TypeError) as exc:
        raise ValidationFailedError(f"Invalid naming template {template!r}: {exc}") from exc


def validate_template(template: str) -> None:
    """Settings-save-time check (phase-06 acceptance criteria): renders
    `template` against a full dummy variable set so an unknown variable or a
    bad format spec is caught here, not later when `OrganizeStage` runs and
    a real job is legitimately missing e.g. `episode`."""
    render_template(template, _DUMMY_NAMING_VARIABLES)


_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RUN = re.compile(r"\s+")
_MAX_SEGMENT_LEN = 180


def sanitize_segment(segment: str) -> str:
    """One path *segment* (never a full path -- a template's own `/` are
    directory separators, handled by `render_path`, not text to preserve).

    Strips `<>:"/\\|?*` and control chars, collapses whitespace, strips
    trailing dots/spaces (Windows/SMB shares break on these regardless of
    the local filesystem, per phase-06's gotcha), NFC-normalizes, and
    truncates to 180 chars on a word boundary.
    """
    normalized = unicodedata.normalize("NFC", segment)
    stripped = _ILLEGAL_CHARS.sub("", normalized)
    collapsed = _WHITESPACE_RUN.sub(" ", stripped).strip()
    trimmed = collapsed.rstrip(". ")
    if len(trimmed) <= _MAX_SEGMENT_LEN:
        return trimmed
    truncated = trimmed[:_MAX_SEGMENT_LEN]
    head, sep, _tail = truncated.rpartition(" ")
    if sep:
        truncated = head
    return truncated.rstrip(". ")


def render_path(template: str, variables: Mapping[str, Any]) -> Path:
    """Renders `template`, splits it on `/` into directory segments, and
    sanitizes each segment independently. Empty segments (e.g. a sanitized
    title that came out blank) are dropped."""
    rendered = render_template(template, variables)
    parts = [sanitize_segment(part) for part in rendered.split("/")]
    parts = [p for p in parts if p]
    if not parts:
        raise ValidationFailedError(f"Naming template {template!r} rendered to an empty path.")
    return Path(*parts)


def resolve_destination(base_dir: Path, relative: Path) -> Path:
    """Joins `relative` under `base_dir` and guarantees the result is under
    `base_dir` after `.resolve()` -- architecture.md §8's path-traversal
    defense. `relative` should already be built from sanitized segments
    (`render_path`); this is the backstop, not the primary defense."""
    base_resolved = base_dir.resolve()
    candidate = (base_resolved / relative).resolve()
    if candidate != base_resolved and base_resolved not in candidate.parents:
        raise ValidationFailedError(f"Resolved path escapes the library directory: {relative}")
    return candidate
