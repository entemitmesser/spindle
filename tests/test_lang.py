from __future__ import annotations

import pytest

from spindle.utils import lang


def test_round_trips_de_ger_deu() -> None:
    assert lang.to_639_2b("de") == "ger"
    assert lang.to_639_2t("ger") == "deu"
    assert lang.to_639_1("deu") == "de"


def test_identity_language_where_b_and_t_agree() -> None:
    assert lang.to_639_2t("en") == "eng"
    assert lang.to_639_2b("eng") == "eng"
    assert lang.to_639_1("eng") == "en"


@pytest.mark.parametrize("code", ["xx", "", "zzz", "not-a-lang"])
def test_unknown_codes_raise_loudly(code: str) -> None:
    with pytest.raises(lang.UnknownLanguageError):
        lang.to_639_2t(code)


def test_is_known() -> None:
    assert lang.is_known("deu") is True
    assert lang.is_known("xx-nope") is False
    assert lang.is_known(None) is False


def test_quirk_codes_map_onto_a_known_language() -> None:
    assert lang.to_639_2t("pt-BR") == "por"
    assert lang.to_639_2t("zh-CN") == "zho"


def test_language_without_iso_639_1_raises_when_requested() -> None:
    # Latin has no ISO 639-1 code.
    assert lang.to_639_2t("lat") == "lat"
    with pytest.raises(lang.UnknownLanguageError):
        lang.to_639_1("lat")


def test_case_and_whitespace_insensitive() -> None:
    assert lang.to_639_2t("  DEU  ") == "deu"
