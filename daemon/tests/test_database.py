"""Tests for async database operations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import (
    ChangeToken,
    ConflictRecord,
    FileState,
    SyncEntry,
    SyncLogEntry,
)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    await database.open()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_upsert_and_get_sync_entry(db: Database):
    entry = SyncEntry(
        path="docs/readme.md",
        pair_id="pair_0",
        local_md5="abc123",
        remote_md5="abc123",
        remote_id="drive_id_1",
        state=FileState.SYNCED,
        local_mtime=1000.0,
        remote_mtime=1000.0,
        last_synced=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    await db.upsert_sync_entry(entry)

    got = await db.get_sync_entry("docs/readme.md", "pair_0")
    assert got is not None
    assert got.path == "docs/readme.md"
    assert got.local_md5 == "abc123"
    assert got.state == FileState.SYNCED
    assert got.remote_id == "drive_id_1"


@pytest.mark.asyncio
async def test_upsert_updates_existing(db: Database):
    entry = SyncEntry(path="file.txt", pair_id="p0", state=FileState.SYNCED, local_md5="aaa")
    await db.upsert_sync_entry(entry)

    entry.local_md5 = "bbb"
    entry.state = FileState.PENDING_UPLOAD
    await db.upsert_sync_entry(entry)

    got = await db.get_sync_entry("file.txt", "p0")
    assert got is not None
    assert got.local_md5 == "bbb"
    assert got.state == FileState.PENDING_UPLOAD


@pytest.mark.asyncio
async def test_delete_sync_entry(db: Database):
    entry = SyncEntry(path="del.txt", pair_id="p0", state=FileState.SYNCED)
    await db.upsert_sync_entry(entry)
    await db.delete_sync_entry("del.txt", "p0")
    assert await db.get_sync_entry("del.txt", "p0") is None


@pytest.mark.asyncio
async def test_get_all_entries(db: Database):
    for i in range(5):
        await db.upsert_sync_entry(
            SyncEntry(path=f"file_{i}.txt", pair_id="p0", state=FileState.SYNCED)
        )
    entries = await db.get_all_entries("p0")
    assert len(entries) == 5


@pytest.mark.asyncio
async def test_get_entries_by_state(db: Database):
    await db.upsert_sync_entry(SyncEntry(path="a.txt", pair_id="p0", state=FileState.SYNCED))
    await db.upsert_sync_entry(SyncEntry(path="b.txt", pair_id="p0", state=FileState.CONFLICT))
    await db.upsert_sync_entry(SyncEntry(path="c.txt", pair_id="p0", state=FileState.SYNCED))

    synced = await db.get_entries_by_state(FileState.SYNCED, "p0")
    assert len(synced) == 2
    conflicts = await db.get_entries_by_state(FileState.CONFLICT, "p0")
    assert len(conflicts) == 1


@pytest.mark.asyncio
async def test_change_token_crud(db: Database):
    ct = ChangeToken(pair_id="p0", token="token_abc")
    await db.upsert_change_token(ct)

    got = await db.get_change_token("p0")
    assert got is not None
    assert got.token == "token_abc"

    ct.token = "token_xyz"
    await db.upsert_change_token(ct)
    got = await db.get_change_token("p0")
    assert got is not None
    assert got.token == "token_xyz"


@pytest.mark.asyncio
async def test_conflict_crud(db: Database):
    conflict = ConflictRecord(
        path="conflict.txt",
        pair_id="p0",
        local_md5="aaa",
        remote_md5="bbb",
        local_mtime=100.0,
        remote_mtime=200.0,
    )
    cid = await db.add_conflict(conflict)
    assert cid is not None

    unresolved = await db.get_unresolved_conflicts("p0")
    assert len(unresolved) == 1
    assert unresolved[0].path == "conflict.txt"

    await db.resolve_conflict(cid, "keep_both")
    unresolved = await db.get_unresolved_conflicts("p0")
    assert len(unresolved) == 0


@pytest.mark.asyncio
async def test_sync_log(db: Database):
    entry = SyncLogEntry(
        action="upload",
        path="test.txt",
        pair_id="p0",
        status="ok",
        detail="uploaded successfully",
    )
    await db.add_log_entry(entry)

    logs = await db.get_recent_log(limit=10, pair_id="p0")
    assert len(logs) == 1
    assert logs[0].action == "upload"
    assert logs[0].status == "ok"


@pytest.mark.asyncio
async def test_count_by_state(db: Database):
    await db.upsert_sync_entry(SyncEntry(path="a.txt", pair_id="p0", state=FileState.SYNCED))
    await db.upsert_sync_entry(SyncEntry(path="b.txt", pair_id="p0", state=FileState.SYNCED))
    await db.upsert_sync_entry(SyncEntry(path="c.txt", pair_id="p0", state=FileState.ERROR))

    counts = await db.count_by_state("p0")
    assert counts["synced"] == 2
    assert counts["error"] == 1


@pytest.mark.asyncio
async def test_clear_pair(db: Database):
    await db.upsert_sync_entry(SyncEntry(path="a.txt", pair_id="p0", state=FileState.SYNCED))
    await db.upsert_change_token(ChangeToken(pair_id="p0", token="t"))
    await db.add_log_entry(SyncLogEntry(action="x", path="a.txt", pair_id="p0", status="ok"))

    await db.clear_pair("p0")
    assert await db.get_all_entries("p0") == []
    assert await db.get_change_token("p0") is None
    assert await db.get_recent_log(pair_id="p0") == []
