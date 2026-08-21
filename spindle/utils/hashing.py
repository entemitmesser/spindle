"""OpenSubtitles "moviehash", per phase-05-metadata-subtitles.md task 2.

The published algorithm (opensubtitles.org's "Hashing for Dummies"): an
unsigned 64-bit sum of the file size plus every little-endian `uint64` word
in the first 64 KiB and every `uint64` word in the last 64 KiB. On files
under 128 KiB those two windows overlap — the reference implementation
re-seeks to `max(0, filesize - 65536)` rather than skipping the tail read, so
this does the same instead of special-casing small files.
"""

from __future__ import annotations

import struct
from pathlib import Path

_CHUNK_BYTES = 64 * 1024
_MASK_64 = (1 << 64) - 1


def _sum_le_u64_words(data: bytes) -> int:
    word_count = len(data) // 8
    if word_count == 0:
        return 0
    words: tuple[int, ...] = struct.unpack(f"<{word_count}Q", data[: word_count * 8])
    return sum(words) & _MASK_64


def opensubtitles_hash(path: Path) -> str:
    """Lowercase, zero-padded 16-hex-digit hash, as OpenSubtitles expects it
    in the `moviehash` search parameter."""
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(_CHUNK_BYTES)
        f.seek(max(0, size - _CHUNK_BYTES))
        tail = f.read(_CHUNK_BYTES)

    total = (size + _sum_le_u64_words(head) + _sum_le_u64_words(tail)) & _MASK_64
    return f"{total:016x}"
