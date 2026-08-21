"""`opensubtitles_hash` per phase-05-metadata-subtitles.md task 2 ("Implement
it in `utils/hashing.py` with a known-value test vector").

The algorithm itself is unambiguous and well documented (opensubtitles.org's
"Hashing for Dummies": unsigned-64-bit sum of filesize + every little-endian
uint64 word in the first and last 64 KiB), but reproducing the OpenSubtitles
site's own published sample-file vector isn't possible without that exact
binary file, which isn't available in this environment. Instead these tests
pin down two independently-verifiable golden values:

- an all-zero file, where every summed word is 0 so the hash must equal the
  file size itself, by construction;
- a non-trivial byte pattern, hashed a second time by a deliberately
  different (naive, non-vectorized) reference loop written directly in the
  test, so a bug in either implementation's indexing/looping would show up
  as a mismatch between the two.
"""

from __future__ import annotations

import struct
from pathlib import Path

from spindle.utils.hashing import opensubtitles_hash


def test_all_zero_file_hash_equals_its_size(tmp_path: Path) -> None:
    # Every summed 64-bit word is 0 for both the head and tail windows, so
    # the hash reduces to exactly `size`, independent of the algorithm's
    # windowing/overlap behavior.
    size = 65536
    path = tmp_path / "zeros.bin"
    path.write_bytes(b"\x00" * size)

    assert opensubtitles_hash(path) == f"{size:016x}"
    assert opensubtitles_hash(path) == "0000000000010000"


def test_matches_an_independent_reference_implementation(tmp_path: Path) -> None:
    size = 70000  # > 64 KiB so the head/tail windows don't overlap
    content = bytes((i * 2654435761) & 0xFF for i in range(size))
    path = tmp_path / "pattern.bin"
    path.write_bytes(content)

    expected = _reference_hash(content)
    assert opensubtitles_hash(path) == expected


def test_small_file_reuses_the_same_bytes_for_head_and_tail(tmp_path: Path) -> None:
    # Files under 128 KiB: the reference algorithm re-seeks to
    # max(0, size - 65536), which for a small file is offset 0 — the same
    # bytes get summed twice, not skipped.
    content = b"\x01\x00\x00\x00\x00\x00\x00\x00" * 4  # eight 1s as u64 LE words
    path = tmp_path / "small.bin"
    path.write_bytes(content)

    size = len(content)
    expected_total = (size + 4 + 4) & ((1 << 64) - 1)  # summed twice (head == tail)
    assert opensubtitles_hash(path) == f"{expected_total:016x}"


def test_hash_is_stable_across_repeated_calls(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"some pretend video bytes " * 5000)

    assert opensubtitles_hash(path) == opensubtitles_hash(path)


def _reference_hash(content: bytes) -> str:
    mask = (1 << 64) - 1
    size = len(content)
    total = size

    head = content[:65536]
    for i in range(0, len(head) - 7, 8):
        (word,) = struct.unpack("<Q", head[i : i + 8])
        total = (total + word) & mask

    tail_start = max(0, size - 65536)
    tail = content[tail_start : tail_start + 65536]
    for i in range(0, len(tail) - 7, 8):
        (word,) = struct.unpack("<Q", tail[i : i + 8])
        total = (total + word) & mask

    return f"{total:016x}"
