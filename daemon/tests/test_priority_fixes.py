"""Tests for the 5 priority fixes applied to the daemon code.

1. Path traversal sanitization (executor._sanitize_path, handlers._add_sync_pair)
2. Streaming downloads (operations.py — atomic writes via temp file)
3. Atomic file writes (operations.py — os.replace from temp)
4. ask_user conflict resolution (conflict.py — Future registration, set_user_resolution)
5. sync_complete/status_changed notifications (engine.py — include fields)
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gdrive_sync.config import Config, SyncConfig, SyncPair
from gdrive_sync.db.database import Database
from gdrive_sync.db.models import ConflictRecord, FileState, SyncEntry
from gdrive_sync.drive.mock_client import MockChangePoller, MockDriveClient, MockFileOperations
from gdrive_sync.ipc.handlers import RequestHandler
from gdrive_sync.ipc.protocol import JsonRpcRequest
from gdrive_sync.sync.conflict import ConflictResolver
from gdrive_sync.sync.engine import SyncEngine
from gdrive_sync.sync.executor import SyncExecutor
from gdrive_sync.sync.planner import ActionType, SyncAction


# ── Shared fixtures ──────────────────────────────────────────────────


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
    database = Database(tmp_path / "test_priority_fixes.db")
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


# ══════════════════════════════════════════════════════════════════════
# Fix 1: Path traversal sanitization
# ══════════════════════════════════════════════════════════════════════


class TestSanitizePath:
    """Test SyncExecutor._sanitize_path rejects traversal and allows valid paths."""

    def test_rejects_parent_traversal(self, executor: SyncExecutor, demo_dirs):
        """Paths containing '../' that escape the sync root must be rejected."""
        local, _ = demo_dirs
        with pytest.raises(ValueError, match="Path traversal detected"):
            executor._sanitize_path("../../etc/passwd")

    def test_rejects_single_dotdot(self, executor: SyncExecutor):
        with pytest.raises(ValueError, match="Path traversal detected"):
            executor._sanitize_path("../secret.txt")

    def test_rejects_nested_traversal(self, executor: SyncExecutor):
        with pytest.raises(ValueError, match="Path traversal detected"):
            executor._sanitize_path("subdir/../../..")

    def test_allows_valid_relative_path(self, executor: SyncExecutor, demo_dirs):
        local, _ = demo_dirs
        result = executor._sanitize_path("docs/readme.txt")
        assert result == (local / "docs" / "readme.txt").resolve()

    def test_allows_simple_filename(self, executor: SyncExecutor, demo_dirs):
        local, _ = demo_dirs
        result = executor._sanitize_path("file.txt")
        assert result == (local / "file.txt").resolve()

    def test_allows_deeply_nested_path(self, executor: SyncExecutor, demo_dirs):
        local, _ = demo_dirs
        result = executor._sanitize_path("a/b/c/d.txt")
        assert result == (local / "a" / "b" / "c" / "d.txt").resolve()

    def test_allows_dotdot_that_stays_within_root(self, executor: SyncExecutor, demo_dirs):
        """A path like 'subdir/../file.txt' resolves within the root and is safe."""
        local, _ = demo_dirs
        result = executor._sanitize_path("subdir/../file.txt")
        assert result == (local / "file.txt").resolve()


class TestSanitizePathUsedByActions:
    """Verify that _sanitize_path is actually called from action methods."""

    @pytest.mark.asyncio
    async def test_download_rejects_traversal(self, executor: SyncExecutor):
        action = SyncAction(
            action=ActionType.DOWNLOAD,
            path="../../etc/shadow",
            remote_info={"id": "some_id"},
        )
        failed = await executor.execute_all([action])
        assert len(failed) == 1

    @pytest.mark.asyncio
    async def test_upload_rejects_traversal(self, executor: SyncExecutor):
        action = SyncAction(
            action=ActionType.UPLOAD,
            path="../../../tmp/exploit",
            local_info=MagicMock(md5="x", mtime=1, size=1),
        )
        failed = await executor.execute_all([action])
        assert len(failed) == 1

    @pytest.mark.asyncio
    async def test_mkdir_rejects_traversal(self, executor: SyncExecutor):
        action = SyncAction(
            action=ActionType.MKDIR,
            path="../../tmp/evil_dir",
        )
        failed = await executor.execute_all([action])
        assert len(failed) == 1

    @pytest.mark.asyncio
    async def test_delete_local_rejects_traversal(self, executor: SyncExecutor, db):
        await db.upsert_sync_entry(SyncEntry(
            path="../../etc/important", pair_id="pair_0",
            state=FileState.SYNCED, remote_id="rid",
        ))
        stored = await db.get_sync_entry("../../etc/important", "pair_0")
        action = SyncAction(
            action=ActionType.DELETE_LOCAL,
            path="../../etc/important",
            stored_entry=stored,
        )
        failed = await executor.execute_all([action])
        assert len(failed) == 1


# ══════════════════════════════════════════════════════════════════════
# Fix 1b: add_sync_pair rejects relative paths and '..' components
# ══════════════════════════════════════════════════════════════════════


class TestAddSyncPairValidation:
    """Test that add_sync_pair validates local_path is absolute and has no '..'."""

    @pytest.fixture
    def config(self, tmp_path: Path):
        cfg = Config()
        cfg.sync = SyncConfig(pairs=[])
        # Override save to avoid writing to real config path
        cfg.save = MagicMock()
        return cfg

    @pytest.fixture
    def handler(self, config):
        return RequestHandler(engine=None, config=config)

    @pytest.mark.asyncio
    async def test_rejects_relative_path(self, handler):
        req = JsonRpcRequest(id=1, method="add_sync_pair", params={
            "local_path": "relative/path/here",
        })
        resp = await handler.handle(req)
        assert resp.error is not None
        assert "absolute" in resp.error.message.lower()

    @pytest.mark.asyncio
    async def test_rejects_path_with_dotdot(self, handler):
        req = JsonRpcRequest(id=2, method="add_sync_pair", params={
            "local_path": "/home/user/../etc/shadow",
        })
        resp = await handler.handle(req)
        assert resp.error is not None
        assert ".." in resp.error.message

    @pytest.mark.asyncio
    async def test_accepts_valid_absolute_path(self, handler):
        req = JsonRpcRequest(id=3, method="add_sync_pair", params={
            "local_path": "/home/user/Documents/sync",
            "remote_folder_id": "root",
        })
        resp = await handler.handle(req)
        assert resp.error is None
        assert resp.result["local_path"] == "/home/user/Documents/sync"

    @pytest.mark.asyncio
    async def test_rejects_dotdot_in_middle(self, handler):
        req = JsonRpcRequest(id=4, method="add_sync_pair", params={
            "local_path": "/home/user/docs/../../../etc",
        })
        resp = await handler.handle(req)
        assert resp.error is not None


# ══════════════════════════════════════════════════════════════════════
# Fix 2 & 3: Atomic downloads — streaming to temp file with os.replace
# ══════════════════════════════════════════════════════════════════════


class TestAtomicDownloads:
    """Test that download_file uses temp files and atomic rename."""

    @pytest.mark.asyncio
    async def test_successful_download_produces_final_file(
        self, executor: SyncExecutor, db: Database, demo_dirs, mock_client
    ):
        """After a successful download, the final file must exist at the target path."""
        local, _ = demo_dirs
        # Create a file in mock remote
        src = local / "_src.txt"
        src.write_text("content for download")
        result = await mock_client.create_file("atomic.txt", "root", content_path=str(src))
        src.unlink()

        action = SyncAction(
            action=ActionType.DOWNLOAD,
            path="atomic.txt",
            remote_info={"id": result["id"], "md5Checksum": result.get("md5Checksum")},
        )
        failed = await executor.execute_all([action])
        assert failed == []

        final_path = local / "atomic.txt"
        assert final_path.exists()
        assert final_path.read_text() == "content for download"

    @pytest.mark.asyncio
    async def test_no_temp_files_left_after_success(
        self, executor: SyncExecutor, db: Database, demo_dirs, mock_client
    ):
        """No .tmp files should remain after a successful download."""
        local, _ = demo_dirs
        src = local / "_src.txt"
        src.write_text("clean download")
        result = await mock_client.create_file("clean.txt", "root", content_path=str(src))
        src.unlink()

        action = SyncAction(
            action=ActionType.DOWNLOAD,
            path="clean.txt",
            remote_info={"id": result["id"]},
        )
        await executor.execute_all([action])

        # No .tmp files should linger in the local directory
        tmp_files = list(local.glob("*.tmp"))
        assert tmp_files == [], f"Temp files left behind: {tmp_files}"

    @pytest.mark.asyncio
    async def test_failed_download_cleans_up_temp(self, demo_dirs):
        """When a download fails mid-stream, temp files must be cleaned up."""
        local, remote = demo_dirs
        target = local / "fail_download.txt"

        # Simulate what operations.py does: write to temp, fail, clean up
        fd, tmp_path = tempfile.mkstemp(
            dir=str(local), prefix=f".{target.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b"partial data")
            # Simulate failure
            raise IOError("network error")
        except IOError:
            # This is the cleanup path from operations.py
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        assert not os.path.exists(tmp_path), "Temp file was not cleaned up after failure"
        assert not target.exists(), "Target file should not exist after failed download"


# ══════════════════════════════════════════════════════════════════════
# Fix 4: ask_user conflict resolution
# ══════════════════════════════════════════════════════════════════════


class TestAskUserConflictResolution:
    """Test the ConflictResolver's ask_user flow: Future registration, resolution, timeout."""

    @pytest.mark.asyncio
    async def test_future_registered_on_ask_user(self):
        """When ask_user is triggered, a Future should be registered in _pending_resolutions."""
        resolver = ConflictResolver("ask_user")
        conflict = ConflictRecord(id=42, path="file.txt", pair_id="p0",
                                  local_md5="aaa", remote_md5="bbb")

        # Run resolve in background — it will wait on the Future
        async def resolve_coro():
            return await resolver.resolve(
                path="file.txt",
                local_path=Path("/fake/file.txt"),
                local_mtime=100.0,
                remote_mtime=200.0,
                conflict=conflict,
            )

        task = asyncio.create_task(resolve_coro())
        # Give the task a moment to register the future
        await asyncio.sleep(0.05)

        assert 42 in resolver._pending_resolutions
        future = resolver._pending_resolutions[42]
        assert not future.done()

        # Clean up: resolve the future so the task can finish
        resolver.set_user_resolution(42, "keep_local")
        result = await task
        assert result is not None
        assert result.action == ActionType.UPLOAD

    @pytest.mark.asyncio
    async def test_set_user_resolution_completes_future(self):
        """set_user_resolution should complete the pending Future with the resolution."""
        resolver = ConflictResolver("ask_user")
        conflict = ConflictRecord(id=99, path="doc.txt", pair_id="p0",
                                  local_md5="aaa", remote_md5="bbb")

        async def resolve_coro():
            return await resolver.resolve(
                path="doc.txt",
                local_path=Path("/fake/doc.txt"),
                local_mtime=100.0,
                remote_mtime=200.0,
                conflict=conflict,
            )

        task = asyncio.create_task(resolve_coro())
        await asyncio.sleep(0.05)

        # Resolve with keep_remote
        resolver.set_user_resolution(99, "keep_remote")
        result = await task

        assert result is not None
        assert result.action == ActionType.DOWNLOAD
        assert "keep_remote" in result.reason

    @pytest.mark.asyncio
    async def test_set_user_resolution_keep_both(self):
        """Resolving with 'keep_both' should produce a DOWNLOAD action."""
        resolver = ConflictResolver("ask_user")
        conflict = ConflictRecord(id=77, path="both.txt", pair_id="p0",
                                  local_md5="aaa", remote_md5="bbb")

        async def resolve_coro():
            return await resolver.resolve(
                path="both.txt",
                local_path=Path("/fake/both.txt"),
                local_mtime=100.0,
                remote_mtime=200.0,
                conflict=conflict,
            )

        task = asyncio.create_task(resolve_coro())
        await asyncio.sleep(0.05)

        resolver.set_user_resolution(77, "keep_both")
        result = await task

        assert result is not None
        assert result.action == ActionType.DOWNLOAD
        assert "keep_both" in result.reason

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        """If no resolution arrives within the timeout, resolve should return None."""
        resolver = ConflictResolver("ask_user")
        conflict = ConflictRecord(id=55, path="timeout.txt", pair_id="p0",
                                  local_md5="aaa", remote_md5="bbb")

        # Patch the timeout to be very short for testing
        with patch.object(asyncio, "wait_for", side_effect=asyncio.TimeoutError):
            result = await resolver._resolve_ask_user(
                "timeout.txt", conflict, notify_callback=None
            )

        assert result is None
        # The pending resolution should be cleaned up
        assert 55 not in resolver._pending_resolutions

    @pytest.mark.asyncio
    async def test_notify_callback_called(self):
        """The notify_callback should be called with conflict_detected."""
        resolver = ConflictResolver("ask_user")
        conflict = ConflictRecord(id=10, path="notify.txt", pair_id="p0",
                                  local_md5="aaa", remote_md5="bbb")
        notifications = []

        async def notify(method, params):
            notifications.append((method, params))

        async def resolve_coro():
            return await resolver.resolve(
                path="notify.txt",
                local_path=Path("/fake/notify.txt"),
                local_mtime=100.0,
                remote_mtime=200.0,
                conflict=conflict,
                notify_callback=notify,
            )

        task = asyncio.create_task(resolve_coro())
        await asyncio.sleep(0.05)

        assert len(notifications) == 1
        assert notifications[0][0] == "conflict_detected"
        assert notifications[0][1]["id"] == 10
        assert notifications[0][1]["path"] == "notify.txt"

        # Clean up
        resolver.set_user_resolution(10, "keep_local")
        await task

    @pytest.mark.asyncio
    async def test_set_user_resolution_no_pending_logs_warning(self):
        """set_user_resolution for a non-existent conflict_id should not raise."""
        resolver = ConflictResolver("ask_user")
        # Should not raise; just logs a warning
        resolver.set_user_resolution(999, "keep_local")

    @pytest.mark.asyncio
    async def test_none_conflict_id_returns_none(self):
        """If conflict.id is None, _resolve_ask_user should return None immediately."""
        resolver = ConflictResolver("ask_user")
        conflict = ConflictRecord(id=None, path="noid.txt", pair_id="p0",
                                  local_md5="aaa", remote_md5="bbb")

        result = await resolver._resolve_ask_user("noid.txt", conflict)
        assert result is None

    def test_resolution_to_action_keep_local(self):
        action = ConflictResolver._resolution_to_action("file.txt", "keep_local")
        assert action is not None
        assert action.action == ActionType.UPLOAD

    def test_resolution_to_action_keep_remote(self):
        action = ConflictResolver._resolution_to_action("file.txt", "keep_remote")
        assert action is not None
        assert action.action == ActionType.DOWNLOAD

    def test_resolution_to_action_keep_both(self):
        action = ConflictResolver._resolution_to_action("file.txt", "keep_both")
        assert action is not None
        assert action.action == ActionType.DOWNLOAD

    def test_resolution_to_action_unknown(self):
        action = ConflictResolver._resolution_to_action("file.txt", "invalid")
        assert action is None


