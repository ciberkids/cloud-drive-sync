"""Integration tests using demo mode components (no real Google credentials needed)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gdrive_sync.config import Config, SyncConfig, SyncPair
from gdrive_sync.db.database import Database
from gdrive_sync.db.models import FileState
from gdrive_sync.drive.mock_client import MockChangePoller, MockDriveClient, MockFileOperations
from gdrive_sync.ipc.handlers import RequestHandler
from gdrive_sync.ipc.protocol import JsonRpcRequest
from gdrive_sync.sync.engine import SyncEngine


@pytest.fixture
def demo_dirs(tmp_path: Path):
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    return local, remote


@pytest.fixture
def demo_config(demo_dirs):
    local, remote = demo_dirs
    config = Config()
    config.sync = SyncConfig(
        poll_interval=1,
        conflict_strategy="keep_both",
        max_concurrent_transfers=2,
        debounce_delay=0.2,
        pairs=[SyncPair(local_path=str(local), remote_folder_id="root", enabled=True)],
    )
    return config


@pytest.fixture
def mock_client(demo_dirs):
    _, remote = demo_dirs
    return MockDriveClient(remote)


@pytest.fixture
def mock_ops(mock_client):
    return MockFileOperations(mock_client)


@pytest.fixture
def mock_poller(mock_client):
    return MockChangePoller(mock_client)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_integration.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
async def engine(demo_config, db, mock_client, mock_ops, mock_poller):
    eng = SyncEngine(
        demo_config, db, mock_client, file_ops=mock_ops, change_poller=mock_poller
    )
    yield eng
    await eng.stop()


# ── Mock Client Unit Tests ──────────────────────────────────────


@pytest.mark.integration
async def test_mock_client_create_and_get(mock_client: MockDriveClient, demo_dirs):
    local, remote = demo_dirs
    # Create a temp file to upload
    src = local / "hello.txt"
    src.write_text("hello world")

    result = await mock_client.create_file("hello.txt", "root", content_path=str(src))
    assert result["id"].startswith("mock_")
    assert result["name"] == "hello.txt"
    assert result["md5Checksum"] is not None

    # Get should return same metadata
    fetched = await mock_client.get_file(result["id"])
    assert fetched["name"] == "hello.txt"
    assert fetched["md5Checksum"] == result["md5Checksum"]

    # File should exist in remote dir
    assert (remote / "hello.txt").exists()
    assert (remote / "hello.txt").read_text() == "hello world"


@pytest.mark.integration
async def test_mock_client_list_files(mock_client: MockDriveClient, demo_dirs):
    local, _ = demo_dirs
    src = local / "a.txt"
    src.write_text("aaa")
    await mock_client.create_file("a.txt", "root", content_path=str(src))

    src2 = local / "b.txt"
    src2.write_text("bbb")
    await mock_client.create_file("b.txt", "root", content_path=str(src2))

    result = await mock_client.list_files("root")
    names = {f["name"] for f in result["files"]}
    assert "a.txt" in names
    assert "b.txt" in names


@pytest.mark.integration
async def test_mock_client_update_file(mock_client: MockDriveClient, demo_dirs):
    local, remote = demo_dirs
    src = local / "update_me.txt"
    src.write_text("version 1")
    created = await mock_client.create_file("update_me.txt", "root", content_path=str(src))
    old_md5 = created["md5Checksum"]

    # Update content
    src.write_text("version 2")
    updated = await mock_client.update_file(created["id"], content_path=str(src))
    assert updated["md5Checksum"] != old_md5
    assert (remote / "update_me.txt").read_text() == "version 2"


@pytest.mark.integration
async def test_mock_client_delete(mock_client: MockDriveClient, demo_dirs):
    local, remote = demo_dirs
    src = local / "delete_me.txt"
    src.write_text("goodbye")
    created = await mock_client.create_file("delete_me.txt", "root", content_path=str(src))

    await mock_client.delete_file(created["id"])
    assert not (remote / "delete_me.txt").exists()

    with pytest.raises(FileNotFoundError):
        await mock_client.get_file(created["id"])


@pytest.mark.integration
async def test_mock_client_trash(mock_client: MockDriveClient, demo_dirs):
    local, remote = demo_dirs
    src = local / "trash_me.txt"
    src.write_text("to trash")
    created = await mock_client.create_file("trash_me.txt", "root", content_path=str(src))

    trashed = await mock_client.trash_file(created["id"])
    assert trashed["trashed"] is True

    # Should not appear in list_files (trashed files filtered)
    result = await mock_client.list_files("root")
    ids = {f["id"] for f in result["files"]}
    assert created["id"] not in ids


@pytest.mark.integration
async def test_mock_about(mock_client: MockDriveClient):
    about = await mock_client.get_about()
    assert about["user"]["displayName"] == "Demo User"
    assert "storageQuota" in about


# ── Mock Operations Tests ───────────────────────────────────────


@pytest.mark.integration
async def test_mock_ops_upload_download(mock_ops: MockFileOperations, demo_dirs):
    local, remote = demo_dirs
    src = local / "roundtrip.txt"
    src.write_text("round trip content")

    # Upload
    result = await mock_ops.upload_file(src, "root")
    assert result["id"].startswith("mock_")
    assert (remote / "roundtrip.txt").exists()

    # Download to a new location
    dest = local / "downloaded.txt"
    await mock_ops.download_file(result["id"], dest)
    assert dest.read_text() == "round trip content"


@pytest.mark.integration
async def test_mock_ops_upload_update(mock_ops: MockFileOperations, demo_dirs):
    local, remote = demo_dirs
    src = local / "versioned.txt"
    src.write_text("v1")

    created = await mock_ops.upload_file(src, "root")

    src.write_text("v2")
    updated = await mock_ops.upload_file(src, "root", existing_id=created["id"])
    assert updated["md5Checksum"] != created["md5Checksum"]
    assert (remote / "versioned.txt").read_text() == "v2"


# ── Change Polling Tests ────────────────────────────────────────


@pytest.mark.integration
async def test_mock_poller_detects_new_file(
    mock_client: MockDriveClient, mock_poller: MockChangePoller, demo_dirs
):
    _, remote = demo_dirs

    # Take initial snapshot
    token = await mock_poller.get_start_page_token()

    # Drop a new file directly in the remote dir (simulating external change)
    (remote / "new_file.txt").write_text("appeared externally")

    # Poll should detect it
    changes, new_token = await mock_poller.poll_changes(token)
    assert len(changes) >= 1
    new_file_changes = [c for c in changes if c.file_name == "new_file.txt"]
    assert len(new_file_changes) == 1
    assert new_file_changes[0].md5 is not None
    assert not new_file_changes[0].removed


@pytest.mark.integration
async def test_mock_poller_detects_modification(
    mock_client: MockDriveClient, mock_poller: MockChangePoller, demo_dirs
):
    local, remote = demo_dirs
    src = local / "modify_me.txt"
    src.write_text("original")

    await mock_client.create_file("modify_me.txt", "root", content_path=str(src))
    token = await mock_poller.get_start_page_token()

    # Modify the file in remote
    (remote / "modify_me.txt").write_text("modified content")

    changes, _ = await mock_poller.poll_changes(token)
    assert len(changes) >= 1
    mod_changes = [c for c in changes if c.file_name == "modify_me.txt" and not c.removed]
    assert len(mod_changes) == 1


# ── Sync Engine Integration Tests ──────────────────────────────


@pytest.mark.integration
async def test_engine_initial_sync_uploads_local_file(
    engine: SyncEngine, db: Database, demo_dirs, mock_client: MockDriveClient
):
    local, remote = demo_dirs

    # Create a local file before starting engine
    (local / "notes.txt").write_text("my notes")

    await engine.start()
    # Give the engine a moment to complete initial sync
    await asyncio.sleep(1.0)

    # The file should have been uploaded to mock remote
    assert (remote / "notes.txt").exists()
    assert (remote / "notes.txt").read_text() == "my notes"

    # Database should have a synced entry
    entry = await db.get_sync_entry("notes.txt", "pair_0")
    assert entry is not None
    assert entry.state == FileState.SYNCED
    assert entry.remote_id is not None


@pytest.mark.integration
async def test_engine_initial_sync_downloads_remote_file(
    engine: SyncEngine, db: Database, demo_dirs
):
    local, remote = demo_dirs

    # Create a file in remote before starting engine
    (remote / "from_drive.txt").write_text("from the cloud")

    await engine.start()
    await asyncio.sleep(1.0)

    # The file should have been downloaded to local
    assert (local / "from_drive.txt").exists()
    assert (local / "from_drive.txt").read_text() == "from the cloud"


@pytest.mark.integration
async def test_engine_detects_conflict(
    engine: SyncEngine, db: Database, demo_dirs
):
    local, remote = demo_dirs

    # Create same file on both sides with different content
    (local / "conflict.txt").write_text("local version")
    (remote / "conflict.txt").write_text("remote version")

    await engine.start()
    await asyncio.sleep(1.0)

    # With keep_both strategy, both versions should be preserved
    local_files = list(local.iterdir())
    filenames = [f.name for f in local_files if f.is_file()]

    # Should have at least the original and a conflict copy
    conflict_files = [f for f in filenames if "conflict" in f]
    assert len(conflict_files) >= 1


# ── IPC Handler Tests (via demo engine) ─────────────────────────


@pytest.mark.integration
async def test_ipc_get_status(engine: SyncEngine, demo_config: Config, demo_dirs):
    local, _ = demo_dirs
    (local / "status_test.txt").write_text("testing status")

    await engine.start()
    await asyncio.sleep(0.5)

    handler = RequestHandler(engine, demo_config)

    request = JsonRpcRequest(method="get_status", params={}, id=1)
    response = await handler.handle(request)
    assert response.error is None
    assert response.result["connected"] is True
    assert "files_synced" in response.result
    assert "active_transfers" in response.result


@pytest.mark.integration
async def test_ipc_get_sync_pairs(engine: SyncEngine, demo_config: Config):
    handler = RequestHandler(engine, demo_config)
    request = JsonRpcRequest(method="get_sync_pairs", params={}, id=2)
    response = await handler.handle(request)
    assert response.error is None
    assert len(response.result) == 1
    assert response.result[0]["enabled"] is True


@pytest.mark.integration
async def test_ipc_get_activity_log(
    engine: SyncEngine, demo_config: Config, demo_dirs
):
    local, _ = demo_dirs
    (local / "logged.txt").write_text("log me")

    await engine.start()
    await asyncio.sleep(1.0)

    handler = RequestHandler(engine, demo_config)
    request = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=3)
    response = await handler.handle(request)
    assert response.error is None
    assert isinstance(response.result, list)


@pytest.mark.integration
async def test_ipc_pause_resume(engine: SyncEngine, demo_config: Config):
    await engine.start()
    await asyncio.sleep(0.5)

    handler = RequestHandler(engine, demo_config)

    # Pause
    request = JsonRpcRequest(method="pause_sync", params={"pair_id": "pair_0"}, id=4)
    response = await handler.handle(request)
    assert response.result["status"] == "paused"

    # Resume
    request = JsonRpcRequest(method="resume_sync", params={"pair_id": "pair_0"}, id=5)
    response = await handler.handle(request)
    assert response.result["status"] == "resumed"


@pytest.mark.integration
async def test_ipc_force_sync(engine: SyncEngine, demo_config: Config):
    await engine.start()
    await asyncio.sleep(0.5)

    handler = RequestHandler(engine, demo_config)
    request = JsonRpcRequest(method="force_sync", params={"pair_id": "pair_0"}, id=6)
    response = await handler.handle(request)
    assert response.result["status"] == "ok"
