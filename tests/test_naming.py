from __future__ import annotations

from pathlib import Path

import pytest

from spindle.errors import ValidationFailedError
from spindle.utils.naming import (
    DiscLabelHints,
    normalize_disc_label,
    render_path,
    render_template,
    resolve_destination,
    sanitize_segment,
    validate_template,
)

# (raw disc label, expected clean title, expected hints)
LABEL_FIXTURES: list[tuple[str, str, DiscLabelHints]] = [
    ("THE_MATRIX_RELOADED_D1", "The Matrix Reloaded", DiscLabelHints(disc_number=1)),
    ("THE_MATRIX_REVOLUTIONS_D2", "The Matrix Revolutions", DiscLabelHints(disc_number=2)),
    ("DIE_HARDE_1080P_DE", "Die Harde", DiscLabelHints()),
    ("INCEPTION_2160P_UHD", "Inception", DiscLabelHints()),
    ("BREAKING_BAD_S01_D2", "Breaking Bad", DiscLabelHints(disc_number=2, season=1)),
    ("GAME_OF_THRONES_S03_DISC4", "Game Of Thrones", DiscLabelHints(disc_number=4, season=3)),
    ("STAR_WARS_A_NEW_HOPE", "Star Wars A New Hope", DiscLabelHints()),
    (
        "BLADE_RUNNER_2049_EXTENDED_CUT",
        "Blade Runner 2049",
        DiscLabelHints(edition="Extended Cut"),
    ),
    ("ALIENS_DIRECTORS_CUT", "Aliens", DiscLabelHints(edition="Director's Cut")),
    ("APOCALYPSE_NOW_FINAL_CUT", "Apocalypse Now", DiscLabelHints(edition="Final Cut")),
    ("TERMINATOR_2_SPECIAL_EDITION", "Terminator 2", DiscLabelHints(edition="Special Edition")),
    (
        "LORD_OF_THE_RINGS_EXTENDED_EDITION_DISC1",
        "Lord Of The Rings",
        DiscLabelHints(disc_number=1, edition="Extended Edition"),
    ),
    ("JURASSIC_PARK_UNRATED", "Jurassic Park", DiscLabelHints(edition="Unrated")),
    ("PULP_FICTION_THEATRICAL", "Pulp Fiction", DiscLabelHints(edition="Theatrical")),
    ("BLADE_RUNNER_REMASTERED_UK", "Blade Runner", DiscLabelHints(edition="Remastered")),
    ("FRIENDS_S02_D3_720P", "Friends", DiscLabelHints(disc_number=3, season=2)),
    ("THE_OFFICE_S04_DISC2_US", "The Office", DiscLabelHints(disc_number=2, season=4)),
    ("AVATAR_2160P_R2", "Avatar", DiscLabelHints()),
    ("TITANIC_REGION_1", "Titanic", DiscLabelHints()),
    ("CASABLANCA_NTSC", "Casablanca", DiscLabelHints()),
    ("AMELIE_FR_PAL", "Amelie", DiscLabelHints()),
    ("SEVEN_SAMURAI_JP", "Seven Samurai", DiscLabelHints()),
    ("THE_GODFATHER_PART_II", "The Godfather Part II", DiscLabelHints()),
    ("ROCKY_IV", "Rocky IV", DiscLabelHints()),
    (
        "STAR_TREK_II_THE_WRATH_OF_KHAN",
        "Star Trek II The Wrath Of Khan",
        DiscLabelHints(),
    ),
    ("2001_A_SPACE_ODYSSEY", "2001 A Space Odyssey", DiscLabelHints()),
    (
        "BACK_TO_THE_FUTURE_PART_III_D1",
        "Back To The Future Part III",
        DiscLabelHints(disc_number=1),
    ),
    (
        "SPIDER-MAN_INTO_THE_SPIDER-VERSE",
        "Spider-Man Into The Spider-Verse",
        DiscLabelHints(),
    ),
    ("O'BRIEN_DOCUMENTARY", "O'Brien Documentary", DiscLabelHints()),
    ("AMADEUS_DIRECTOR'S_CUT", "Amadeus", DiscLabelHints(edition="Director's Cut")),
    (
        "THE_LION_KING_1994_D1_DE",
        "The Lion King 1994",
        DiscLabelHints(disc_number=1),
    ),
    ("MONTY_PYTHON_AND_THE_HOLY_GRAIL", "Monty Python And The Holy Grail", DiscLabelHints()),
    (
        "GLADIATOR_EXTENDED_D2_UK",
        "Gladiator",
        DiscLabelHints(disc_number=2, edition="Extended"),
    ),
    ("V_FOR_VENDETTA", "V For Vendetta", DiscLabelHints()),
    ("X-MEN_DAYS_OF_FUTURE_PAST", "X-Men Days Of Future Past", DiscLabelHints()),
    ("K-19_THE_WIDOWMAKER", "K-19 The Widowmaker", DiscLabelHints()),
    ("WALL-E", "Wall-E", DiscLabelHints()),
    ("UP_2009_1080P", "Up 2009", DiscLabelHints()),
    (
        "TOY_STORY_ANNIVERSARY_EDITION_D1",
        "Toy Story",
        DiscLabelHints(disc_number=1, edition="Anniversary Edition"),
    ),
    (
        "THE_WIRE_S05_DISC3_US_NTSC",
        "The Wire",
        DiscLabelHints(disc_number=3, season=5),
    ),
]


def test_forty_label_fixtures_covered() -> None:
    assert len(LABEL_FIXTURES) == 40


@pytest.mark.parametrize(("label", "expected_title", "expected_hints"), LABEL_FIXTURES)
def test_normalize_disc_label(
    label: str, expected_title: str, expected_hints: DiscLabelHints
) -> None:
    title, hints = normalize_disc_label(label)
    assert title == expected_title
    assert hints == expected_hints


