"""Tests for filesystem watcher with debounced event queueing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gdrive_sync.local.watcher import ChangeType, DirectoryWatcher, LocalChange


@pytest.mark.asyncio
async def test_watcher_detects_file_creation(tmp_path: Path):
    watcher = DirectoryWatcher(tmp_path, debounce_delay=0.2)
    await watcher.start()

    try:
        await asyncio.sleep(0.3)
        (tmp_path / "new_file.txt").write_text("hello")
        await asyncio.sleep(1.0)

        changes = []
        while not watcher.changes.empty():
            changes.append(watcher.changes.get_nowait())

        # Look for any event related to the file (CREATED or MODIFIED)
        related = [c for c in changes if "new_file" in c.path]
        assert len(related) >= 1
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_detects_file_modification(tmp_path: Path):
    f = tmp_path / "existing.txt"
    f.write_text("original")

    watcher = DirectoryWatcher(tmp_path, debounce_delay=0.2)
    await watcher.start()

    try:
        await asyncio.sleep(0.3)
        f.write_text("modified")
        await asyncio.sleep(1.0)

        changes = []
        while not watcher.changes.empty():
            changes.append(watcher.changes.get_nowait())

        related = [c for c in changes if "existing" in c.path]
        assert len(related) >= 1
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_detects_file_deletion(tmp_path: Path):
    f = tmp_path / "delete_me.txt"
    f.write_text("going away")

    watcher = DirectoryWatcher(tmp_path, debounce_delay=0.2)
    await watcher.start()

    try:
        await asyncio.sleep(0.3)
        f.unlink()
        await asyncio.sleep(1.0)

        changes = []
        while not watcher.changes.empty():
            changes.append(watcher.changes.get_nowait())

        deleted = [c for c in changes if c.change_type == ChangeType.DELETED and "delete_me" in c.path]
        assert len(deleted) >= 1
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_debounce_coalesces_rapid_changes(tmp_path: Path):
    watcher = DirectoryWatcher(tmp_path, debounce_delay=0.5)
    await watcher.start()

    try:
        await asyncio.sleep(0.3)
        f = tmp_path / "rapid.txt"
        # Write multiple times rapidly
        for i in range(5):
            f.write_text(f"version {i}")
            await asyncio.sleep(0.02)

        # Wait for debounce to flush
        await asyncio.sleep(1.5)

        changes = []
        while not watcher.changes.empty():
            changes.append(watcher.changes.get_nowait())

        # Debouncing should coalesce rapid changes to the same path
        rapid_changes = [c for c in changes if "rapid" in c.path]
        # Should have fewer events than the 5 writes due to debouncing
        assert len(rapid_changes) < 5
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_changes_queue_property(tmp_path: Path):
    watcher = DirectoryWatcher(tmp_path)
    assert watcher.changes is not None
    assert isinstance(watcher.changes, asyncio.Queue)


@pytest.mark.asyncio
async def test_watcher_stop_is_idempotent(tmp_path: Path):
    watcher = DirectoryWatcher(tmp_path, debounce_delay=0.1)
    await watcher.start()
    await watcher.stop()
    # Stopping again should not raise
    await watcher.stop()


class TestLocalChange:
    def test_created_change(self):
        change = LocalChange(ChangeType.CREATED, "file.txt")
        assert change.change_type == ChangeType.CREATED
        assert change.path == "file.txt"
        assert change.is_directory is False
        assert change.dest_path is None

    def test_moved_change(self):
        change = LocalChange(ChangeType.MOVED, "old.txt", dest_path="new.txt")
        assert change.change_type == ChangeType.MOVED
        assert change.path == "old.txt"
        assert change.dest_path == "new.txt"

    def test_directory_change(self):
        change = LocalChange(ChangeType.CREATED, "new_dir", is_directory=True)
        assert change.is_directory is True
