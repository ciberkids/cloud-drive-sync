"""Tests for data model serialization and deserialization."""

from __future__ import annotations

from datetime import datetime, timezone

from gdrive_sync.db.models import (
    ChangeToken,
    ConflictRecord,
    FileState,
    SyncEntry,
    SyncLogEntry,
)


class TestSyncEntry:
    def test_to_row_full(self):
        dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        entry = SyncEntry(
            path="docs/readme.md",
            local_md5="abc",
            remote_md5="def",
            remote_id="rid1",
            state=FileState.SYNCED,
            local_mtime=1000.0,
            remote_mtime=2000.0,
            last_synced=dt,
            pair_id="p0",
        )
        row = entry.to_row()
        assert row == (
            "docs/readme.md", "abc", "def", "rid1", "synced",
            1000.0, 2000.0, dt.isoformat(), "p0",
        )

    def test_to_row_none_fields(self):
        entry = SyncEntry(path="f.txt", pair_id="p0")
        row = entry.to_row()
        assert row[1] is None  # local_md5
        assert row[2] is None  # remote_md5
        assert row[3] is None  # remote_id
        assert row[4] == "unknown"  # state
        assert row[7] is None  # last_synced

    def test_from_row_roundtrip(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        original = SyncEntry(
            path="test.txt",
            local_md5="aaa",
            remote_md5="bbb",
            remote_id="rid",
            state=FileState.PENDING_UPLOAD,
            local_mtime=100.0,
            remote_mtime=200.0,
            last_synced=dt,
            pair_id="p1",
        )
        row = original.to_row()
        restored = SyncEntry.from_row(row)
        assert restored.path == original.path
        assert restored.local_md5 == original.local_md5
        assert restored.remote_md5 == original.remote_md5
        assert restored.remote_id == original.remote_id
        assert restored.state == original.state
        assert restored.local_mtime == original.local_mtime
        assert restored.remote_mtime == original.remote_mtime
        assert restored.last_synced == original.last_synced
        assert restored.pair_id == original.pair_id

    def test_from_row_with_none_last_synced(self):
        row = ("f.txt", "aaa", "bbb", "rid", "synced", 1.0, 2.0, None, "p0")
        entry = SyncEntry.from_row(row)
        assert entry.last_synced is None

    def test_default_state_is_unknown(self):
        entry = SyncEntry(path="x.txt")
        assert entry.state == FileState.UNKNOWN

    def test_all_file_states_roundtrip(self):
        for state in FileState:
            entry = SyncEntry(path="x.txt", state=state, pair_id="p0")
            row = entry.to_row()
            restored = SyncEntry.from_row(row)
            assert restored.state == state


class TestConflictRecord:
    def test_to_row(self):
        dt = datetime(2025, 3, 1, tzinfo=timezone.utc)
        record = ConflictRecord(
            id=5,
            path="conflict.txt",
            pair_id="p0",
            local_md5="aaa",
            remote_md5="bbb",
            local_mtime=100.0,
            remote_mtime=200.0,
            detected_at=dt,
            resolved=False,
            resolution=None,
        )
        row = record.to_row()
        # to_row doesn't include id
        assert row == (
            "conflict.txt", "p0", "aaa", "bbb",
            100.0, 200.0, dt.isoformat(), False, None,
        )

    def test_from_row_roundtrip(self):
        dt = datetime(2025, 3, 1, tzinfo=timezone.utc)
        row = (
            10, "conflict.txt", "p0", "aaa", "bbb",
            100.0, 200.0, dt.isoformat(), 1, "keep_both",
        )
        record = ConflictRecord.from_row(row)
        assert record.id == 10
        assert record.path == "conflict.txt"
        assert record.pair_id == "p0"
        assert record.local_md5 == "aaa"
        assert record.remote_md5 == "bbb"
        assert record.local_mtime == 100.0
        assert record.remote_mtime == 200.0
        assert record.detected_at == dt
        assert record.resolved is True
        assert record.resolution == "keep_both"

    def test_default_values(self):
        record = ConflictRecord()
        assert record.id is None
        assert record.path == ""
        assert record.resolved is False
        assert record.resolution is None


class TestSyncLogEntry:
    def test_to_row(self):
        dt = datetime(2025, 6, 1, 10, 30, 0, tzinfo=timezone.utc)
        entry = SyncLogEntry(
            id=1,
            timestamp=dt,
            action="upload",
            path="file.txt",
            pair_id="p0",
            status="ok",
            detail="uploaded successfully",
        )
        row = entry.to_row()
        # to_row doesn't include id
        assert row == (
            dt.isoformat(), "upload", "file.txt", "p0", "ok", "uploaded successfully",
        )

    def test_from_row_roundtrip(self):
        dt = datetime(2025, 6, 1, 10, 30, 0, tzinfo=timezone.utc)
        row = (42, dt.isoformat(), "download", "doc.pdf", "p1", "error", "network timeout")
        entry = SyncLogEntry.from_row(row)
        assert entry.id == 42
        assert entry.timestamp == dt
        assert entry.action == "download"
        assert entry.path == "doc.pdf"
        assert entry.pair_id == "p1"
        assert entry.status == "error"
        assert entry.detail == "network timeout"

    def test_default_timestamp_is_utc(self):
        entry = SyncLogEntry()
        assert entry.timestamp.tzinfo == timezone.utc


class TestChangeToken:
    def test_default_updated_at(self):
        ct = ChangeToken(pair_id="p0", token="tok")
        assert ct.updated_at.tzinfo == timezone.utc

    def test_fields(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        ct = ChangeToken(pair_id="p1", token="abc", updated_at=dt)
        assert ct.pair_id == "p1"
        assert ct.token == "abc"
        assert ct.updated_at == dt