# -- render_template / validate_template -------------------------------------


def test_render_template_substitutes_known_variables() -> None:
    result = render_template(
        "{title} ({year}) - {source}-{resolution}",
        {
            "title": "Amelie",
            "year": 2001,
            "source": "Bluray",
            "resolution": "1080p",
        },
    )
    assert result == "Amelie (2001) - Bluray-1080p"


def test_render_template_supports_zero_padded_format_specs() -> None:
    result = render_template(
        "{title} - S{season:02d}E{episode:02d}",
        {"title": "The Wire", "season": 1, "episode": 3},
    )
    assert result == "The Wire - S01E03"


def test_render_template_rejects_unknown_variable() -> None:
    with pytest.raises(ValidationFailedError):
        render_template("{title} {nonexistent}", {"title": "X", "nonexistent": "Y"})


def test_render_template_rejects_attribute_access() -> None:
    with pytest.raises(ValidationFailedError):
        render_template("{title.upper}", {"title": "X"})


def test_render_template_rejects_index_access() -> None:
    with pytest.raises(ValidationFailedError):
        render_template("{title[0]}", {"title": "X"})


def test_render_template_rejects_positional_fields() -> None:
    with pytest.raises(ValidationFailedError):
        render_template("{} {title}", {"title": "X"})


def test_render_template_rejects_conversions() -> None:
    with pytest.raises(ValidationFailedError):
        render_template("{title!r}", {"title": "X"})


def test_render_template_raises_when_a_referenced_variable_has_no_value() -> None:
    with pytest.raises(ValidationFailedError):
        render_template("{title} {season}", {"title": "X"})


def test_validate_template_accepts_the_default_templates() -> None:
    validate_template("{title} ({year})")
    validate_template("{title} ({year}) - {source}-{resolution}")
    validate_template("{title} ({year})/Season {season:02d}")
    validate_template("{title} - S{season:02d}E{episode:02d}")


def test_validate_template_catches_an_unknown_variable_without_a_real_job() -> None:
    # This is the phase-06 acceptance criterion: an unknown variable is
    # caught at settings-save time (this call), not later at organize time
    # against a real job's (possibly partial) variable dict.
    with pytest.raises(ValidationFailedError):
        validate_template("{title} ({yeaar})")


def test_validate_template_catches_a_bad_format_spec() -> None:
    with pytest.raises(ValidationFailedError):
        validate_template("{title:02d}")


# -- sanitize_segment ----------------------------------------------------


def test_sanitize_segment_strips_illegal_characters() -> None:
    assert sanitize_segment('Mission: Impossible <2>? "Ghost" | Protocol*') == (
        "Mission Impossible 2 Ghost Protocol"
    )


def test_sanitize_segment_preserves_umlauts() -> None:
    assert sanitize_segment("Das Böse Erwacht") == "Das Böse Erwacht"


def test_sanitize_segment_collapses_whitespace() -> None:
    assert sanitize_segment("Amelie   From    Montmartre") == "Amelie From Montmartre"


def test_sanitize_segment_strips_trailing_dots_and_spaces() -> None:
    assert sanitize_segment("Mr. Robot. . ") == "Mr. Robot"


def test_sanitize_segment_truncates_a_very_long_title_on_a_word_boundary() -> None:
    long_title = "Word " * 60  # 300 chars
    result = sanitize_segment(long_title)
    assert len(result) <= 180
    assert not result.endswith(" ")
    assert result == long_title[: len(result)].rstrip()


def test_sanitize_segment_removes_slashes_so_traversal_cannot_hide_in_one_segment() -> None:
    assert "/" not in sanitize_segment("../../etc")
    assert "\\" not in sanitize_segment("..\\..\\etc")


# -- render_path -----------------------------------------------------------


def test_render_path_splits_on_slash_into_separate_segments() -> None:
    path = render_path(
        "{title} ({year})/Season {season:02d}", {"title": "The Wire", "year": 2002, "season": 1}
    )
    assert path == Path("The Wire (2002)", "Season 01")


def test_render_path_sanitizes_each_segment_independently() -> None:
    path = render_path("{title}/{source}", {"title": 'Se7en: "Redux"', "source": "Bluray"})
    assert path == Path("Se7en Redux", "Bluray")


# -- resolve_destination (path traversal) -----------------------------------


def test_resolve_destination_joins_a_well_behaved_relative_path(tmp_path: Path) -> None:
    result = resolve_destination(tmp_path, Path("Movies", "Amelie (2001)"))
    assert result == (tmp_path / "Movies" / "Amelie (2001)").resolve()


def test_resolve_destination_rejects_a_traversal_that_escapes_the_base_dir(
    tmp_path: Path,
) -> None:
    # A raw `..` component bypassing `sanitize_segment` (the primary
    # defense) must still be caught here -- architecture.md §8's backstop.
    with pytest.raises(ValidationFailedError):
        resolve_destination(tmp_path, Path("..", "..", "etc"))


def test_resolve_destination_rejects_an_absolute_path_escaping_the_base_dir(
    tmp_path: Path,
) -> None:
    escape_root = tmp_path.parent / "elsewhere"
    with pytest.raises(ValidationFailedError):
        resolve_destination(tmp_path, escape_root)


def test_title_of_dotdot_etc_cannot_escape_library_dir_end_to_end(tmp_path: Path) -> None:
    """The phase-06 acceptance criterion, exercised through the whole
    render_path -> resolve_destination pipeline rather than a raw Path."""
    relative = render_path("{title}", {"title": "../../etc"})
    result = resolve_destination(tmp_path, relative)
    assert tmp_path.resolve() in result.parents or result == tmp_path.resolve()
