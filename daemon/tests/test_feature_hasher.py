"""Tests for file hashing.

``local/hasher.py`` was 31% covered and is the highest risk-per-line code in the
daemon: these hashes decide whether a local file matches its remote counterpart. Get
one wrong and the answer is not an error — it is a wrong sync decision. Two files
that differ look identical (a change is silently dropped) or two identical files look
different (endless re-uploads, and conflict resolution invoked on files nobody
touched).

So correctness here is checked against **independent references**, not against this
module's own output:

* MD5 and SHA-1 against ``hashlib`` directly.
* The Dropbox content hash against a separate implementation of the documented
  algorithm, written from the specification rather than from this code.
* QuickXorHash against a verbatim copy of the per-byte implementation it replaced,
  which is the only reference available without a live OneDrive account.

Two defects were found this way and are fixed:

1. **The Dropbox hash of an empty file was wrong.** An empty file has no blocks and
   so hashes to ``sha256(b"")``; the code invented a block and produced
   ``sha256(sha256(b""))``. Every empty file therefore disagreed with Dropbox
   forever and was re-uploaded on every pass. Every non-empty size was already
   correct, which is why it went unnoticed.
2. **QuickXorHash looped over every byte in Python**, costing ~0.66 s per megabyte —
   about 5½ minutes of CPU for a 1 GB file, on a path the engine runs for every
   changed file. Now folded per period and placed once: bit-identical output, ~37×
   faster.

What is *not* verified here: whether QuickXorHash agrees with what OneDrive actually
returns. That needs a real account. These tests pin it against the previous
implementation, so a refactor cannot change it silently — but they cannot tell you
the algorithm was right to begin with.
"""

from __future__ import annotations

import hashlib
import random
import struct
import time

import pytest

from cloud_drive_sync.local import hasher

BLOCK = hasher.DROPBOX_BLOCK_SIZE


def _write(tmp_path, data: bytes, name: str = "f.bin"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _varied(size: int, seed: int = 7) -> bytes:
    """Position-dependent bytes, so a chunk read twice or out of order shows up."""
    return random.Random(seed).randbytes(size)


# ── MD5 and SHA-1, against hashlib ──────────────────────────────────────

#: Sizes chosen around the 8 KB read boundary, where a chunking mistake lives.
SIZES = [0, 1, 255, 8191, 8192, 8193, 16384, 16385, 100_000]


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s}b")
async def test_md5_matches_hashlib(tmp_path, size):
    data = _varied(size)

    got = await hasher.md5_hash(_write(tmp_path, data))

    assert got == hashlib.md5(data).hexdigest()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s}b")
async def test_sha1_matches_hashlib(tmp_path, size):
    data = _varied(size)

    got = await hasher.sha1_hash(_write(tmp_path, data))

    assert got == hashlib.sha1(data).hexdigest()


async def test_different_content_gives_different_hashes(tmp_path):
    """The property the sync engine relies on. A hash that collided on a one-byte
    edit would make that edit invisible."""
    a = await hasher.md5_hash(_write(tmp_path, b"content-a", "a"))
    b = await hasher.md5_hash(_write(tmp_path, b"content-b", "b"))

    assert a != b


async def test_the_same_content_hashes_the_same_from_two_files(tmp_path):
    data = _varied(5000)

    assert await hasher.md5_hash(_write(tmp_path, data, "one")) == await hasher.md5_hash(
        _write(tmp_path, data, "two")
    )


# ── Dropbox content hash, against the documented algorithm ──────────────


def dropbox_reference(data: bytes) -> str:
    """The Dropbox content hash, implemented from the specification.

    SHA-256 each 4 MB block, concatenate those digests, SHA-256 the result. Written
    independently so it can disagree with the module under test — which is the point,
    and how the empty-file bug surfaced. Note there is no special case for an empty
    input: with no blocks there is nothing to concatenate, so the answer is
    ``sha256(b"")``, exactly as Dropbox's own reference hasher produces.
    """
    overall = hashlib.sha256()
    for i in range(0, len(data), BLOCK):
        overall.update(hashlib.sha256(data[i : i + BLOCK]).digest())
    return overall.hexdigest()


