"""Tests for the sync executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdrive_sync.db.database import Database
from gdrive_sync.db.models import FileState, SyncEntry
from gdrive_sync.drive.mock_client import MockDriveClient, MockFileOperations
from gdrive_sync.local.scanner import LocalFileInfo
from gdrive_sync.sync.executor import SyncExecutor
from gdrive_sync.sync.planner import ActionType, SyncAction


@pytest.fixture
def demo_dirs(tmp_path: Path):
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    return local, remote


@pytest.fixture
def mock_client(demo_dirs):
    _, remote = demo_dirs
    return MockDriveClient(remote)


@pytest.fixture
def mock_ops(mock_client):
    return MockFileOperations(mock_client)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_executor.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def executor(mock_ops, db, demo_dirs, mock_client):
    local, _ = demo_dirs
    return SyncExecutor(
        mock_ops, db, local, "pair_0",
        remote_folder_id="root",
        max_concurrent=2,
        drive_client=mock_client,
    )


@pytest.mark.asyncio
async def test_execute_upload(executor: SyncExecutor, db: Database, demo_dirs):
    local, remote = demo_dirs
    (local / "upload.txt").write_text("upload me")

    action = SyncAction(
        action=ActionType.UPLOAD,
        path="upload.txt",
        local_info=LocalFileInfo(md5="abc", mtime=1000, size=9),
    )

    failed = await executor.execute_all([action])
    assert failed == []

    # File should be in remote
    assert (remote / "upload.txt").exists()
    assert (remote / "upload.txt").read_text() == "upload me"

    # Database should have synced entry
    entry = await db.get_sync_entry("upload.txt", "pair_0")
    assert entry is not None
    assert entry.state == FileState.SYNCED
    assert entry.remote_id is not None


@pytest.mark.asyncio
async def test_execute_download(executor: SyncExecutor, db: Database, demo_dirs, mock_client):
    local, remote = demo_dirs
    # Create file in remote first
    src = local / "_tmp_src.txt"
    src.write_text("download me")
    result = await mock_client.create_file("download.txt", "root", content_path=str(src))
    src.unlink()

    action = SyncAction(
        action=ActionType.DOWNLOAD,
        path="download.txt",
        remote_info={"id": result["id"], "md5Checksum": result.get("md5Checksum")},
    )

    failed = await executor.execute_all([action])
    assert failed == []

    # File should be in local
    assert (local / "download.txt").exists()
    assert (local / "download.txt").read_text() == "download me"

    # Database should have synced entry
    entry = await db.get_sync_entry("download.txt", "pair_0")
    assert entry is not None
    assert entry.state == FileState.SYNCED


@pytest.mark.asyncio
async def test_execute_delete_local(executor: SyncExecutor, db: Database, demo_dirs):
    local, _ = demo_dirs
    f = local / "delete_local.txt"
    f.write_text("goodbye")

    # Insert a stored entry
    await db.upsert_sync_entry(SyncEntry(
        path="delete_local.txt", pair_id="pair_0",
        state=FileState.SYNCED, remote_id="rid1",
    ))

    stored = await db.get_sync_entry("delete_local.txt", "pair_0")
    action = SyncAction(
        action=ActionType.DELETE_LOCAL,
        path="delete_local.txt",
        stored_entry=stored,
    )

    failed = await executor.execute_all([action])
    assert failed == []
    assert not f.exists()

    # Entry should be removed from DB
    assert await db.get_sync_entry("delete_local.txt", "pair_0") is None


@pytest.mark.asyncio
async def test_execute_delete_remote(executor: SyncExecutor, db: Database, demo_dirs, mock_client):
    local, remote = demo_dirs
    # Create file in remote
    src = local / "_tmp.txt"
    src.write_text("to delete")
    result = await mock_client.create_file("delete_remote.txt", "root", content_path=str(src))

    # Insert a stored entry
    await db.upsert_sync_entry(SyncEntry(
        path="delete_remote.txt", pair_id="pair_0",
        state=FileState.SYNCED, remote_id=result["id"],
    ))

    stored = await db.get_sync_entry("delete_remote.txt", "pair_0")
    action = SyncAction(
        action=ActionType.DELETE_REMOTE,
        path="delete_remote.txt",
        stored_entry=stored,
    )

    failed = await executor.execute_all([action])
    assert failed == []

    # Entry should be removed from DB
    assert await db.get_sync_entry("delete_remote.txt", "pair_0") is None


@pytest.mark.asyncio
async def test_execute_noop_skipped(executor: SyncExecutor):
    action = SyncAction(action=ActionType.NOOP, path="no_op.txt")
    failed = await executor.execute_all([action])
    assert failed == []


@pytest.mark.asyncio
async def test_execute_conflict_marks_state(executor: SyncExecutor, db: Database):
    # Insert a stored entry
    await db.upsert_sync_entry(SyncEntry(
        path="conflict.txt", pair_id="pair_0",
        state=FileState.SYNCED, remote_id="rid1",
        local_md5="aaa", remote_md5="bbb",
    ))

    stored = await db.get_sync_entry("conflict.txt", "pair_0")
    action = SyncAction(
        action=ActionType.CONFLICT,
        path="conflict.txt",
        stored_entry=stored,
    )

    failed = await executor.execute_all([action])
    assert failed == []

    entry = await db.get_sync_entry("conflict.txt", "pair_0")
    assert entry is not None
    assert entry.state == FileState.CONFLICT


@pytest.mark.asyncio
async def test_upload_missing_file_fails(executor: SyncExecutor):
    action = SyncAction(
        action=ActionType.UPLOAD,
        path="nonexistent.txt",
        local_info=LocalFileInfo(md5="abc", mtime=1000, size=9),
    )

    failed = await executor.execute_all([action])
    assert len(failed) == 1
    assert failed[0].path == "nonexistent.txt"


@pytest.mark.asyncio
async def test_download_no_remote_id_fails(executor: SyncExecutor):
    action = SyncAction(
        action=ActionType.DOWNLOAD,
        path="no_id.txt",
        remote_info={},  # no 'id' key
    )

    failed = await executor.execute_all([action])
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_execute_multiple_actions(executor: SyncExecutor, db: Database, demo_dirs, mock_client):
    local, remote = demo_dirs
    (local / "a.txt").write_text("aaa")
    (local / "b.txt").write_text("bbb")

    actions = [
        SyncAction(ActionType.UPLOAD, "a.txt", local_info=LocalFileInfo(md5="a", mtime=1, size=3)),
        SyncAction(ActionType.UPLOAD, "b.txt", local_info=LocalFileInfo(md5="b", mtime=1, size=3)),
        SyncAction(ActionType.NOOP, "c.txt"),
    ]

    failed = await executor.execute_all(actions)
    assert failed == []
    assert (remote / "a.txt").exists()
    assert (remote / "b.txt").exists()


@pytest.mark.asyncio
async def test_active_count_tracking(executor: SyncExecutor, demo_dirs):
    assert executor.active_count == 0

    local, _ = demo_dirs
    (local / "count.txt").write_text("data")

    action = SyncAction(
        ActionType.UPLOAD, "count.txt",
        local_info=LocalFileInfo(md5="x", mtime=1, size=4),
    )
    await executor.execute_all([action])

    # After completion, active_count should be back to 0
    assert executor.active_count == 0


@pytest.mark.asyncio
async def test_execute_upload_with_existing_id(executor: SyncExecutor, db: Database, demo_dirs, mock_client):
    local, remote = demo_dirs
    # Create initial file
    src = local / "update.txt"
    src.write_text("v1")
    result = await mock_client.create_file("update.txt", "root", content_path=str(src))

    # Insert stored entry
    await db.upsert_sync_entry(SyncEntry(
        path="update.txt", pair_id="pair_0",
        state=FileState.SYNCED, remote_id=result["id"],
        local_md5="old",
    ))

    # Update the file
    src.write_text("v2")
    stored = await db.get_sync_entry("update.txt", "pair_0")

    action = SyncAction(
        action=ActionType.UPLOAD,
        path="update.txt",
        local_info=LocalFileInfo(md5="new", mtime=2000, size=2),
        stored_entry=stored,
    )

    failed = await executor.execute_all([action])
    assert failed == []

    entry = await db.get_sync_entry("update.txt", "pair_0")
    assert entry is not None
    assert entry.state == FileState.SYNCED


@pytest.mark.asyncio
async def test_delete_local_folder_cleans_child_entries(executor: SyncExecutor, db: Database, demo_dirs):
    """Deleting a local folder should also remove child DB entries."""
    local, _ = demo_dirs
    folder = local / "docs"
    folder.mkdir()
    (folder / "a.txt").write_text("aaa")
    (folder / "b.txt").write_text("bbb")

    # Insert entries for the folder and its children
    for path in ["docs", "docs/a.txt", "docs/b.txt"]:
        await db.upsert_sync_entry(SyncEntry(
            path=path, pair_id="pair_0",
            state=FileState.SYNCED, remote_id=f"rid_{path}",
        ))

    stored = await db.get_sync_entry("docs", "pair_0")
    action = SyncAction(
        action=ActionType.DELETE_LOCAL,
        path="docs",
        stored_entry=stored,
    )

    failed = await executor.execute_all([action])
    assert failed == []
    assert not folder.exists()

    # Parent and children should all be removed from DB
    assert await db.get_sync_entry("docs", "pair_0") is None
    assert await db.get_sync_entry("docs/a.txt", "pair_0") is None
    assert await db.get_sync_entry("docs/b.txt", "pair_0") is None


@pytest.mark.asyncio
async def test_delete_remote_folder_cleans_child_entries(executor: SyncExecutor, db: Database, demo_dirs, mock_client):
    """Deleting a remote folder should also remove child DB entries."""
    local, _ = demo_dirs
    # Create a folder in mock remote
    folder_result = await mock_client.create_file("projects", "root", is_folder=True)

    # Insert entries for the folder and its children
    await db.upsert_sync_entry(SyncEntry(
        path="projects", pair_id="pair_0",
        state=FileState.SYNCED, remote_id=folder_result["id"],
    ))
    for child in ["projects/readme.md", "projects/src/main.py"]:
        await db.upsert_sync_entry(SyncEntry(
            path=child, pair_id="pair_0",
            state=FileState.SYNCED, remote_id=f"rid_{child}",
        ))

    stored = await db.get_sync_entry("projects", "pair_0")
    action = SyncAction(
        action=ActionType.DELETE_REMOTE,
        path="projects",
        stored_entry=stored,
    )

    failed = await executor.execute_all([action])
    assert failed == []

    # Parent and children should all be removed from DB
    assert await db.get_sync_entry("projects", "pair_0") is None
    assert await db.get_sync_entry("projects/readme.md", "pair_0") is None
    assert await db.get_sync_entry("projects/src/main.py", "pair_0") is None


@pytest.mark.asyncio
async def test_log_entries_created(executor: SyncExecutor, db: Database, demo_dirs):
    local, _ = demo_dirs
    (local / "logged.txt").write_text("log me")

    action = SyncAction(
        ActionType.UPLOAD, "logged.txt",
        local_info=LocalFileInfo(md5="x", mtime=1, size=6),
    )
    await executor.execute_all([action])

    logs = await db.get_recent_log(limit=10, pair_id="pair_0")
    assert len(logs) >= 1
    upload_logs = [entry for entry in logs if entry.action == "upload"]
    assert len(upload_logs) == 1
    assert upload_logs[0].status == "ok"


@pytest.mark.asyncio
async def test_delete_sync_entries_by_prefix(db: Database):
    """Direct test for Database.delete_sync_entries_by_prefix."""
    # Insert entries at various paths
    for path in ["a", "a/b.txt", "a/c.txt", "a/sub/d.txt", "ab/e.txt", "other.txt"]:
        await db.upsert_sync_entry(SyncEntry(
            path=path, pair_id="pair_0", state=FileState.SYNCED,
        ))

    count = await db.delete_sync_entries_by_prefix("a", "pair_0")
    assert count == 3  # a/b.txt, a/c.txt, a/sub/d.txt

    # "a" itself should still exist (prefix deletion only removes children)
    assert await db.get_sync_entry("a", "pair_0") is not None
    # Children should be gone
    assert await db.get_sync_entry("a/b.txt", "pair_0") is None
    assert await db.get_sync_entry("a/c.txt", "pair_0") is None
    assert await db.get_sync_entry("a/sub/d.txt", "pair_0") is None
    # "ab/e.txt" should NOT be affected (not a child of "a")
    assert await db.get_sync_entry("ab/e.txt", "pair_0") is not None
    # Unrelated entry should be untouched
    assert await db.get_sync_entry("other.txt", "pair_0") is not None


@pytest.mark.asyncio
async def test_delete_file_prefix_does_not_affect_siblings(db: Database):
    """Deleting a file should not accidentally remove entries with similar prefixes.

    When we delete "notes.txt", the prefix deletion (LIKE 'notes.txt/%')
    should not match "notes.txt.bak" or "notes.txt_old/x.txt".
    """
    # Insert the file being deleted plus entries with similar-looking prefixes
    for path in [
        "notes.txt",
        "notes.txt.bak",
        "notes.txt_old/x.txt",
        "notes.txt2",
        "other/notes.txt",
    ]:
        await db.upsert_sync_entry(SyncEntry(
            path=path, pair_id="pair_0", state=FileState.SYNCED,
        ))

    # Prefix deletion for a file: should delete 0 rows (no "notes.txt/..." children)
    count = await db.delete_sync_entries_by_prefix("notes.txt", "pair_0")
    assert count == 0

    # All sibling entries must be untouched
    assert await db.get_sync_entry("notes.txt", "pair_0") is not None
    assert await db.get_sync_entry("notes.txt.bak", "pair_0") is not None
    assert await db.get_sync_entry("notes.txt_old/x.txt", "pair_0") is not None
    assert await db.get_sync_entry("notes.txt2", "pair_0") is not None
    assert await db.get_sync_entry("other/notes.txt", "pair_0") is not None


@pytest.mark.asyncio
async def test_concurrent_uploads_no_duplicate_folders(mock_ops, db, demo_dirs, mock_client):
    """Concurrent uploads to the same subdirectory must create only one remote folder.

    Regression test: without the _mkdir_lock in _ensure_remote_dirs, multiple
    concurrent uploads targeting the same parent folder would each race through
    the check-then-create logic and create duplicate folders on Drive.
    """
    local, _ = demo_dirs

    # Create local files in a shared subdirectory
    shared = local / "shared"
    shared.mkdir()
    for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
        (shared / name).write_text(f"content of {name}")

    # Use max_concurrent=4 so all uploads run simultaneously
    executor = SyncExecutor(
        mock_ops, db, local, "pair_0",
        remote_folder_id="root",
        max_concurrent=4,
        drive_client=mock_client,
    )

    actions = [
        SyncAction(
            ActionType.UPLOAD, f"shared/{name}",
            local_info=LocalFileInfo(md5=f"md5_{name}", mtime=1000, size=10),
        )
        for name in ("a.txt", "b.txt", "c.txt", "d.txt")
    ]

    failed = await executor.execute_all(actions)
    assert failed == []

    # Query mock client for folders under root, then filter by name
    result = await mock_client.list_files(
        folder_id="root",
        query="mimeType = 'application/vnd.google-apps.folder'",
    )
    shared_folders = [f for f in result["files"] if f["name"] == "shared"]
    assert len(shared_folders) == 1, (
        f"Expected exactly 1 'shared' folder, found {len(shared_folders)}"
    )
