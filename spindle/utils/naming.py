from __future__ import annotations

import re
from dataclasses import dataclass

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