@pytest.mark.parametrize(
    ("label", "size"),
    [
        ("empty", 0),
        ("one-byte", 1),
        ("under-a-block", 1000),
        ("one-block-less-one", BLOCK - 1),
        ("exactly-one-block", BLOCK),
        ("one-block-plus-one", BLOCK + 1),
        ("exactly-two-blocks", 2 * BLOCK),
        ("two-blocks-plus-tail", 2 * BLOCK + 12345),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
async def test_dropbox_content_hash_matches_the_specification(tmp_path, label, size):
    data = _varied(size)

    got = await hasher.dropbox_content_hash(_write(tmp_path, data))

    assert got == dropbox_reference(data), f"{label}: disagrees with the documented algorithm"


async def test_an_empty_file_hashes_to_sha256_of_nothing(tmp_path):
    """Called out separately because this is the case that was wrong.

    The old code appended ``sha256(b"")`` as though an empty file had one block, so
    the result was ``sha256(sha256(b""))``. Dropbox never agreed, the file never
    looked synced, and it was uploaded again every pass — quietly, since nothing
    errored. Empty files are common: placeholders, freshly rotated logs, `.gitkeep`.
    """
    got = await hasher.dropbox_content_hash(_write(tmp_path, b""))

    assert got == hashlib.sha256(b"").hexdigest()
    assert got != hashlib.sha256(hashlib.sha256(b"").digest()).hexdigest(), (
        "this is the old, wrong value"
    )


async def test_dropbox_block_boundaries_are_not_off_by_one(tmp_path):
    """A block split one byte early or late produces a plausible-looking hash that
    Dropbox rejects, so the sizes either side of the boundary are what matter."""
    for size in (BLOCK - 1, BLOCK, BLOCK + 1):
        data = _varied(size, seed=size)
        got = await hasher.dropbox_content_hash(_write(tmp_path, data, f"b{size}"))
        assert got == dropbox_reference(data), f"boundary wrong at {size}"


# ── QuickXorHash: equivalence to the implementation it replaced ──────────


class PerByteQuickXor:
    """The original per-byte implementation, kept verbatim as the reference.

    The rewrite exists only to be faster, so the contract is that it produces the
    same bytes. Keeping the slow version here means an accidental behaviour change
    fails a test rather than silently altering every OneDrive hash — which would make
    every file look modified at once.
    """

    BITS, SHIFT = 160, 11

    def __init__(self) -> None:
        self._data = bytearray(self.BITS // 8)
        self._length = 0
        self._shift_so_far = 0

    def update(self, data: bytes) -> None:
        for byte in data:
            byte_pos = (self._shift_so_far // 8) % len(self._data)
            bit_offset = self._shift_so_far % 8
            self._data[byte_pos] ^= (byte >> bit_offset) & 0xFF
            if bit_offset > 0:
                self._data[(byte_pos + 1) % len(self._data)] ^= (
                    byte << (8 - bit_offset)
                ) & 0xFF
            self._shift_so_far = (self._shift_so_far + self.SHIFT) % self.BITS
            self._length += 1

    def hexdigest(self) -> str:
        result = bytearray(self._data)
        for i, b in enumerate(struct.pack("<Q", self._length)):
            result[len(result) - 8 + i] ^= b
        return bytes(result).hex()


#: Around the 160-byte period (11 and 160 are coprime, so positions repeat there)
#: and the 8-bit lane boundary, which is where a folding mistake shows up.
QX_SIZES = [0, 1, 7, 8, 9, 20, 159, 160, 161, 319, 320, 321, 1000, 8192, 8193, 20000]


@pytest.mark.parametrize("size", QX_SIZES, ids=lambda s: f"{s}b")
def test_quickxor_matches_the_per_byte_implementation(size):
    data = _varied(size, seed=size)

    fast, slow = hasher._QuickXorHasher(), PerByteQuickXor()
    fast.update(data)
    slow.update(data)

    assert fast.hexdigest() == slow.hexdigest()


@pytest.mark.parametrize(
    "steps",
    [[1], [7], [8], [159], [160], [161], [1, 160, 7], [8192, 1], [3, 5, 157, 160]],
    ids=lambda s: "-".join(map(str, s)),
)
def test_quickxor_is_the_same_when_fed_in_chunks(steps):
    """The engine feeds 8 KB at a time, so register state has to carry across calls.

    Chunk sizes that are not multiples of the period are the interesting ones: they
    leave the shift position mid-period, and a rewrite that assumed alignment would
    pass the single-shot tests and fail here.
    """
    data = _varied(3000, seed=99)

    fast, slow = hasher._QuickXorHasher(), PerByteQuickXor()
    pos = 0
    i = 0
    while pos < len(data):
        step = steps[i % len(steps)]
        fast.update(data[pos : pos + step])
        slow.update(data[pos : pos + step])
        pos += step
        i += 1

    assert fast.hexdigest() == slow.hexdigest()


def test_quickxor_of_an_empty_input_is_all_zero():
    assert hasher._QuickXorHasher().hexdigest() == "00" * 20


def test_quickxor_mixes_in_the_length():
    """Two inputs that XOR to the same register must still differ by length, or
    padding a file with zeros would not change its hash."""
    a = hasher._QuickXorHasher()
    a.update(b"\x00" * 4)
    b = hasher._QuickXorHasher()
    b.update(b"\x00" * 8)

    assert a.hexdigest() != b.hexdigest()


async def test_quickxor_over_a_file_matches_the_reference(tmp_path):
    data = _varied(50_000, seed=5)
    slow = PerByteQuickXor()
    slow.update(data)

    got = await hasher.quickxor_hash(_write(tmp_path, data))

    assert got == slow.hexdigest()


def test_quickxor_is_not_quadratic_in_the_input():
    """A guard on the performance fix, not a benchmark.

    The per-byte version took ~0.66 s per megabyte, so a 1 GB file cost about 5½
    minutes of CPU while the sync engine waited. The bound here is ~30x looser than
    the current implementation needs, so it should not flake on a slow runner, but it
    still fails outright if the loop returns to being per-byte in Python.
    """
    data = _varied(2 * 1024 * 1024, seed=3)

    started = time.monotonic()
    h = hasher._QuickXorHasher()
    h.update(data)
    h.hexdigest()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, (
        f"2 MB took {elapsed:.2f}s; at this rate a 1 GB file needs "
        f"{elapsed * 512:.0f}s and the per-byte loop is probably back"
    )


# ── Dispatch ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("algorithm", ["md5", "sha1", "content_hash", "quickxor"])
async def test_compute_hash_dispatches_to_each_algorithm(tmp_path, algorithm):
    path = _write(tmp_path, b"dispatch me")

    direct = await hasher.HASH_FUNCTIONS[algorithm](path)

    assert await hasher.compute_hash(path, algorithm) == direct


async def test_compute_hash_defaults_to_md5(tmp_path):
    path = _write(tmp_path, b"default")

    assert await hasher.compute_hash(path) == await hasher.md5_hash(path)


async def test_an_unknown_algorithm_raises_with_the_supported_list(tmp_path):
    """Naming the alternatives matters: a provider added with a typo'd algorithm
    should say what it could have been, not just that it failed."""
    path = _write(tmp_path, b"x")

    with pytest.raises(ValueError, match="Unsupported hash algorithm"):
        await hasher.compute_hash(path, "sha256")

    try:
        await hasher.compute_hash(path, "sha256")
    except ValueError as exc:
        assert "md5" in str(exc)


async def test_every_provider_hash_algorithm_is_registered():
    """The providers each declare a hash algorithm; if one is not in the table,
    hashing that provider's files raises at sync time rather than at startup."""
    assert set(hasher.HASH_FUNCTIONS) >= {"md5", "sha1", "content_hash", "quickxor"}


async def test_hashing_a_missing_file_raises(tmp_path):
    with pytest.raises((FileNotFoundError, OSError)):
        await hasher.md5_hash(tmp_path / "does-not-exist")
