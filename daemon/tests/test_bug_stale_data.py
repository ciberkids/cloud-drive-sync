"""Tests for Bug 1: Unwanted folder on restart due to stale DB entries.

Bug: When a sync pair is removed and the daemon restarts, stale entries from
     the removed pair remain in the database (sync_state, sync_log, change_tokens,
     conflicts tables). This causes phantom sync entries from previous sessions.

     The database should be cleaned of entries for pairs that no longer exist
     in the configuration when the daemon starts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gdrive_sync.config import Config, SyncConfig, SyncPair
from gdrive_sync.db.database import Database
from gdrive_sync.db.models import (
    ChangeToken,
    ConflictRecord,
    FileState,
    SyncEntry,
    SyncLogEntry,
)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_stale.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def config_with_one_pair(tmp_path: Path) -> Config:
    """Config with only pair_0 active (pair_1 was removed)."""
    cfg = Config()
    cfg.sync = SyncConfig(
        pairs=[
            SyncPair(local_path=str(tmp_path / "active"), remote_folder_id="root", enabled=True),
        ],
    )
    return cfg


async def _populate_stale_entries(db: Database):
    """Insert entries for two pairs: pair_0 (active) and pair_1 (removed)."""
    # Active pair entries
    await db.upsert_sync_entry(SyncEntry(
        path="active_file.txt", pair_id="pair_0",
        local_md5="abc", remote_md5="abc", remote_id="remote_0",
        state=FileState.SYNCED,
    ))
    await db.add_log_entry(SyncLogEntry(
        action="upload", path="active_file.txt", pair_id="pair_0",
        status="ok", detail="",
    ))

    # STALE entries from removed pair
    await db.upsert_sync_entry(SyncEntry(
        path="old_file.txt", pair_id="pair_1",
        local_md5="def", remote_md5="def", remote_id="remote_1",
        state=FileState.SYNCED,
    ))
    await db.upsert_sync_entry(SyncEntry(
        path="another_old.txt", pair_id="pair_1",
        local_md5="ghi", remote_md5="ghi", remote_id="remote_2",
        state=FileState.SYNCED,
    ))
    await db.add_log_entry(SyncLogEntry(
        action="download", path="old_file.txt", pair_id="pair_1",
        status="ok", detail="",
    ))
    await db.upsert_change_token(ChangeToken(pair_id="pair_1", token="token_old"))
    await db.add_conflict(ConflictRecord(
        path="conflict_old.txt", pair_id="pair_1",
        local_md5="xxx", remote_md5="yyy",
        local_mtime=100.0, remote_mtime=200.0,
    ))


# ── Bug 1a: Stale sync_state entries persist after pair removal ────


@pytest.mark.asyncio
async def test_stale_sync_entries_persist_after_pair_removal(
    db: Database, config_with_one_pair: Config
):
    """BUG: After removing pair_1, its sync_state entries remain in the DB.

    This causes phantom files to appear associated with a pair that no longer
    exists in the configuration.
    """
    await _populate_stale_entries(db)

    # Verify stale entries exist
    stale_entries = await db.get_all_entries("pair_1")
    assert len(stale_entries) == 2, "Setup: stale entries should exist"

    # Simulate daemon startup with only pair_0 in config
    active_pair_ids = {
        f"pair_{i}" for i, p in enumerate(config_with_one_pair.sync.pairs)
    }

    # Clean up stale pairs (what engine.start() does)
    await db.cleanup_stale_pairs(active_pair_ids)

    # After fix: daemon should clear stale entries on startup
    stale_entries_after = await db.get_all_entries("pair_1")
    assert len(stale_entries_after) == 0, (
        f"Found {len(stale_entries_after)} stale sync entries for removed pair_1. "
        "Bug: database not cleaned on restart for removed pairs."
    )


@pytest.mark.asyncio
async def test_stale_change_tokens_persist(db: Database, config_with_one_pair: Config):
    """BUG: Change tokens for removed pairs persist in the database."""
    await _populate_stale_entries(db)

    # Verify stale token exists
    stale_token = await db.get_change_token("pair_1")
    assert stale_token is not None, "Setup: stale token should exist"

    # Clean up stale pairs (what engine.start() does)
    active_pair_ids = {f"pair_{i}" for i, p in enumerate(config_with_one_pair.sync.pairs)}
    await db.cleanup_stale_pairs(active_pair_ids)

    # After fix: should be cleaned up on daemon start
    stale_token = await db.get_change_token("pair_1")
    assert stale_token is None, (
        "Stale change token for pair_1 still exists. "
        "Bug: change tokens not cleaned for removed pairs."
    )


@pytest.mark.asyncio
async def test_stale_conflicts_persist(db: Database, config_with_one_pair: Config):
    """BUG: Conflicts for removed pairs persist in the database."""
    await _populate_stale_entries(db)

    stale_conflicts = await db.get_unresolved_conflicts("pair_1")
    assert len(stale_conflicts) == 1, "Setup: stale conflict should exist"

    # Clean up stale pairs (what engine.start() does)
    active_pair_ids = {f"pair_{i}" for i, p in enumerate(config_with_one_pair.sync.pairs)}
    await db.cleanup_stale_pairs(active_pair_ids)

    stale_conflicts = await db.get_unresolved_conflicts("pair_1")
    assert len(stale_conflicts) == 0, (
        f"Found {len(stale_conflicts)} stale conflicts for removed pair_1. "
        "Bug: conflicts not cleaned for removed pairs."
    )


@pytest.mark.asyncio
async def test_stale_log_entries_persist(db: Database, config_with_one_pair: Config):
    """BUG: Activity log entries for removed pairs persist in the database."""
    await _populate_stale_entries(db)

    all_logs = await db.get_recent_log(limit=100)
    stale_logs = [entry for entry in all_logs if entry.pair_id == "pair_1"]
    assert len(stale_logs) == 1, "Setup: stale log entry should exist"

    # Clean up stale pairs (what engine.start() does)
    active_pair_ids = {f"pair_{i}" for i, p in enumerate(config_with_one_pair.sync.pairs)}
    await db.cleanup_stale_pairs(active_pair_ids)

    all_logs = await db.get_recent_log(limit=100)
    stale_logs = [entry for entry in all_logs if entry.pair_id == "pair_1"]
    assert len(stale_logs) == 0, (
        f"Found {len(stale_logs)} stale log entries for removed pair_1. "
        "Bug: sync_log not cleaned for removed pairs."
    )


# ── Bug 1b: Database.clear_pair should be called on cleanup ───────


@pytest.mark.asyncio
async def test_clear_pair_removes_all_pair_data(db: Database):
    """Verify that clear_pair properly removes all data for a pair.
    This method exists but is never called on startup.
    """
    await _populate_stale_entries(db)

    # clear_pair should remove everything for pair_1
    await db.clear_pair("pair_1")

    entries = await db.get_all_entries("pair_1")
    assert len(entries) == 0, "sync_state should be cleared"

    token = await db.get_change_token("pair_1")
    assert token is None, "change_token should be cleared"

    conflicts = await db.get_unresolved_conflicts("pair_1")
    assert len(conflicts) == 0, "conflicts should be cleared"

    logs = await db.get_recent_log(limit=100, pair_id="pair_1")
    assert len(logs) == 0, "sync_log should be cleared"

    # Active pair should be untouched
    active_entries = await db.get_all_entries("pair_0")
    assert len(active_entries) == 1, "Active pair entries should remain"


# ── Bug 1c: Engine startup should clean stale data ─────────────────


@pytest.mark.asyncio
async def test_engine_startup_does_not_clean_stale_pairs(
    db: Database, config_with_one_pair: Config, tmp_path: Path
):
    """BUG: SyncEngine.start() does not clean up entries for pairs that
    are no longer in the config.

    On daemon restart, old pair data should be purged.
    """
    from gdrive_sync.drive.mock_client import MockDriveClient, MockChangePoller, MockFileOperations

    await _populate_stale_entries(db)

    local_dir = tmp_path / "active"
    local_dir.mkdir(exist_ok=True)
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()

    mock_client = MockDriveClient(remote_dir)
    mock_ops = MockFileOperations(mock_client)
    mock_poller = MockChangePoller(mock_client)

    from gdrive_sync.sync.engine import SyncEngine

    engine = SyncEngine(
        config=config_with_one_pair,
        db=db,
        drive_client=mock_client,
        file_ops=mock_ops,
        change_poller=mock_poller,
    )

    await engine.start()
    # Give initial sync time to complete
    import asyncio
    await asyncio.sleep(0.5)
    await engine.stop()

    # Check if stale data was cleaned
    stale_entries = await db.get_all_entries("pair_1")

    # BUG: Stale entries remain because engine doesn't clean on startup
    assert len(stale_entries) == 0, (
        f"Found {len(stale_entries)} stale entries for pair_1 after engine start. "
        "Bug: SyncEngine.start() doesn't clean up removed pairs."
    )
