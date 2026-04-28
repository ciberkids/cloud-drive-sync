"""Regression test for issue #35: DELETE_REMOTE with no remote_id logs warning and cleans DB.

After a broken migration _conflict_* files were left in the DB with remote_id=None.
Previously the executor raised ValueError for each one, flooding the error log with 3,090
errors. Now it silently cleans the stale entry and moves on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import FileState, SyncEntry
from cloud_drive_sync.drive.mock_client import MockDriveClient, MockFileOperations
from cloud_drive_sync.sync.executor import SyncExecutor
from cloud_drive_sync.sync.planner import ActionType, SyncAction


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_no_id.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def executor(tmp_path, db):
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    client = MockDriveClient(remote)
    ops = MockFileOperations(client)
    return SyncExecutor(ops, db, local, "pair_0", remote_folder_id="root",
                        max_concurrent=1, drive_client=client)


@pytest.mark.asyncio
async def test_delete_remote_no_remote_id_cleans_db(executor, db):
    """DELETE_REMOTE with remote_id=None cleans the DB entry instead of raising."""
    stale_entry = SyncEntry(
        path="_conflict_20240101_report.docx",
        pair_id="pair_0",
        remote_id=None,
        local_md5="abc",
        remote_md5="abc",
        state=FileState.SYNCED,
        local_mtime=0,
        last_synced=0,
    )
    await db.upsert_sync_entry(stale_entry)

    action = SyncAction(
        action=ActionType.DELETE_REMOTE,
        path="_conflict_20240101_report.docx",
        stored_entry=stale_entry,
        reason="local deletion",
    )

    failed = await executor.execute_all([action])
    assert failed == [], f"Expected no failures, got: {failed}"

    # DB entry must be cleaned up
    entry = await db.get_sync_entry("_conflict_20240101_report.docx", "pair_0")
    assert entry is None, "Stale DB entry should have been deleted"


@pytest.mark.asyncio
async def test_delete_remote_no_stored_entry_is_noop(executor, db):
    """DELETE_REMOTE with no stored_entry at all does nothing and doesn't raise."""
    action = SyncAction(
        action=ActionType.DELETE_REMOTE,
        path="ghost_file.txt",
        stored_entry=None,
        reason="local deletion",
    )

    failed = await executor.execute_all([action])
    assert failed == []
