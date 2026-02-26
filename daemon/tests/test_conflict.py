"""Tests for conflict detection and resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdrive_sync.db.models import FileState, SyncEntry
from gdrive_sync.sync.conflict import (
    detect_conflict,
    resolve_keep_both,
    resolve_newest_wins,
)
from gdrive_sync.sync.planner import ActionType


class TestDetectConflict:
    def test_no_stored_entry_both_exist_different(self):
        assert detect_conflict("aaa", "bbb", None) is True

    def test_no_stored_entry_both_exist_same(self):
        assert detect_conflict("aaa", "aaa", None) is False

    def test_no_stored_entry_one_missing(self):
        assert detect_conflict("aaa", None, None) is False
        assert detect_conflict(None, "bbb", None) is False

    def test_only_local_changed(self):
        stored = SyncEntry(
            path="f.txt", pair_id="p0", state=FileState.SYNCED,
            local_md5="base", remote_md5="base",
        )
        assert detect_conflict("changed", "base", stored) is False

    def test_only_remote_changed(self):
        stored = SyncEntry(
            path="f.txt", pair_id="p0", state=FileState.SYNCED,
            local_md5="base", remote_md5="base",
        )
        assert detect_conflict("base", "changed", stored) is False

    def test_both_changed(self):
        stored = SyncEntry(
            path="f.txt", pair_id="p0", state=FileState.SYNCED,
            local_md5="base", remote_md5="base",
        )
        assert detect_conflict("local_new", "remote_new", stored) is True

    def test_both_changed_to_same_value(self):
        stored = SyncEntry(
            path="f.txt", pair_id="p0", state=FileState.SYNCED,
            local_md5="base", remote_md5="base",
        )
        # Both changed but to the same value — still counts as conflict
        # because both sides diverged from base
        assert detect_conflict("same_new", "same_new", stored) is True


class TestResolveKeepBoth:
    def test_creates_conflict_copy(self, tmp_path: Path):
        original = tmp_path / "document.txt"
        original.write_text("original content")

        conflict_path = resolve_keep_both(original)

        assert conflict_path.exists()
        assert conflict_path.parent == tmp_path
        assert "conflict" in conflict_path.name
        assert conflict_path.suffix == ".txt"
        assert conflict_path.read_text() == "original content"
        # Original still exists
        assert original.exists()

    def test_conflict_copy_preserves_extension(self, tmp_path: Path):
        original = tmp_path / "photo.png"
        original.write_bytes(b"image data")

        conflict_path = resolve_keep_both(original)
        assert conflict_path.suffix == ".png"


class TestResolveNewestWins:
    def test_local_newer(self):
        result = resolve_newest_wins(200.0, 100.0)
        assert result == ActionType.UPLOAD

    def test_remote_newer(self):
        result = resolve_newest_wins(100.0, 200.0)
        assert result == ActionType.DOWNLOAD

    def test_same_time_prefers_local(self):
        result = resolve_newest_wins(100.0, 100.0)
        assert result == ActionType.UPLOAD
