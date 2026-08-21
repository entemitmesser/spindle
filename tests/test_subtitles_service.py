from __future__ import annotations

from spindle.services.subtitles import (
    SubtitleCandidate,
    decode_subtitle_bytes,
    rank_candidates,
    validate_srt,
    wanted_languages,
)


def _candidate(**overrides: object) -> SubtitleCandidate:
    defaults: dict[str, object] = {
        "file_id": 1,
        "language": "eng",
        "release": None,
        "download_count": 0,
        "moviehash_match": False,
        "is_hearing_impaired": False,
        "ai_translated": False,
        "machine_translated": False,
    }
    return SubtitleCandidate(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_wanted_languages_subtracts_embedded_and_dedupes() -> None:
    wanted = wanted_languages(["deu", "eng", "deu"], embedded_full_langs={"eng"})
    assert wanted == ["deu"]


def test_wanted_languages_empty_when_all_embedded() -> None:
    assert wanted_languages(["deu", "eng"], embedded_full_langs={"deu", "eng"}) == []


def test_rank_candidates_moviehash_match_wins_over_download_count() -> None:
    low_but_hash = _candidate(file_id=1, moviehash_match=True, download_count=1)
    high_no_hash = _candidate(file_id=2, moviehash_match=False, download_count=9999)

    ranked = rank_candidates([high_no_hash, low_but_hash], hearing_impaired="exclude")

    assert ranked[0].file_id == 1


def test_rank_candidates_breaks_ties_by_download_count() -> None:
    fewer = _candidate(file_id=1, download_count=5)
    more = _candidate(file_id=2, download_count=50)

    ranked = rank_candidates([fewer, more], hearing_impaired="exclude")

    assert [c.file_id for c in ranked] == [2, 1]


def test_rank_candidates_rejects_ai_and_machine_translated() -> None:
    ai = _candidate(file_id=1, ai_translated=True, download_count=1000)
    machine = _candidate(file_id=2, machine_translated=True, download_count=1000)
    clean = _candidate(file_id=3, download_count=1)

    ranked = rank_candidates([ai, machine, clean], hearing_impaired="exclude")

    assert [c.file_id for c in ranked] == [3]


def test_rank_candidates_hearing_impaired_exclude() -> None:
    hi = _candidate(file_id=1, is_hearing_impaired=True)
    normal = _candidate(file_id=2, is_hearing_impaired=False)

    ranked = rank_candidates([hi, normal], hearing_impaired="exclude")

    assert [c.file_id for c in ranked] == [2]


def test_rank_candidates_hearing_impaired_only() -> None:
    hi = _candidate(file_id=1, is_hearing_impaired=True)
    normal = _candidate(file_id=2, is_hearing_impaired=False)

    ranked = rank_candidates([hi, normal], hearing_impaired="only")

    assert [c.file_id for c in ranked] == [1]


def test_rank_candidates_hearing_impaired_include_keeps_both() -> None:
    hi = _candidate(file_id=1, is_hearing_impaired=True)
    normal = _candidate(file_id=2, is_hearing_impaired=False)

    ranked = rank_candidates([hi, normal], hearing_impaired="include")

    assert {c.file_id for c in ranked} == {1, 2}


_VALID_SRT = "\n\n".join(
    f"{i}\n00:{i:02d}:00,000 --> 00:{i:02d}:02,000\nLine {i}" for i in range(1, 12)
)


def test_validate_srt_accepts_a_well_formed_file() -> None:
    result = validate_srt(_VALID_SRT)
    assert result.ok is True


def test_validate_srt_rejects_too_few_cues() -> None:
    spam = "1\n00:00:01,000 --> 00:00:02,000\nDownload our app at example.com\n"
    result = validate_srt(spam)
    assert result.ok is False
    assert "cues" in (result.reason or "")


def test_validate_srt_rejects_unparseable_garbage() -> None:
    result = validate_srt("this is not an srt file at all, just prose.")
    assert result.ok is False


def test_validate_srt_rejects_first_cue_starting_too_late() -> None:
    late = "\n\n".join(
        f"{i}\n{11 + i:02d}:00:00,000 --> {11 + i:02d}:00:02,000\nLine {i}" for i in range(1, 12)
    )
    result = validate_srt(late)
    assert result.ok is False
    assert "late" in (result.reason or "")


def test_decode_subtitle_bytes_handles_valid_utf8() -> None:
    text = "Café results — déjà vu"
    assert decode_subtitle_bytes(text.encode("utf-8")) == text


def test_decode_subtitle_bytes_recovers_from_mislabelled_cp1252() -> None:
    # A real subtitle-length sample encoded as CP1252, not valid UTF-8 on its
    # own — charset-normalizer needs enough text to detect reliably, unlike
    # a four-character string.
    text = "This is a subtitle line with a café mentioned in it. " * 20
    raw = text.encode("cp1252")

    decoded = decode_subtitle_bytes(raw)

    assert "café" in decoded
