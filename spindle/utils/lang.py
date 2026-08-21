"""ISO 639 language-code normalization, per phase-05-metadata-subtitles.md
task 5: "a small file that prevents a large class of bugs; write it early and
use it everywhere."

Everything internal (job/config/`MediaFile.language`) uses ISO 639-2/T
(`deu`, `eng`). The one documented exception is `mkvpropedit` track tagging
(`spindle/services/metadata.py`), which the phase doc calls out as using
639-2/B (`ger`) instead — Matroska's own convention.

Covers the languages plausibly found on a DVD/Blu-ray plus a small quirks
table for the handful of pseudo-codes HandBrake and OpenSubtitles emit that
don't fit the 639-1/639-2B/639-2T triple cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass

from spindle.errors import ValidationFailedError


class UnknownLanguageError(ValidationFailedError):
    """A language code that doesn't match any known ISO 639-1/639-2 code."""


@dataclass(frozen=True)
class _Lang:
    iso1: str | None
    b: str
    t: str


# (639-1, 639-2/B, 639-2/T) — B and T are identical for most languages; only
# the handful with a distinct bibliographic code (German, French, Chinese,
# Dutch, Romanian, Czech, Greek, Icelandic, Welsh, Basque, Macedonian,
# Armenian, Georgian, Persian, Malay, Burmese, Tibetan, Slovak) differ.
_LANGUAGES: tuple[_Lang, ...] = (
    _Lang("en", "eng", "eng"),
    _Lang("de", "ger", "deu"),
    _Lang("fr", "fre", "fra"),
    _Lang("es", "spa", "spa"),
    _Lang("it", "ita", "ita"),
    _Lang("pt", "por", "por"),
    _Lang("nl", "dut", "nld"),
    _Lang("ru", "rus", "rus"),
    _Lang("ja", "jpn", "jpn"),
    _Lang("zh", "chi", "zho"),
    _Lang("ko", "kor", "kor"),
    _Lang("sv", "swe", "swe"),
    _Lang("no", "nor", "nor"),
    _Lang("da", "dan", "dan"),
    _Lang("fi", "fin", "fin"),
    _Lang("pl", "pol", "pol"),
    _Lang("cs", "cze", "ces"),
    _Lang("sk", "slo", "slk"),
    _Lang("hu", "hun", "hun"),
    _Lang("tr", "tur", "tur"),
    _Lang("el", "gre", "ell"),
    _Lang("ar", "ara", "ara"),
    _Lang("he", "heb", "heb"),
    _Lang("hi", "hin", "hin"),
    _Lang("th", "tha", "tha"),
    _Lang("vi", "vie", "vie"),
    _Lang("id", "ind", "ind"),
    _Lang("ro", "rum", "ron"),
    _Lang("bg", "bul", "bul"),
    _Lang("hr", "hrv", "hrv"),
    _Lang("sr", "srp", "srp"),
    _Lang("sl", "slv", "slv"),
    _Lang("uk", "ukr", "ukr"),
    _Lang("is", "ice", "isl"),
    _Lang("et", "est", "est"),
    _Lang("lv", "lav", "lav"),
    _Lang("lt", "lit", "lit"),
    _Lang("ca", "cat", "cat"),
    _Lang("eu", "baq", "eus"),
    _Lang("gl", "glg", "glg"),
    _Lang("cy", "wel", "cym"),
    _Lang("ga", "gle", "gle"),
    _Lang("mt", "mlt", "mlt"),
    _Lang("sq", "alb", "sqi"),
    _Lang("mk", "mac", "mkd"),
    _Lang("hy", "arm", "hye"),
    _Lang("ka", "geo", "kat"),
    _Lang("fa", "per", "fas"),
    _Lang("ms", "may", "msa"),
    _Lang("my", "bur", "mya"),
    _Lang("bo", "tib", "bod"),
    _Lang(None, "lat", "lat"),
)

# HandBrake/OpenSubtitles quirks: pseudo-codes that map onto one of the
# languages above rather than being distinct languages of their own.
_QUIRKS: dict[str, str] = {
    "pt-br": "por",
    "pob": "por",
    "zh-cn": "zho",
    "zh-tw": "zho",
    "zh-hans": "zho",
    "zh-hant": "zho",
}

_BY_T = {lang.t: lang for lang in _LANGUAGES}
_BY_B = {lang.b: lang for lang in _LANGUAGES}
_BY_1 = {lang.iso1: lang for lang in _LANGUAGES if lang.iso1 is not None}


def _resolve(code: str) -> _Lang:
    if not code or not code.strip():
        raise UnknownLanguageError("Empty language code.")
    normalized = code.strip().lower()
    normalized = _QUIRKS.get(normalized, normalized)
    lang = _BY_T.get(normalized) or _BY_B.get(normalized) or _BY_1.get(normalized)
    if lang is None:
        raise UnknownLanguageError(f"Unknown language code: {code!r}")
    return lang


def is_known(code: str | None) -> bool:
    if not code:
        return False
    try:
        _resolve(code)
    except UnknownLanguageError:
        return False
    return True


def to_639_2t(code: str) -> str:
    """The internal representation — use this everywhere except `mkvpropedit`
    track tagging."""
    return _resolve(code).t


def to_639_2b(code: str) -> str:
    """Matroska's convention — only `spindle/services/metadata.py` should
    need this."""
    return _resolve(code).b


def to_639_1(code: str) -> str:
    lang = _resolve(code)
    if lang.iso1 is None:
        raise UnknownLanguageError(f"{code!r} has no ISO 639-1 equivalent.")
    return lang.iso1
