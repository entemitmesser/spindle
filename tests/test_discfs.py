from __future__ import annotations

import struct

from spindle.adapters._discfs import (
    Iso9660Filesystem,
    UdfFilesystem,
    open_disc_filesystem,
)

SECTOR = 2048


def _both32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


def _both16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def _iso_dir_record(name: bytes, extent_lba: int, data_len: int, *, is_dir: bool) -> bytes:
    flags = 0x02 if is_dir else 0x00
    id_len = len(name)
    before_pad = 33 + id_len
    record = bytearray()
    record.append(before_pad + (before_pad % 2))
    record.append(0)
    record += _both32(extent_lba)
    record += _both32(data_len)
    record += bytes(7)
    record.append(flags)
    record.append(0)
    record.append(0)
    record += _both16(1)
    record.append(id_len)
    record += name
    if before_pad % 2 == 1:
        record.append(0)
    return bytes(record)


class _FakeDisc:
    """A dict-of-sectors fake block device: `read(lba) -> 2048 bytes`."""

    def __init__(self) -> None:
        self._sectors: dict[int, bytes] = {}

    def put(self, lba: int, data: bytes) -> None:
        assert len(data) <= SECTOR
        self._sectors[lba] = data.ljust(SECTOR, b"\x00")

    def read(self, lba: int) -> bytes:
        return self._sectors.get(lba, bytes(SECTOR))


def _build_iso9660_disc() -> _FakeDisc:
    disc = _FakeDisc()

    root_record = _iso_dir_record(b"\x00", extent_lba=20, data_len=SECTOR, is_dir=True)
    pvd = bytearray(SECTOR)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[40:72] = b"TESTMOVIE".ljust(32)
    pvd[156 : 156 + len(root_record)] = root_record
    disc.put(16, bytes(pvd))

    root_dir = (
        _iso_dir_record(b"\x00", extent_lba=20, data_len=SECTOR, is_dir=True)
        + _iso_dir_record(b"\x01", extent_lba=20, data_len=SECTOR, is_dir=True)
        + _iso_dir_record(b"VIDEO_TS", extent_lba=21, data_len=SECTOR, is_dir=True)
    )
    disc.put(20, root_dir)

    video_ts_dir = _iso_dir_record(
        b"\x00", extent_lba=21, data_len=SECTOR, is_dir=True
    ) + _iso_dir_record(b"\x01", extent_lba=21, data_len=SECTOR, is_dir=True)
    disc.put(21, video_ts_dir)

    return disc


def test_iso9660_reads_volume_label_and_root_entries() -> None:
    disc = _build_iso9660_disc()
    fs = open_disc_filesystem(disc.read)

    assert isinstance(fs, Iso9660Filesystem)
    assert fs.volume_id == "TESTMOVIE"

    names = {e.name for e in fs.list_dir([])}
    assert names == {"VIDEO_TS"}

    assert fs.list_dir(["VIDEO_TS"]) == []
    assert fs.list_dir(["NOPE"]) == []


