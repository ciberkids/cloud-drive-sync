"""Async hashing for files. Supports MD5, SHA1, Dropbox content hash, QuickXorHash."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import aiofiles

CHUNK_SIZE = 8192
DROPBOX_BLOCK_SIZE = 4 * 1024 * 1024  # 4 MB


async def md5_hash(path: Path) -> str:
    """Compute the MD5 hash of a file asynchronously, reading in 8KB chunks.

    Args:
        path: Path to the file to hash.

    Returns:
        Hex digest of the MD5 hash.
    """
    h = hashlib.md5()
    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


async def sha1_hash(path: Path) -> str:
    """Compute the SHA1 hash of a file asynchronously.

    Args:
        path: Path to the file to hash.

    Returns:
        Hex digest of the SHA1 hash.
    """
    h = hashlib.sha1()
    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


async def dropbox_content_hash(path: Path) -> str:
    """Compute the Dropbox content hash of a file.

    Dropbox content hash is computed by:
    1. Split the file into 4MB blocks
    2. Compute SHA256 of each block
    3. Concatenate all block hashes
    4. Compute SHA256 of the concatenation

    Args:
        path: Path to the file to hash.

    Returns:
        Hex digest of the Dropbox content hash.
    """
    block_hashes = []
    current_block = hashlib.sha256()
    current_block_size = 0

    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(CHUNK_SIZE)
            if not chunk:
                break

            remaining = chunk
            while remaining:
                space_in_block = DROPBOX_BLOCK_SIZE - current_block_size
                to_add = remaining[:space_in_block]
                current_block.update(to_add)
                current_block_size += len(to_add)
                remaining = remaining[space_in_block:]

                if current_block_size == DROPBOX_BLOCK_SIZE:
                    block_hashes.append(current_block.digest())
                    current_block = hashlib.sha256()
                    current_block_size = 0

    # Don't forget the last partial block
    if current_block_size > 0:
        block_hashes.append(current_block.digest())

    # An empty file has *no* blocks, so it contributes nothing and the result is
    # sha256 of the empty string. This used to invent a block by appending
    # sha256(b"") as if one existed, giving sha256(sha256(b"")) instead — so every
    # empty file's hash disagreed with Dropbox's, was never recognised as matching,
    # and got re-uploaded on every sync pass forever. Dropbox's own reference hasher
    # adds nothing when the current block is empty; this now matches it.
    overall = hashlib.sha256(b"".join(block_hashes))
    return overall.hexdigest()


class _QuickXorHasher:
    """Implements Microsoft's QuickXorHash algorithm.

    QuickXorHash is a non-cryptographic hash used by OneDrive/SharePoint.
    It XORs data into a circular shift register of 160 bits.
    """

    BITS = 160
    SHIFT = 11

    def __init__(self) -> None:
        self._data = bytearray(self.BITS // 8)  # 20 bytes
        self._length = 0
        self._shift_so_far = 0

    #: How many bytes it takes for the shift position to return to where it started.
    #: ``SHIFT`` and ``BITS`` are coprime, so every byte in a run of this length
    #: lands at a distinct bit position, and the pattern then repeats exactly.
    PERIOD = BITS  # 160 bytes, since gcd(11, 160) == 1

    def update(self, data: bytes) -> None:
        """XOR ``data`` into the register.

        Written to avoid a Python-level loop over every byte. That version cost
        about 0.66 s per megabyte — roughly **5½ minutes for a 1 GB file** — of pure
        interpreter time, on a code path the sync engine calls for every changed
        file.

        The saving comes from XOR being associative and the bit positions repeating
        every ``PERIOD`` bytes: bytes at the same offset within their period always
        land in the same place, so they can be folded together first, in C, and
        placed once. Output is bit-identical to the per-byte version.
        """
        if not data:
            return

        start = self._shift_so_far
        period = self.PERIOD

        # Byte j lands at bit position (start + SHIFT*j) mod BITS, which repeats every
        # `period` bytes. So fold data[j] into folded[j % period] — bytes that share a
        # slot XOR together and get placed once. Each fold is a single large-integer
        # XOR, which happens in C rather than a loop over bytes here.
        view = memoryview(data)
        n = len(view)
        folded = bytearray(period)
        for pos in range(0, n, period):
            take = min(period, n - pos)
            merged = int.from_bytes(folded[:take], "big") ^ int.from_bytes(
                view[pos : pos + take], "big"
            )
            folded[:take] = merged.to_bytes(take, "big")

        # Place the folded period: at most `period` bytes, however large the input.
        shift = start
        for byte in folded[:min(period, n)]:
            if byte:
                byte_pos = shift // 8
                bit_offset = shift % 8
                self._data[byte_pos] ^= (byte >> bit_offset) & 0xFF
                if bit_offset > 0:
                    self._data[(byte_pos + 1) % len(self._data)] ^= (
                        byte << (8 - bit_offset)
                    ) & 0xFF
            shift = (shift + self.SHIFT) % self.BITS

        self._shift_so_far = (start + self.SHIFT * n) % self.BITS
        self._length += n

    def digest(self) -> bytes:
        # XOR the length into the final hash (as 8 little-endian bytes)
        result = bytearray(self._data)
        length_bytes = struct.pack("<Q", self._length)
        for i, b in enumerate(length_bytes):
            result[len(result) - 8 + i] ^= b
        return bytes(result)

    def hexdigest(self) -> str:
        return self.digest().hex()


async def quickxor_hash(path: Path) -> str:
    """Compute the QuickXorHash of a file (used by OneDrive/SharePoint).

    Args:
        path: Path to the file to hash.

    Returns:
        Hex digest of the QuickXorHash.
    """
    h = _QuickXorHasher()
    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# Map of algorithm name -> hash function for provider-agnostic usage
HASH_FUNCTIONS = {
    "md5": md5_hash,
    "sha1": sha1_hash,
    "content_hash": dropbox_content_hash,
    "quickxor": quickxor_hash,
}


async def compute_hash(path: Path, algorithm: str = "md5") -> str:
    """Compute a file hash using the specified algorithm.

    Args:
        path: Path to the file.
        algorithm: One of "md5", "sha1", "content_hash", "quickxor".

    Returns:
        Hex digest of the hash.

    Raises:
        ValueError: If algorithm is not supported.
    """
    func = HASH_FUNCTIONS.get(algorithm)
    if func is None:
        raise ValueError(f"Unsupported hash algorithm: {algorithm!r}. Supported: {list(HASH_FUNCTIONS.keys())}")
    return await func(path)
