"""Tests for local directory scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdrive_sync.local.scanner import (
    DEFAULT_IGNORE_PATTERNS,
    LocalFileInfo,
    _is_ignored,
    scan_directory,
)


class TestIsIgnored:
    def test_git_directory(self):
        assert _is_ignored(".git", DEFAULT_IGNORE_PATTERNS) is True

    def test_nested_git(self):
        assert _is_ignored(".git/objects/abc", DEFAULT_IGNORE_PATTERNS) is True

    def test_pycache(self):
        assert _is_ignored("__pycache__", DEFAULT_IGNORE_PATTERNS) is True

    def test_pyc_file(self):
        assert _is_ignored("module.pyc", DEFAULT_IGNORE_PATTERNS) is True

    def test_nested_pyc(self):
        assert _is_ignored("src/__pycache__/module.pyc", DEFAULT_IGNORE_PATTERNS) is True

    def test_ds_store(self):
        assert _is_ignored(".DS_Store", DEFAULT_IGNORE_PATTERNS) is True

    def test_thumbs_db(self):
        assert _is_ignored("Thumbs.db", DEFAULT_IGNORE_PATTERNS) is True

    def test_gdrive_sync_marker(self):
        assert _is_ignored(".gdrive-sync-state", DEFAULT_IGNORE_PATTERNS) is True

    def test_normal_file_not_ignored(self):
        assert _is_ignored("document.txt", DEFAULT_IGNORE_PATTERNS) is False

    def test_normal_nested_file_not_ignored(self):
        assert _is_ignored("src/main.py", DEFAULT_IGNORE_PATTERNS) is False

    def test_custom_patterns(self):
        patterns = ["*.log", "temp_*"]
        assert _is_ignored("debug.log", patterns) is True
        assert _is_ignored("temp_data.csv", patterns) is True
        assert _is_ignored("notes.txt", patterns) is False

    def test_empty_patterns_ignores_nothing(self):
        assert _is_ignored("anything.txt", []) is False


@pytest.mark.asyncio
async def test_scan_empty_directory(tmp_path: Path):
    result = await scan_directory(tmp_path)
    assert result == {}


@pytest.mark.asyncio
async def test_scan_single_file(tmp_path: Path):
    (tmp_path / "hello.txt").write_text("hello")
    result = await scan_directory(tmp_path)
    assert "hello.txt" in result
    info = result["hello.txt"]
    assert isinstance(info, LocalFileInfo)
    assert info.size == 5
    assert info.md5 is not None
    assert info.mtime > 0


@pytest.mark.asyncio
async def test_scan_nested_files(tmp_path: Path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    (tmp_path / "root.txt").write_text("root")
    (sub / "nested.txt").write_text("nested")

    result = await scan_directory(tmp_path)
    assert "root.txt" in result
    assert "subdir/nested.txt" in result


@pytest.mark.asyncio
async def test_scan_ignores_git(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main")
    (tmp_path / "real_file.txt").write_text("data")

    result = await scan_directory(tmp_path)
    assert "real_file.txt" in result
    assert ".git/HEAD" not in result


@pytest.mark.asyncio
async def test_scan_ignores_pycache(tmp_path: Path):
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "module.cpython-312.pyc").write_bytes(b"\x00")
    (tmp_path / "module.py").write_text("pass")

    result = await scan_directory(tmp_path)
    assert "module.py" in result
    paths = list(result.keys())
    assert not any("__pycache__" in p for p in paths)


@pytest.mark.asyncio
async def test_scan_custom_ignore_patterns(tmp_path: Path):
    (tmp_path / "keep.txt").write_text("keep")
    (tmp_path / "ignore.log").write_text("ignore")

    result = await scan_directory(tmp_path, ignore_patterns=["*.log"])
    assert "keep.txt" in result
    assert "ignore.log" not in result


@pytest.mark.asyncio
async def test_scan_nonexistent_directory(tmp_path: Path):
    result = await scan_directory(tmp_path / "nonexistent")
    assert result == {}


@pytest.mark.asyncio
async def test_scan_skips_directories(tmp_path: Path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "file.txt").write_text("data")

    result = await scan_directory(tmp_path)
    # Only files should be in results, not directories
    assert all(not (tmp_path / p).is_dir() for p in result)


@pytest.mark.asyncio
async def test_scan_md5_correctness(tmp_path: Path):
    import hashlib

    content = b"test content for hashing"
    (tmp_path / "hash_test.txt").write_bytes(content)

    result = await scan_directory(tmp_path)
    expected_md5 = hashlib.md5(content).hexdigest()
    assert result["hash_test.txt"].md5 == expected_md5