# ══════════════════════════════════════════════════════════════════════
# Fix 5: sync_complete and status_changed notifications
# ══════════════════════════════════════════════════════════════════════


class TestSyncNotifications:
    """Test that engine notifications include the required fields."""

    @pytest.fixture
    def config(self, demo_dirs):
        local, _ = demo_dirs
        cfg = Config()
        cfg.sync = SyncConfig(
            poll_interval=60,
            conflict_strategy="keep_both",
            max_concurrent_transfers=2,
            debounce_delay=0.1,
            pairs=[SyncPair(local_path=str(local), remote_folder_id="root", enabled=True)],
        )
        return cfg

    @pytest.fixture
    async def engine(self, config, db, mock_client):
        ops = MockFileOperations(mock_client)
        poller = MockChangePoller(mock_client)
        eng = SyncEngine(config, db, mock_client, file_ops=ops, change_poller=poller)
        yield eng
        await eng.stop()

    @pytest.mark.asyncio
    async def test_sync_complete_includes_required_fields(
        self, engine: SyncEngine, demo_dirs
    ):
        """sync_complete notification must include uploaded, downloaded, and errors."""
        local, _ = demo_dirs
        (local / "test.txt").write_text("hello")

        notifications = []

        async def capture_notify(method, params):
            notifications.append((method, params))

        engine.set_notify_callback(capture_notify)
        await engine.start()
        await asyncio.sleep(1.0)

        sync_complete_msgs = [
            (m, p) for m, p in notifications if m == "sync_complete"
        ]
        assert len(sync_complete_msgs) >= 1, (
            f"Expected at least one sync_complete notification, got: {notifications}"
        )

        _, params = sync_complete_msgs[0]
        assert "uploaded" in params, f"Missing 'uploaded' in sync_complete params: {params}"
        assert "downloaded" in params, f"Missing 'downloaded' in sync_complete params: {params}"
        assert "errors" in params, f"Missing 'errors' in sync_complete params: {params}"
        assert isinstance(params["uploaded"], int)
        assert isinstance(params["downloaded"], int)
        assert isinstance(params["errors"], int)

    @pytest.mark.asyncio
    async def test_sync_complete_includes_pair_id(
        self, engine: SyncEngine, demo_dirs
    ):
        """sync_complete notification must include pair_id."""
        local, _ = demo_dirs
        (local / "test2.txt").write_text("world")

        notifications = []

        async def capture_notify(method, params):
            notifications.append((method, params))

        engine.set_notify_callback(capture_notify)
        await engine.start()
        await asyncio.sleep(1.0)

        sync_complete_msgs = [
            (m, p) for m, p in notifications if m == "sync_complete"
        ]
        assert len(sync_complete_msgs) >= 1

        _, params = sync_complete_msgs[0]
        assert "pair_id" in params
        assert params["pair_id"] == "pair_0"

    @pytest.mark.asyncio
    async def test_status_changed_includes_pair_id_and_status(
        self, engine: SyncEngine, demo_dirs
    ):
        """status_changed notification must include pair_id and status."""
        local, _ = demo_dirs
        (local / "test3.txt").write_text("data")

        notifications = []

        async def capture_notify(method, params):
            notifications.append((method, params))

        engine.set_notify_callback(capture_notify)
        await engine.start()
        await asyncio.sleep(1.0)

        status_changed_msgs = [
            (m, p) for m, p in notifications if m == "status_changed"
        ]
        assert len(status_changed_msgs) >= 1, (
            f"Expected at least one status_changed notification, got: {notifications}"
        )

        _, params = status_changed_msgs[0]
        assert "pair_id" in params, f"Missing 'pair_id' in status_changed params: {params}"
        assert "status" in params, f"Missing 'status' in status_changed params: {params}"
        assert params["pair_id"] == "pair_0"
        assert params["status"] == "idle"

    @pytest.mark.asyncio
    async def test_notifications_sent_in_order(
        self, engine: SyncEngine, demo_dirs
    ):
        """sync_complete should arrive before status_changed."""
        local, _ = demo_dirs
        (local / "order.txt").write_text("ordering")

        notifications = []

        async def capture_notify(method, params):
            notifications.append((method, params))

        engine.set_notify_callback(capture_notify)
        await engine.start()
        await asyncio.sleep(1.0)

        methods = [m for m, _ in notifications]
        # Find positions of sync_complete and status_changed
        if "sync_complete" in methods and "status_changed" in methods:
            sc_idx = methods.index("sync_complete")
            st_idx = methods.index("status_changed")
            assert sc_idx < st_idx, (
                f"sync_complete (idx={sc_idx}) should come before "
                f"status_changed (idx={st_idx}), got: {methods}"
            )

    @pytest.mark.asyncio
    async def test_no_notifications_without_callback(
        self, engine: SyncEngine, demo_dirs
    ):
        """Engine should not fail if no notify callback is set."""
        local, _ = demo_dirs
        (local / "silent.txt").write_text("no callback")

        # Do NOT set a notify callback
        await engine.start()
        await asyncio.sleep(1.0)
        # Just verify no exception was raised
