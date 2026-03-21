"""Tests for folder sync support.

Covers:
- Planner: MKDIR actions for remote folders in initial and continuous sync
- Planner: Google-native docs are still skipped, but folders are not
- Planner: Folder deletion handling
- Executor: _do_mkdir creates local directories and DB entries
- Continuous sync: remote folder changes produce MKDIR
- Continuous sync: local directory creation produces MKDIR
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import FileState, SyncEntry
from cloud_drive_sync.drive.mock_client import MockDriveClient, MockFileOperations
from cloud_drive_sync.local.scanner import LocalFileInfo
from cloud_drive_sync.sync.executor import SyncExecutor
from cloud_drive_sync.sync.planner import (
    FOLDER_MIME,
    ActionType,
    SyncAction,
    filter_actions_by_mode,
    plan_continuous_sync,
    plan_initial_sync,
)


# ── Planner: plan_initial_sync ─────────────────────────────────────


class TestInitialSyncFolders:
    def test_remote_folder_produces_mkdir(self):
        """A remote folder (no local counterpart) should produce a MKDIR action."""
        local: dict = {}
        remote = [
            {
                "name": "photos",
                "relativePath": "photos",
                "mimeType": FOLDER_MIME,
                "id": "folder_1",
            }
        ]
        actions = plan_initial_sync(local, remote)
        assert len(actions) == 1
        assert actions[0].action == ActionType.MKDIR
        assert actions[0].path == "photos"
        assert actions[0].remote_info is not None

    def test_nested_remote_folder_produces_mkdir(self):
        """Nested remote folders should each produce a MKDIR action."""
        local: dict = {}
        remote = [
            {
                "name": "docs",
                "relativePath": "docs",
                "mimeType": FOLDER_MIME,
                "id": "folder_docs",
            },
            {
                "name": "drafts",
                "relativePath": "docs/drafts",
                "mimeType": FOLDER_MIME,
                "id": "folder_drafts",
            },
        ]
        actions = plan_initial_sync(local, remote)
        mkdirs = [a for a in actions if a.action == ActionType.MKDIR]
        assert len(mkdirs) == 2
        paths = {a.path for a in mkdirs}
        assert paths == {"docs", "docs/drafts"}

    def test_google_docs_still_skipped(self):
        """Google Docs/Sheets/Slides should still be skipped."""
        local: dict = {}
        remote = [
            {
                "name": "My Doc",
                "relativePath": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
            },
            {
                "name": "My Sheet",
                "relativePath": "My Sheet",
                "mimeType": "application/vnd.google-apps.spreadsheet",
            },
            {
                "name": "My Slides",
                "relativePath": "My Slides",
                "mimeType": "application/vnd.google-apps.presentation",
            },
        ]
        actions = plan_initial_sync(local, remote)
        assert len(actions) == 0

    def test_folder_and_file_mixed(self):
        """Folders produce MKDIR, regular files produce DOWNLOAD."""
        local: dict = {}
        remote = [
            {
                "name": "photos",
                "relativePath": "photos",
                "mimeType": FOLDER_MIME,
                "id": "folder_1",
            },
            {
                "name": "readme.txt",
                "relativePath": "readme.txt",
                "md5Checksum": "abc123",
                "mimeType": "text/plain",
            },
        ]
        actions = plan_initial_sync(local, remote)
        types = {a.path: a.action for a in actions}
        assert types["photos"] == ActionType.MKDIR
        assert types["readme.txt"] == ActionType.DOWNLOAD

    def test_folder_exists_both_sides_noop(self):
        """When a folder path exists in local_files and remote, it should NOOP.

        Note: In a real scenario, local_files only tracks files, not directories.
        But if a local file and remote folder happen to share a path, the folder
        mime check produces a NOOP.
        """
        local = {"shared": LocalFileInfo(md5="x", mtime=1, size=0)}
        remote = [
            {
                "name": "shared",
                "relativePath": "shared",
                "mimeType": FOLDER_MIME,
                "id": "folder_shared",
            }
        ]
        actions = plan_initial_sync(local, remote)
        assert len(actions) == 1
        assert actions[0].action == ActionType.NOOP
        assert actions[0].reason == "folder in sync"


# ── Planner: plan_continuous_sync ───────────────────────────────────


class TestContinuousSyncFolders:
    def test_new_remote_folder_mkdir(self):
        """A new remote folder change (not deleted) should produce MKDIR."""
        changes = [
            {
                "path": "new_folder",
                "source": "remote",
                "deleted": False,
                "md5": None,
                "mtime": 0,
                "mimeType": FOLDER_MIME,
                "remote_info": {"id": "rfid1", "mimeType": FOLDER_MIME},
            }
        ]
        actions = plan_continuous_sync(changes, {})
        assert len(actions) == 1
        assert actions[0].action == ActionType.MKDIR
        assert actions[0].reason == "new remote folder"

    def test_remote_folder_change_already_tracked_noop(self):
        """A remote folder change for an already-tracked folder should NOOP."""
        stored = {
            "existing_folder": SyncEntry(
                path="existing_folder",
                pair_id="p0",
                state=FileState.SYNCED,
                remote_id="rfid1",
            )
        }
        changes = [
            {
                "path": "existing_folder",
                "source": "remote",
                "deleted": False,
                "md5": None,
                "mtime": 0,
                "mimeType": FOLDER_MIME,
                "remote_info": {"id": "rfid1", "mimeType": FOLDER_MIME},
            }
        ]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.NOOP

    def test_deleted_remote_folder_deletes_local(self):
        """When a remote folder is deleted/trashed, it should produce DELETE_LOCAL."""
        stored = {
            "old_folder": SyncEntry(
                path="old_folder",
                pair_id="p0",
                state=FileState.SYNCED,
                remote_id="rfid1",
            )
        }
        changes = [
            {
                "path": "old_folder",
                "source": "remote",
                "deleted": True,
                "md5": None,
                "mtime": 0,
                "mimeType": FOLDER_MIME,
            }
        ]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.DELETE_LOCAL

    def test_new_local_directory_mkdir(self):
        """A new local directory creation should produce MKDIR."""
        changes = [
            {
                "path": "my_new_dir",
                "source": "local",
                "deleted": False,
                "md5": None,
                "mtime": 100,
                "is_directory": True,
            }
        ]
        actions = plan_continuous_sync(changes, {})
        assert len(actions) == 1
        assert actions[0].action == ActionType.MKDIR
        assert actions[0].reason == "new local directory"

    def test_local_directory_already_tracked_noop(self):
        """A local directory change for an already-tracked directory should NOOP."""
        stored = {
            "tracked_dir": SyncEntry(
                path="tracked_dir",
                pair_id="p0",
                state=FileState.SYNCED,
                remote_id="rfid1",
            )
        }
        changes = [
            {
                "path": "tracked_dir",
                "source": "local",
                "deleted": False,
                "md5": None,
                "mtime": 100,
                "is_directory": True,
            }
        ]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.NOOP

    def test_deleted_local_directory_deletes_remote(self):
        """When a local directory is deleted, it should produce DELETE_REMOTE."""
        stored = {
            "gone_dir": SyncEntry(
                path="gone_dir",
                pair_id="p0",
                state=FileState.SYNCED,
                remote_id="rfid1",
            )
        }
        changes = [
            {
                "path": "gone_dir",
                "source": "local",
                "deleted": True,
                "md5": None,
                "mtime": 0,
                "is_directory": True,
            }
        ]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.DELETE_REMOTE

    def test_mime_from_remote_info_fallback(self):
        """When mimeType is not top-level, it should be read from remote_info."""
        changes = [
            {
                "path": "folder_via_info",
                "source": "remote",
                "deleted": False,
                "md5": None,
                "mtime": 0,
                "remote_info": {
                    "id": "rfid2",
                    "mimeType": FOLDER_MIME,
                },
            }
        ]
        actions = plan_continuous_sync(changes, {})
        assert len(actions) == 1
        assert actions[0].action == ActionType.MKDIR


# ── Planner: filter_actions_by_mode with MKDIR ─────────────────────


class TestFilterMkdir:
    def test_mkdir_passes_upload_only(self):
        """MKDIR should not be blocked by upload_only mode."""
        actions = [SyncAction(ActionType.MKDIR, "new_dir")]
        result = filter_actions_by_mode(actions, "upload_only")
        assert len(result) == 1
        assert result[0].action == ActionType.MKDIR

    def test_mkdir_passes_download_only(self):
        """MKDIR should not be blocked by download_only mode."""
        actions = [SyncAction(ActionType.MKDIR, "new_dir")]
        result = filter_actions_by_mode(actions, "download_only")
        assert len(result) == 1
        assert result[0].action == ActionType.MKDIR


# ── Executor: _do_mkdir ─────────────────────────────────────────────


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
    database = Database(tmp_path / "test_folder_sync.db")
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
async def test_executor_mkdir_creates_directory(executor: SyncExecutor, db: Database, demo_dirs):
    """MKDIR action should create the local directory and a DB entry."""
    local, _ = demo_dirs
    action = SyncAction(
        action=ActionType.MKDIR,
        path="new_folder",
        remote_info={"id": "rfid_new"},
    )

    failed = await executor.execute_all([action])
    assert failed == []
    assert (local / "new_folder").is_dir()

    entry = await db.get_sync_entry("new_folder", "pair_0")
    assert entry is not None
    assert entry.state == FileState.SYNCED
    assert entry.remote_id == "rfid_new"


@pytest.mark.asyncio
async def test_executor_mkdir_nested(executor: SyncExecutor, db: Database, demo_dirs):
    """MKDIR with nested path should create parent directories too."""
    local, _ = demo_dirs
    action = SyncAction(
        action=ActionType.MKDIR,
        path="a/b/c",
        remote_info={"id": "rfid_nested"},
    )

    failed = await executor.execute_all([action])
    assert failed == []
    assert (local / "a" / "b" / "c").is_dir()

    entry = await db.get_sync_entry("a/b/c", "pair_0")
    assert entry is not None
    assert entry.state == FileState.SYNCED


@pytest.mark.asyncio
async def test_executor_mkdir_already_exists(executor: SyncExecutor, db: Database, demo_dirs):
    """MKDIR on an existing directory should succeed (exist_ok=True)."""
    local, _ = demo_dirs
    (local / "existing").mkdir()

    action = SyncAction(
        action=ActionType.MKDIR,
        path="existing",
        remote_info={"id": "rfid_existing"},
    )

    failed = await executor.execute_all([action])
    assert failed == []
    assert (local / "existing").is_dir()

    entry = await db.get_sync_entry("existing", "pair_0")
    assert entry is not None
    assert entry.state == FileState.SYNCED


@pytest.mark.asyncio
async def test_executor_mkdir_no_remote_info(executor: SyncExecutor, db: Database, demo_dirs):
    """MKDIR without remote_info (local directory creation) should still work."""
    local, _ = demo_dirs
    action = SyncAction(
        action=ActionType.MKDIR,
        path="local_only_dir",
    )

    failed = await executor.execute_all([action])
    assert failed == []
    assert (local / "local_only_dir").is_dir()

    entry = await db.get_sync_entry("local_only_dir", "pair_0")
    assert entry is not None
    assert entry.state == FileState.SYNCED
    # When a drive client is available, the executor creates the remote
    # directory via _ensure_remote_dirs, so remote_id will be set.
    assert entry.remote_id is not None


@pytest.mark.asyncio
async def test_executor_mkdir_log_entry(executor: SyncExecutor, db: Database, demo_dirs):
    """MKDIR should produce a log entry."""
    action = SyncAction(
        action=ActionType.MKDIR,
        path="logged_dir",
        remote_info={"id": "rfid_log"},
    )
    await executor.execute_all([action])

    logs = await db.get_recent_log(limit=10, pair_id="pair_0")
    mkdir_logs = [entry for entry in logs if entry.action == "mkdir"]
    assert len(mkdir_logs) == 1
    assert mkdir_logs[0].status == "ok"


@pytest.mark.asyncio
async def test_delete_local_removes_directory(executor: SyncExecutor, db: Database, demo_dirs):
    """DELETE_LOCAL should remove a directory and its DB entry."""
    local, _ = demo_dirs
    d = local / "to_delete"
    d.mkdir()
    (d / "file.txt").write_text("content")

    await db.upsert_sync_entry(SyncEntry(
        path="to_delete", pair_id="pair_0",
        state=FileState.SYNCED, remote_id="rfid_del",
    ))

    stored = await db.get_sync_entry("to_delete", "pair_0")
    action = SyncAction(
        action=ActionType.DELETE_LOCAL,
        path="to_delete",
        stored_entry=stored,
    )

    failed = await executor.execute_all([action])
    assert failed == []
    assert not d.exists()
    assert await db.get_sync_entry("to_delete", "pair_0") is None


# ── Integration-style test: initial sync with folders ───────────────


@pytest.mark.asyncio
async def test_initial_sync_creates_remote_folders(
    executor: SyncExecutor, db: Database, demo_dirs, mock_client
):
    """Full flow: initial sync planning + execution creates local folders from remote."""
    local, _ = demo_dirs

    # Create a remote folder via mock client
    await mock_client.create_file("Documents", "root", is_folder=True)

    # List remote (like the engine would)
    remote_files = await mock_client.list_all_recursive("root")

    # Plan
    local_files: dict[str, LocalFileInfo] = {}
    actions = plan_initial_sync(local_files, remote_files)

    mkdirs = [a for a in actions if a.action == ActionType.MKDIR]
    assert len(mkdirs) == 1
    assert mkdirs[0].path == "Documents"

    # Execute
    failed = await executor.execute_all(actions)
    assert failed == []
    assert (local / "Documents").is_dir()

    entry = await db.get_sync_entry("Documents", "pair_0")
    assert entry is not None
    assert entry.state == FileState.SYNCED


@pytest.mark.asyncio
async def test_initial_sync_nested_remote_folders(
    executor: SyncExecutor, db: Database, demo_dirs, mock_client
):
    """Initial sync with nested remote folders creates the correct local directory tree."""
    local, _ = demo_dirs

    # Create nested folders
    parent = await mock_client.create_file("Projects", "root", is_folder=True)
    await mock_client.create_file("Backend", parent["id"], is_folder=True)

    remote_files = await mock_client.list_all_recursive("root")
    local_files: dict[str, LocalFileInfo] = {}
    actions = plan_initial_sync(local_files, remote_files)

    mkdirs = [a for a in actions if a.action == ActionType.MKDIR]
    assert len(mkdirs) == 2

    failed = await executor.execute_all(actions)
    assert failed == []
    assert (local / "Projects").is_dir()
    assert (local / "Projects" / "Backend").is_dir()
