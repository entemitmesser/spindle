"""Built-in HandBrake presets shipped with Spindle (phase-04 task 2), plus the
loader that merges them with user-supplied presets from `data_dir/presets/`.

`spindle/services/transcoder.py::resolve_preset` uses `find_preset` to turn a
configured preset name into the file `HandBrakeAdapter.encode()` should pass
as `--preset-import-file`. `GET /api/v1/settings/presets` (a later phase)
will use `list_presets` directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_BUILTIN_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class PresetInfo:
    name: str
    file: Path
    builtin: bool


def _preset_names(file: Path) -> list[str]:
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p["PresetName"] for p in payload.get("PresetList", []) if "PresetName" in p]


def list_builtin_presets() -> list[PresetInfo]:
    presets: list[PresetInfo] = []
    for file in sorted(_BUILTIN_DIR.glob("*.json")):
        presets.extend(
            PresetInfo(name=name, file=file, builtin=True) for name in _preset_names(file)
        )
    return presets


def list_presets(user_dir: Path | None = None) -> list[PresetInfo]:
    """Built-in presets plus any `*.json` in `user_dir`. A user preset whose
    name collides with a built-in is dropped — built-ins always win, so a
    stray file can't silently shadow `Spindle 1080p H.265` for everyone."""
    presets = list_builtin_presets()
    if user_dir is None or not user_dir.is_dir():
        return presets

    seen = {p.name for p in presets}
    for file in sorted(user_dir.glob("*.json")):
        for name in _preset_names(file):
            if name in seen:
                continue
            presets.append(PresetInfo(name=name, file=file, builtin=False))
            seen.add(name)
    return presets


def find_preset(name: str, user_dir: Path | None = None) -> PresetInfo | None:
    return next((p for p in list_presets(user_dir) if p.name == name), None)
