from __future__ import annotations

from spindle.config import TranscodeConfig
from spindle.models.disc import DiscType
from spindle.services.transcoder import (
    NO_HARDWARE_PRESET,
    build_overrides,
    choose_encoder,
    remap_quality,
    resolve_preset_name,
)


def _config(**overrides: object) -> TranscodeConfig:
    return TranscodeConfig(**overrides)  # type: ignore[arg-type]


def test_resolve_preset_name_dvd_always_uses_the_remux_preset() -> None:
    assert resolve_preset_name(DiscType.DVD, _config()) == NO_HARDWARE_PRESET


def test_resolve_preset_name_uhd_bluray_uses_the_uhd_preset() -> None:
    config = _config(preset_uhd="Custom UHD")
    assert resolve_preset_name(DiscType.UHD_BLURAY, config) == "Custom UHD"


def test_resolve_preset_name_bluray_and_unknown_use_the_movie_preset() -> None:
    config = _config(preset_movie="Custom Movie")
    assert resolve_preset_name(DiscType.BLURAY, config) == "Custom Movie"
    assert resolve_preset_name(None, config) == "Custom Movie"


def test_choose_encoder_auto_prefers_nvenc_then_qsv_then_vce_then_software() -> None:
    config = _config(hardware_encoder="auto")
    assert choose_encoder(config, ["x265", "nvenc_h265", "qsv_h265"], "Spindle 1080p H.265") == (
        "nvenc_h265"
    )
    assert choose_encoder(config, ["x265", "qsv_h265", "vce_h265"], "Spindle 1080p H.265") == (
        "qsv_h265"
    )
    assert choose_encoder(config, ["x265", "vce_h265"], "Spindle 1080p H.265") == "vce_h265"


def test_choose_encoder_falls_back_to_x265_when_no_hardware_encoder_is_available() -> None:
    config = _config(hardware_encoder="auto")

    encoder = choose_encoder(config, ["x264", "x265"], "Spindle 1080p H.265")

    assert encoder == "x265"


def test_choose_encoder_never_uses_hardware_for_the_dvd_remux_preset() -> None:
    config = _config(hardware_encoder="auto")

    encoder = choose_encoder(config, ["x265", "nvenc_h265"], NO_HARDWARE_PRESET)

    assert encoder == "x265"


def test_choose_encoder_manual_selection_falls_back_when_unavailable() -> None:
    config = _config(hardware_encoder="nvenc")

    assert choose_encoder(config, ["x265", "nvenc_h265"], "Spindle 1080p H.265") == "nvenc_h265"
    assert choose_encoder(config, ["x265"], "Spindle 1080p H.265") == "x265"


def test_choose_encoder_none_always_uses_software() -> None:
    config = _config(hardware_encoder="none")

    assert choose_encoder(config, ["x265", "nvenc_h265"], "Spindle 1080p H.265") == "x265"


def test_remap_quality_applies_documented_hardware_offsets() -> None:
    assert remap_quality("nvenc_h265", 22) == 28
    assert remap_quality("qsv_h265", 22) == 26
    assert remap_quality("x265", 22) == 22


def test_build_overrides_includes_encoder_quality_and_audio_langs() -> None:
    config = _config(audio_langs=["deu", "eng"], keep_original_audio=True)

    overrides = build_overrides(config, "x265", 22)

    assert overrides == [
        "--encoder",
        "x265",
        "--quality",
        "22",
        "--audio-lang-list",
        "deu,eng",
        "--all-audio",
    ]


def test_build_overrides_uses_first_audio_when_original_audio_not_kept() -> None:
    config = _config(audio_langs=["eng"], keep_original_audio=False)

    overrides = build_overrides(config, "x265", 22)

    assert overrides[-1] == "--first-audio"
