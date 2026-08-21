from __future__ import annotations

import json
from pathlib import Path

from spindle.presets import find_preset, list_builtin_presets, list_presets

_BUILTIN_NAMES = {
    "Spindle 1080p H.265",
    "Spindle 2160p H.265 10-bit",
    "Spindle DVD Remux",
    "Spindle Passthrough",
}


def test_list_builtin_presets_includes_all_four_shipped_presets() -> None:
    names = {p.name for p in list_builtin_presets()}

    assert names == _BUILTIN_NAMES
    assert all(p.builtin for p in list_builtin_presets())


def test_list_presets_merges_in_a_user_preset(tmp_path: Path) -> None:
    user_dir = tmp_path / "presets"
    user_dir.mkdir()
    (user_dir / "custom.json").write_text(
        json.dumps({"PresetList": [{"PresetName": "My Custom Preset"}]}), encoding="utf-8"
    )

    presets = list_presets(user_dir)

    names = {p.name for p in presets}
    assert "My Custom Preset" in names
    custom = next(p for p in presets if p.name == "My Custom Preset")
    assert custom.builtin is False


def test_list_presets_builtin_wins_a_name_collision(tmp_path: Path) -> None:
    user_dir = tmp_path / "presets"
    user_dir.mkdir()
    (user_dir / "shadow.json").write_text(
        json.dumps({"PresetList": [{"PresetName": "Spindle 1080p H.265"}]}), encoding="utf-8"
    )

    presets = list_presets(user_dir)

    matches = [p for p in presets if p.name == "Spindle 1080p H.265"]
    assert len(matches) == 1
    assert matches[0].builtin is True


def test_list_presets_with_no_user_dir_returns_only_builtins() -> None:
    assert {p.name for p in list_presets(None)} == _BUILTIN_NAMES


def test_find_preset_returns_none_for_an_unknown_name() -> None:
    assert find_preset("Does Not Exist") is None


def test_find_preset_finds_a_builtin_preset() -> None:
    preset = find_preset("Spindle DVD Remux")

    assert preset is not None
    assert preset.file.name == "spindle-dvd-remux.json"