def _udf_fid(name: bytes, *, is_dir: bool, icb_block: int, icb_partition_ref: int = 0) -> bytes:
    characteristics = 0x02 if is_dir else 0x00
    l_fi = len(name)
    record = bytearray(16)  # descriptor tag stub; only tag id (offset 0) is read
    struct.pack_into("<H", record, 0, 257)
    record += struct.pack("<H", 1)  # FileVersionNumber
    record.append(characteristics)
    record.append(l_fi)
    record += struct.pack("<IIH6s", 0, icb_block, icb_partition_ref, bytes(6))  # ICB long_ad
    record += struct.pack("<H", 0)  # L_IU
    record += name
    total_len = 38 + l_fi
    padded = -(-total_len // 4) * 4
    record += bytes(padded - len(record))
    return bytes(record)


def _udf_file_entry(
    *, alloc_type: int, extent_block: int, extent_len: int, partition_ref: int = 0
) -> bytes:
    entry = bytearray(SECTOR)
    struct.pack_into("<H", entry, 0, 261)
    struct.pack_into("<H", entry, 34, alloc_type)
    struct.pack_into("<I", entry, 168, 0)  # L_EA
    struct.pack_into("<I", entry, 172, 8)  # L_AD (one short_ad)
    if alloc_type == 0:
        struct.pack_into("<II", entry, 176, extent_len, extent_block)
    else:
        struct.pack_into("<IIH", entry, 176, extent_len, extent_block, partition_ref)
    return bytes(entry)


def _udf_leaf_file_entry(*, size: int, year: int, month: int, day: int) -> bytes:
    entry = bytearray(SECTOR)
    struct.pack_into("<H", entry, 0, 261)
    struct.pack_into("<Q", entry, 56, size)
    struct.pack_into("<h", entry, 86, year)
    entry[88] = month
    entry[89] = day
    entry[90] = 3
    entry[91] = 4
    entry[92] = 5
    return bytes(entry)


def _build_udf_disc() -> _FakeDisc:
    disc = _FakeDisc()
    partition_start = 100

    avdp = bytearray(SECTOR)
    struct.pack_into("<H", avdp, 0, 2)
    struct.pack_into("<II", avdp, 16, 3 * SECTOR, 32)  # main VDS: length, location
    disc.put(256, bytes(avdp))

    partition_desc = bytearray(SECTOR)
    struct.pack_into("<H", partition_desc, 0, 5)
    struct.pack_into("<H", partition_desc, 22, 0)  # partition number
    struct.pack_into("<I", partition_desc, 188, partition_start)
    disc.put(32, bytes(partition_desc))

    lvd = bytearray(SECTOR)
    struct.pack_into("<H", lvd, 0, 6)
    dstring = bytearray(128)
    text = b"TESTBD"
    dstring[0] = 8
    dstring[1 : 1 + len(text)] = text
    dstring[127] = 1 + len(text)
    lvd[84:212] = bytes(dstring)
    struct.pack_into("<IH", lvd, 252, 0, 0)  # FSD block, partition ref
    disc.put(33, bytes(lvd))

    terminator = bytearray(SECTOR)
    struct.pack_into("<H", terminator, 0, 8)
    disc.put(34, bytes(terminator))

    fsd = bytearray(SECTOR)
    struct.pack_into("<H", fsd, 0, 256)
    struct.pack_into("<IH", fsd, 404, 1, 0)  # root ICB: block, partition ref
    disc.put(partition_start + 0, bytes(fsd))

    # Root directory File Entry -> FID extent at block 2
    disc.put(partition_start + 1, _udf_file_entry(alloc_type=0, extent_block=2, extent_len=SECTOR))
    disc.put(partition_start + 2, _udf_fid(b"\x08BDMV", is_dir=True, icb_block=3))

    # BDMV directory File Entry -> FID extent at block 4
    disc.put(partition_start + 3, _udf_file_entry(alloc_type=0, extent_block=4, extent_len=SECTOR))
    disc.put(partition_start + 4, _udf_fid(b"\x08PLAYLIST", is_dir=True, icb_block=5))

    # PLAYLIST directory File Entry -> FID extent at block 6
    disc.put(partition_start + 5, _udf_file_entry(alloc_type=0, extent_block=6, extent_len=SECTOR))
    disc.put(partition_start + 6, _udf_fid(b"\x0800001.MPLS", is_dir=False, icb_block=7))

    # The .mpls file's own File Entry, used by stat()
    disc.put(partition_start + 7, _udf_leaf_file_entry(size=123456, year=2020, month=1, day=2))

    return disc


def test_udf_walks_bdmv_playlist_and_stats_leaf_file() -> None:
    disc = _build_udf_disc()
    fs = open_disc_filesystem(disc.read)

    assert isinstance(fs, UdfFilesystem)
    assert fs.volume_id == "TESTBD"

    root = fs.list_dir([])
    assert [e.name for e in root] == ["BDMV"]
    assert root[0].is_dir is True

    playlist = fs.list_dir(["BDMV", "PLAYLIST"])
    assert len(playlist) == 1
    assert playlist[0].name == "00001.MPLS"
    assert playlist[0].is_dir is False
    assert playlist[0].size_bytes is None  # not stat()'d yet

    stated = fs.stat(playlist[0])
    assert stated.size_bytes == 123456
    assert stated.mtime is not None
    assert (stated.mtime.year, stated.mtime.month, stated.mtime.day) == (2020, 1, 2)


def test_open_disc_filesystem_returns_none_for_garbage() -> None:
    disc = _FakeDisc()
    assert open_disc_filesystem(disc.read) is None
