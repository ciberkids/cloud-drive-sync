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

    # Handle empty file
    if not block_hashes:
        block_hashes.append(hashlib.sha256(b"").digest())

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

    def update(self, data: bytes) -> None:
        for byte in data:
            # XOR byte into the shift register at the current position
            byte_pos = (self._shift_so_far // 8) % len(self._data)
            bit_offset = self._shift_so_far % 8

            self._data[byte_pos] ^= (byte >> bit_offset) & 0xFF
            # Handle overflow into next byte
            if bit_offset > 0:
                next_pos = (byte_pos + 1) % len(self._data)
                self._data[next_pos] ^= (byte << (8 - bit_offset)) & 0xFF

            self._shift_so_far = (self._shift_so_far + self.SHIFT) % self.BITS
            self._length += 1

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
