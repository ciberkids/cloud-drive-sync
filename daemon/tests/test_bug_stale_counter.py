"""Regression test for issue #39: files_synced counter accumulates across re-sync cycles.

After a wipe+resync, stale DB entries for files no longer present on local or remote
must be removed so the counter reflects only current files.
"""

import pytest
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import SyncEntry, FileState


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.open()
    yield database
    await database.close()


async def test_prune_removes_stale_entries(db):
    """prune_stale_entries removes rows for paths no longer in known set."""
    pair_id = "pair_0"
    for path in ("a.txt", "b.txt", "c.txt"):
        await db.upsert_sync_entry(SyncEntry(
            path=path, pair_id=pair_id, remote_id=f"id_{path}",
            local_md5="abc", remote_md5="abc", state=FileState.SYNCED,
        ))

    removed = await db.prune_stale_entries(pair_id, {"a.txt"})

    assert removed == 2
    remaining = await db.get_all_entries(pair_id)
    assert [e.path for e in remaining] == ["a.txt"]


async def test_prune_with_empty_known_paths_removes_all(db):
    pair_id = "pair_0"
    for path in ("x.txt", "y.txt"):
        await db.upsert_sync_entry(SyncEntry(
            path=path, pair_id=pair_id, remote_id=f"id_{path}",
            local_md5="abc", remote_md5="abc", state=FileState.SYNCED,
        ))

    removed = await db.prune_stale_entries(pair_id, set())
    assert removed == 2
    assert await db.get_all_entries(pair_id) == []


async def test_prune_no_op_when_all_paths_known(db):
    pair_id = "pair_0"
    for path in ("a.txt", "b.txt"):
        await db.upsert_sync_entry(SyncEntry(
            path=path, pair_id=pair_id, remote_id=f"id_{path}",
            local_md5="abc", remote_md5="abc", state=FileState.SYNCED,
        ))

    removed = await db.prune_stale_entries(pair_id, {"a.txt", "b.txt"})
    assert removed == 0
    assert len(await db.get_all_entries(pair_id)) == 2


async def test_prune_does_not_touch_other_pairs(db):
    from cloud_drive_sync.db.models import SyncEntry

    for pair_id in ("pair_0", "pair_1"):
        await db.upsert_sync_entry(SyncEntry(
            path="file.txt", pair_id=pair_id, remote_id="id",
            local_md5="abc", remote_md5="abc", state=FileState.SYNCED,
        ))

    removed = await db.prune_stale_entries("pair_0", set())
    assert removed == 1

    pair1_entries = await db.get_all_entries("pair_1")
    assert len(pair1_entries) == 1
