"""Mount-free ISO9660/UDF directory readers used by `OpticalAdapter`.

Split out of `optical.py` so the parsing logic can be unit tested against
synthetic sector data without a real block device: every reader here takes a
`SectorReader` callable (`lba -> 2048 raw bytes`) instead of a file
descriptor. `optical.py` is the only caller that ever supplies a real one
(backed by `os.pread` against `/dev/sr*`).

DVD-Video discs are ISO9660. Blu-ray discs are UDF and have no ISO9660
bridge, so both readers are needed to satisfy `classify()`'s "root contains
BDMV/VIDEO_TS" signal and the fingerprint stage's BDMV/PLAYLIST walk.
"""

from __future__ import annotations

import dataclasses
import struct
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

SECTOR_SIZE = 2048

SectorReader = Callable[[int], bytes]


@dataclasses.dataclass(frozen=True)
class DiscFsEntry:
    name: str
    is_dir: bool
    size_bytes: int | None
    mtime: datetime | None
    # Opaque handle back into the filesystem that produced this entry (an
    # extent locator). Only meaningful to that filesystem's own list_dir/stat.
    icb: object | None = None


class DiscFilesystem(Protocol):
    def list_dir(self, path: Sequence[str]) -> list[DiscFsEntry]: ...
    def stat(self, entry: DiscFsEntry) -> DiscFsEntry: ...
    @property
    def volume_id(self) -> str | None: ...


def _read_sectors(read: SectorReader, lba: int, count: int) -> bytes:
    return b"".join(read(lba + i) for i in range(max(count, 1)))


