"""Async MD5 hashing for files."""

from __future__ import annotations

import hashlib
from pathlib import Path

import aiofiles

CHUNK_SIZE = 8192


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
