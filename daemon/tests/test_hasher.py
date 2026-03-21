"""Tests for async MD5 hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cloud_drive_sync.local.hasher import md5_hash


@pytest.mark.asyncio
async def test_md5_hash_simple_file(tmp_path: Path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world")
    expected = hashlib.md5(b"hello world").hexdigest()
    result = await md5_hash(f)
    assert result == expected


@pytest.mark.asyncio
async def test_md5_hash_empty_file(tmp_path: Path):
    f = tmp_path / "empty.txt"
    f.write_bytes(b"")
    expected = hashlib.md5(b"").hexdigest()
    result = await md5_hash(f)
    assert result == expected


@pytest.mark.asyncio
async def test_md5_hash_binary_content(tmp_path: Path):
    data = bytes(range(256)) * 100
    f = tmp_path / "binary.bin"
    f.write_bytes(data)
    expected = hashlib.md5(data).hexdigest()
    result = await md5_hash(f)
    assert result == expected


@pytest.mark.asyncio
async def test_md5_hash_large_file(tmp_path: Path):
    """Test with file larger than CHUNK_SIZE (8192 bytes)."""
    data = b"x" * 50000
    f = tmp_path / "large.bin"
    f.write_bytes(data)
    expected = hashlib.md5(data).hexdigest()
    result = await md5_hash(f)
    assert result == expected


@pytest.mark.asyncio
async def test_md5_hash_deterministic(tmp_path: Path):
    f = tmp_path / "det.txt"
    f.write_text("deterministic")
    h1 = await md5_hash(f)
    h2 = await md5_hash(f)
    assert h1 == h2


@pytest.mark.asyncio
async def test_md5_hash_different_content_different_hash(tmp_path: Path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("content a")
    f2.write_text("content b")
    h1 = await md5_hash(f1)
    h2 = await md5_hash(f2)
    assert h1 != h2
