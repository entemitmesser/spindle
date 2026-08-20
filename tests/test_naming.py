from __future__ import annotations

import pytest

from spindle.utils.naming import DiscLabelHints, normalize_disc_label

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
