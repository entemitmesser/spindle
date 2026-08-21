"""Encoder selection, per phase-04-transcode-engine.md task 3.

Deliberately pure/decoupled from `HandBrakeAdapter` — `choose_encoder` takes
the list of already-probed-working encoder names rather than calling the
adapter itself, so the policy is unit-testable without a subprocess.
"""

from __future__ import annotations

from collections.abc import Sequence

from spindle.config import TranscodeConfig
from spindle.models.disc import DiscType

# Never use hardware for this preset — the source is too small to benefit
# and hardware encoders handle interlaced SD badly (phase-04 task 3).
NO_HARDWARE_PRESET = "Spindle DVD Remux"

_AUTO_PREFERENCE = ("nvenc_h265", "qsv_h265", "vce_h265")
_MANUAL_ENCODER = {
    "qsv": "qsv_h265",
    "nvenc": "nvenc_h265",
    "vaapi": "vaapi_h265",
}
_SOFTWARE_ENCODER = "x265"

# HandBrake's hardware quality scales (NVENC CQ, QSV ICQ, ...) aren't
# numerically comparable to x265's RF, so a straight RF passthrough would
# under- or over-shoot quality. These offsets land roughly on the same
# visual quality as the configured RF (nvenc_cq ~= rf + 6, qsv_icq ~= rf + 4,
# per vendor guidance); VCE has no documented equivalent, so it reuses QSV's
# offset as a conservative estimate rather than inventing a number.
_QUALITY_OFFSETS: dict[str, int] = {
    "nvenc_h265": 6,
    "qsv_h265": 4,
    "vce_h265": 4,
}


def resolve_preset_name(disc_type: DiscType | None, config: TranscodeConfig) -> str:
    if disc_type == DiscType.DVD:
        return NO_HARDWARE_PRESET
    if disc_type == DiscType.UHD_BLURAY:
        return config.preset_uhd
    return config.preset_movie


def choose_encoder(config: TranscodeConfig, available: Sequence[str], preset_name: str) -> str:
    if preset_name == NO_HARDWARE_PRESET or config.hardware_encoder == "none":
        return _SOFTWARE_ENCODER

    if config.hardware_encoder != "auto":
        wanted = _MANUAL_ENCODER[config.hardware_encoder]
        return wanted if wanted in available else _SOFTWARE_ENCODER

    for candidate in _AUTO_PREFERENCE:
        if candidate in available:
            return candidate
    return _SOFTWARE_ENCODER


def remap_quality(encoder: str, rf: int) -> int:
    return rf + _QUALITY_OFFSETS.get(encoder, 0)


def build_overrides(
    config: TranscodeConfig, encoder: str, quality: int, *, keep_forced_subs: bool = True
) -> list[str]:
    overrides = ["--encoder", encoder, "--quality", str(quality)]
    langs = list(dict.fromkeys(config.audio_langs))
    if langs:
        overrides += ["--audio-lang-list", ",".join(langs)]
    overrides.append("--all-audio" if config.keep_original_audio else "--first-audio")
    if keep_forced_subs:
        # Keep disc subtitle tracks flagged forced as soft tracks rather than
        # burning them in — burned-in subs are irreversible and Jellyfin
        # can't turn them off (phase-05-metadata-subtitles.md task 1).
        overrides += ["--all-subtitles", "--subtitle-forced", "--subtitle-burned=none"]
    return overrides
