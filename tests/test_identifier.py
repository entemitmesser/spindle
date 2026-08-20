from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from spindle.adapters._discfs import DiscFsEntry
from spindle.adapters.optical import DiscStatus, DvdTitleInfo, OpticalAdapter
from spindle.models.disc import DiscType
from spindle.services.identifier import classify, compute_fingerprint, runtime_agreement, score


class TestClassify:
    def test_audio_disc_status_wins_regardless_of_entries(self) -> None:
        assert (
            classify(disc_status=DiscStatus.AUDIO, root_entries=["VIDEO_TS"], size_bytes=None)
            == DiscType.AUDIO_CD
        )

    def test_bdmv_root_is_bluray(self) -> None:
        assert (
            classify(
                disc_status=DiscStatus.DATA,
                root_entries=["BDMV", "CERTIFICATE"],
                size_bytes=20 * 1024**3,
            )
            == DiscType.BLURAY
        )

    def test_bdmv_over_60gb_is_uhd(self) -> None:
        assert (
            classify(disc_status=DiscStatus.DATA, root_entries=["BDMV"], size_bytes=70 * 1024**3)
            == DiscType.UHD_BLURAY
        )

    def test_video_ts_root_is_dvd(self) -> None:
        assert (
            classify(
                disc_status=DiscStatus.DATA, root_entries=["VIDEO_TS", "AUDIO_TS"], size_bytes=None
            )
            == DiscType.DVD
        )

    def test_unrecognized_entries_are_data(self) -> None:
        assert (
            classify(disc_status=DiscStatus.DATA, root_entries=["SOME_FILE.TXT"], size_bytes=None)
            == DiscType.DATA
        )

    def test_no_entries_is_unknown(self) -> None:
        assert (
            classify(disc_status=DiscStatus.UNKNOWN, root_entries=[], size_bytes=None)
            == DiscType.UNKNOWN
        )


class TestRuntimeAgreement:
    def test_none_when_either_side_missing(self) -> None:
        assert runtime_agreement(None, 120.0) is None
        assert runtime_agreement(120.0, None) is None

    def test_within_five_percent_is_perfect(self) -> None:
        assert runtime_agreement(120.0, 121.0) == 1.0

    def test_far_off_is_zero(self) -> None:
        assert runtime_agreement(10.0, 120.0) == 0.0


class TestScore:
    def test_correct_match_on_messy_label_scores_above_threshold(self) -> None:
        result = score(
            normalized_label="Die Harde",
            candidate_title="Die Hard",
            candidate_year=1988,
            disc_year_hint=1988,
            candidate_runtime_minutes=132.0,
            disc_runtime_minutes=133.0,
            popularity_prior=0.8,
        )
        assert result >= 0.75

    def test_ambiguous_match_scores_below_threshold(self) -> None:
        result = score(
            normalized_label="Untitled",
            candidate_title="Completely Different Film",
            candidate_year=1975,
            disc_year_hint=2020,
            candidate_runtime_minutes=45.0,
            disc_runtime_minutes=130.0,
            popularity_prior=0.1,
        )
        assert result < 0.75

    def test_missing_runtime_is_dropped_and_renormalized_not_zeroed(self) -> None:
        # Same everything, except one has a wildly mismatched runtime (forces
        # runtime_agreement to 0.0) and the other has no runtime data at all
        # (runtime_agreement is None, so the term is dropped and the
        # remaining weights renormalized). Feeding a missing term in as a
        # zero would score no better than the mismatched-runtime case; the
        # renormalized version must score strictly higher.
        forced_zero = score(
            normalized_label="Amelie",
            candidate_title="Amelie",
            candidate_year=2001,
            disc_year_hint=2001,
            candidate_runtime_minutes=1000.0,
            disc_runtime_minutes=100.0,
            popularity_prior=0.5,
        )
        without_runtime = score(
            normalized_label="Amelie",
            candidate_title="Amelie",
            candidate_year=2001,
            disc_year_hint=2001,
            candidate_runtime_minutes=None,
            disc_runtime_minutes=None,
            popularity_prior=0.5,
        )
        assert without_runtime > forced_zero


class TestComputeFingerprint:
    def test_dvd_fingerprint_is_stable_for_same_titles(self) -> None:
        adapter = MagicMock(spec=OpticalAdapter)
        adapter.read_dvd_title_info.return_value = [
            DvdTitleInfo(index=1, length_seconds=5710.04),
            DvdTitleInfo(index=2, length_seconds=120.24),
        ]
        fp1 = compute_fingerprint(adapter, "/dev/sr0", DiscType.DVD)
        fp2 = compute_fingerprint(adapter, "/dev/sr0", DiscType.DVD)
        assert fp1 is not None
        assert fp1.startswith("sha1:")
        assert fp1 == fp2

    def test_dvd_fingerprint_none_when_lsdvd_unavailable(self) -> None:
        adapter = MagicMock(spec=OpticalAdapter)
        adapter.read_dvd_title_info.return_value = []
        assert compute_fingerprint(adapter, "/dev/sr0", DiscType.DVD) is None

    def test_bluray_fingerprint_hashes_mpls_sizes_and_mtimes(self) -> None:
        adapter = MagicMock(spec=OpticalAdapter)
        adapter.list_entries.return_value = [
            DiscFsEntry(
                name="00001.MPLS",
                is_dir=False,
                size_bytes=123,
                mtime=datetime(2020, 1, 2, tzinfo=UTC),
            ),
            DiscFsEntry(name="PLAYLIST", is_dir=True, size_bytes=None, mtime=None),
        ]
        fp = compute_fingerprint(adapter, "/dev/sr0", DiscType.BLURAY)
        assert fp is not None
        assert fp.startswith("sha1:")
        adapter.list_entries.assert_called_once_with("/dev/sr0", ("BDMV", "PLAYLIST"))

    def test_fingerprint_failure_returns_none_not_raise(self) -> None:
        adapter = MagicMock(spec=OpticalAdapter)
        adapter.read_dvd_title_info.side_effect = RuntimeError("boom")
        assert compute_fingerprint(adapter, "/dev/sr0", DiscType.DVD) is None

    def test_unknown_disc_type_has_no_fingerprint(self) -> None:
        adapter = MagicMock(spec=OpticalAdapter)
        assert compute_fingerprint(adapter, "/dev/sr0", DiscType.UNKNOWN) is None