def _ceil_sectors(length: int) -> int:
    return max(-(-length // SECTOR_SIZE), 1)


# --------------------------------------------------------------------------
# ISO9660 (DVD-Video, and any bridged UDF disc)
# --------------------------------------------------------------------------


def _parse_iso_datetime(raw: bytes) -> datetime | None:
    if len(raw) < 6:
        return None
    year, month, day, hour, minute, second = 1900 + raw[0], raw[1], raw[2], raw[3], raw[4], raw[5]
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None


class Iso9660Filesystem:
    def __init__(self, read: SectorReader, volume_id: str | None, root: tuple[int, int]) -> None:
        self._read = read
        self._volume_id = volume_id
        self._root = root

    @property
    def volume_id(self) -> str | None:
        return self._volume_id

    @classmethod
    def try_open(cls, read: SectorReader) -> Iso9660Filesystem | None:
        pvd = read(16)
        if len(pvd) < 190 or pvd[0] != 1 or pvd[1:6] != b"CD001":
            return None
        volume_id = pvd[40:72].decode("ascii", errors="replace").strip() or None
        root_record = pvd[156:190]
        root_lba = struct.unpack_from("<I", root_record, 2)[0]
        root_len = struct.unpack_from("<I", root_record, 10)[0]
        return cls(read, volume_id, (root_lba, root_len))

    def list_dir(self, path: Sequence[str]) -> list[DiscFsEntry]:
        loc: object = self._root
        entries = self._children(loc)
        for segment in path:
            match = next(
                (e for e in entries if e.is_dir and e.name.lower() == segment.lower()), None
            )
            if match is None:
                return []
            entries = self._children(match.icb)
        return entries

    def stat(self, entry: DiscFsEntry) -> DiscFsEntry:
        return entry

    def _children(self, loc: object) -> list[DiscFsEntry]:
        assert isinstance(loc, tuple)  # noqa: S101 -- internal invariant, not user input
        lba, length = loc
        data = _read_sectors(self._read, lba, _ceil_sectors(length))
        entries: list[DiscFsEntry] = []
        pos = 0
        while pos < len(data):
            record_len = data[pos]
            if record_len == 0:
                pos = ((pos // SECTOR_SIZE) + 1) * SECTOR_SIZE
                continue
            record = data[pos : pos + record_len]
            pos += record_len
            if len(record) < 34:
                break
            extent_lba = struct.unpack_from("<I", record, 2)[0]
            data_len = struct.unpack_from("<I", record, 10)[0]
            flags = record[25]
            id_len = record[32]
            file_id = record[33 : 33 + id_len]
            if id_len == 1 and file_id in (b"\x00", b"\x01"):
                continue  # "." / ".."
            name = file_id.decode("ascii", errors="replace").split(";", 1)[0]
            entries.append(
                DiscFsEntry(
                    name=name,
                    is_dir=bool(flags & 0x02),
                    size_bytes=data_len,
                    mtime=_parse_iso_datetime(record[18:25]),
                    icb=(extent_lba, data_len),
                )
            )
        return entries


# --------------------------------------------------------------------------
# UDF (Blu-ray)
# --------------------------------------------------------------------------

_TAG_PARTITION_DESCRIPTOR = 5
_TAG_LOGICAL_VOLUME_DESCRIPTOR = 6
_TAG_TERMINATING_DESCRIPTOR = 8
_TAG_FILE_SET_DESCRIPTOR = 256
_TAG_FILE_IDENTIFIER_DESCRIPTOR = 257
_TAG_FILE_ENTRY = 261

_AVDP_LBA = 256


def _tag_id(data: bytes) -> int:
    return struct.unpack_from("<H", data, 0)[0] if len(data) >= 2 else 0


def _parse_udf_timestamp(raw: bytes) -> datetime | None:
    if len(raw) < 9:
        return None
    year = struct.unpack_from("<h", raw, 2)[0]
    month, day, hour, minute, second = raw[4], raw[5], raw[6], raw[7], raw[8]
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None


def _decode_udf_name(name_bytes: bytes) -> str:
    if not name_bytes:
        return ""
    compression_id, raw = name_bytes[0], name_bytes[1:]
    if compression_id == 16:
        return raw.decode("utf-16-be", errors="replace")
    return raw.decode("latin-1", errors="replace")


def _decode_udf_dstring(raw: bytes) -> str | None:
    if len(raw) < 2 or raw[0] not in (8, 16):
        return None
    length = raw[-1]
    body = raw[1 : max(length, 1)]
    text = _decode_udf_name(bytes([raw[0]]) + body)
    return text.strip("\x00").strip() or None


class UdfFilesystem:
    def __init__(
        self,
        read: SectorReader,
        volume_id: str | None,
        partitions: dict[int, int],
        root: tuple[int, int],
    ) -> None:
        self._read = read
        self._volume_id = volume_id
        self._partitions = partitions
        self._root = root

    @property
    def volume_id(self) -> str | None:
        return self._volume_id

    @classmethod
    def try_open(cls, read: SectorReader) -> UdfFilesystem | None:
        try:
            return cls._try_open(read)
        except (struct.error, IndexError):
            return None

    @classmethod
    def _try_open(cls, read: SectorReader) -> UdfFilesystem | None:
        avdp = read(_AVDP_LBA)
        if _tag_id(avdp) != 2:
            return None
        vds_length, vds_location = struct.unpack_from("<II", avdp, 16)

        partitions: dict[int, int] = {}
        lvd_fsd_block: int | None = None
        lvd_fsd_partition_ref: int | None = None
        volume_id: str | None = None

        for sector_lba in range(vds_location, vds_location + _ceil_sectors(vds_length)):
            sector = read(sector_lba)
            tag = _tag_id(sector)
            if tag == _TAG_TERMINATING_DESCRIPTOR:
                break
            if tag == _TAG_PARTITION_DESCRIPTOR:
                partition_number = struct.unpack_from("<H", sector, 22)[0]
                starting_location = struct.unpack_from("<I", sector, 188)[0]
                partitions[partition_number] = starting_location
            elif tag == _TAG_LOGICAL_VOLUME_DESCRIPTOR:
                volume_id = _decode_udf_dstring(sector[84:212])
                lvd_fsd_block, lvd_fsd_partition_ref = struct.unpack_from("<IH", sector, 252)

        if lvd_fsd_block is None or lvd_fsd_partition_ref is None:
            return None
        partition_start = partitions.get(lvd_fsd_partition_ref)
        if partition_start is None:
            return None

        fsd = read(partition_start + lvd_fsd_block)
        if _tag_id(fsd) != _TAG_FILE_SET_DESCRIPTOR:
            return None
        root_block, root_partition_ref = struct.unpack_from("<IH", fsd, 404)

        return cls(read, volume_id, partitions, (root_partition_ref, root_block))

    def list_dir(self, path: Sequence[str]) -> list[DiscFsEntry]:
        entries = self._children(self._root)
        for segment in path:
            match = next(
                (e for e in entries if e.is_dir and e.name.lower() == segment.lower()), None
            )
            if match is None:
                return []
            assert isinstance(match.icb, tuple)  # noqa: S101 -- internal invariant
            entries = self._children(match.icb)
        return entries

    def stat(self, entry: DiscFsEntry) -> DiscFsEntry:
        if entry.size_bytes is not None or entry.icb is None:
            return entry
        assert isinstance(entry.icb, tuple)  # noqa: S101 -- internal invariant
        partition_ref, block = entry.icb
        partition_start = self._partitions.get(partition_ref)
        if partition_start is None:
            return entry
        data = self._read(partition_start + block)
        if _tag_id(data) != _TAG_FILE_ENTRY:
            return entry
        size = struct.unpack_from("<Q", data, 56)[0]
        mtime = _parse_udf_timestamp(data[84:96])
        return dataclasses.replace(entry, size_bytes=size, mtime=mtime)

    def _children(self, loc: tuple[int, int]) -> list[DiscFsEntry]:
        try:
            return self._children_unsafe(loc)
        except (struct.error, IndexError):
            return []

    def _children_unsafe(self, loc: tuple[int, int]) -> list[DiscFsEntry]:
        partition_ref, block = loc
        partition_start = self._partitions.get(partition_ref)
        if partition_start is None:
            return []
        abs_lba = partition_start + block
        header = self._read(abs_lba)
        if _tag_id(header) != _TAG_FILE_ENTRY:
            return []

        l_ea = struct.unpack_from("<I", header, 168)[0]
        l_ad = struct.unpack_from("<I", header, 172)[0]
        total_len = 176 + l_ea + l_ad
        sectors_needed = _ceil_sectors(total_len)
        data = header if sectors_needed <= 1 else _read_sectors(self._read, abs_lba, sectors_needed)

        icb_flags = struct.unpack_from("<H", data, 34)[0]
        alloc_type = icb_flags & 0x7
        ad_bytes = data[176 + l_ea : 176 + l_ea + l_ad]

        if alloc_type == 3:
            dir_data = ad_bytes
        elif alloc_type in (0, 1):
            dir_data = self._read_extents(ad_bytes, alloc_type, partition_ref)
        else:
            return []  # extended_ad (type 2): not needed for BD-ROM movie discs

        return self._parse_fids(dir_data)

    def _read_extents(self, ad_bytes: bytes, alloc_type: int, default_partition_ref: int) -> bytes:
        stride = 8 if alloc_type == 0 else 16
        chunks: list[bytes] = []
        for i in range(0, len(ad_bytes) - stride + 1, stride):
            if alloc_type == 0:
                raw_len, extent_block = struct.unpack_from("<II", ad_bytes, i)
                extent_partition_ref = default_partition_ref
            else:
                raw_len, extent_block, extent_partition_ref = struct.unpack_from(
                    "<IIH", ad_bytes, i
                )
            if (raw_len >> 30) & 0x3 != 0:
                continue  # not allocated & recorded
            extent_len = raw_len & 0x3FFFFFFF
            if extent_len == 0:
                continue
            extent_start = self._partitions.get(extent_partition_ref)
            if extent_start is None:
                continue
            data = _read_sectors(self._read, extent_start + extent_block, _ceil_sectors(extent_len))
            chunks.append(data[:extent_len])
        return b"".join(chunks)

    def _parse_fids(self, dir_data: bytes) -> list[DiscFsEntry]:
        entries: list[DiscFsEntry] = []
        pos = 0
        while pos + 38 <= len(dir_data):
            if _tag_id(dir_data[pos:]) != _TAG_FILE_IDENTIFIER_DESCRIPTOR:
                break
            file_characteristics = dir_data[pos + 18]
            l_fi = dir_data[pos + 19]
            icb_block, icb_partition_ref = struct.unpack_from("<IH", dir_data, pos + 24)
            l_iu = struct.unpack_from("<H", dir_data, pos + 36)[0]
            name_start = pos + 38 + l_iu
            total_len = 38 + l_iu + l_fi
            padded_len = -(-total_len // 4) * 4
            if name_start + l_fi > len(dir_data):
                break
            name_bytes = dir_data[name_start : name_start + l_fi]
            pos += padded_len
            if l_fi == 0 or (file_characteristics & 0x08):
                continue  # parent ".." entry
            entries.append(
                DiscFsEntry(
                    name=_decode_udf_name(name_bytes),
                    is_dir=bool(file_characteristics & 0x02),
                    size_bytes=None,
                    mtime=None,
                    icb=(icb_partition_ref, icb_block),
                )
            )
        return entries


def open_disc_filesystem(read: SectorReader) -> DiscFilesystem | None:
    iso = Iso9660Filesystem.try_open(read)
    if iso is not None:
        return iso
    return UdfFilesystem.try_open(read)
