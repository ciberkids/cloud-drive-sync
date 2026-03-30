"""Tests for reconnect button and activity log display bugs.

Bug 1: Reconnect button does nothing — after connect_daemon, status should
       show connected=True once engine is set up.

Bug 2: Activity log mkdir entries don't appear under "download" filter —
       mkdir should map to event_type="download". Also delete_local/delete_remote
       should map to "delete", errors to "error", auth to "auth".

Bug 3: Activity log entries don't show human-readable descriptions — details
       should contain labels like "File uploaded", "Directory created", etc.

Bug 4: Activity log "load more" returns duplicate entries — get_recent_log
       must accept offset and return non-overlapping pages.

Bug 5: Stale socket not cleaned up on daemon crash — IpcServer.start() should
       remove an existing socket file before binding.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cloud_drive_sync.config import Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import SyncLogEntry
from cloud_drive_sync.ipc.handlers import RequestHandler
from cloud_drive_sync.ipc.protocol import JsonRpcRequest
from cloud_drive_sync.ipc.server import IpcServer


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_reconnect.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def config(tmp_path: Path):
    cfg = Config()
    cfg.sync = SyncConfig(
        pairs=[
            SyncPair(
                local_path=str(tmp_path / "sync_folder"),
                remote_folder_id="root",
                enabled=True,
            ),
        ],
    )
    return cfg


@pytest.fixture
def handler(config: Config, db: Database):
    h = RequestHandler(engine=None, config=config)
    h.set_db(db)
    return h


# ══════════════════════════════════════════════════════════════════
# Bug 1: Reconnect button — status should reflect connection state
# ══════════════════════════════════════════════════════════════════


class TestReconnectStatus:
    """Tests for Bug 1: verify the daemon IPC contract for reconnect flow."""

    @pytest.mark.asyncio
    async def test_status_disconnected_when_no_engine(self, handler: RequestHandler):
        """EXPECTED: Without engine, get_status returns connected=False."""
        req = JsonRpcRequest(method="get_status", params={}, id=1)
        resp = await handler.handle(req)

        assert resp.error is None
        assert resp.result["connected"] is False
        assert resp.result["syncing"] is False
        assert resp.result["paused"] is False
        assert resp.result["error"] is not None  # should have auth error message

    @pytest.mark.asyncio
    async def test_status_connected_after_engine_set(self, handler: RequestHandler):
        """EXPECTED: After set_engine(), get_status returns connected=True.

        This is the key contract the Tauri reconnect flow relies on —
        after reconnecting the bridge and calling set_engine, the next
        get_status poll must show connected=True.
        """
        # Simulate reconnect: engine gets set up
        mock_engine = MagicMock()
        mock_engine.get_status.return_value = {}
        handler.set_engine(mock_engine)

        req = JsonRpcRequest(method="get_status", params={}, id=2)
        resp = await handler.handle(req)

        assert resp.error is None
        assert resp.result["connected"] is True

    @pytest.mark.asyncio
    async def test_reconnect_flow_disconnect_then_reconnect(self, handler: RequestHandler):
        """EXPECTED: Full reconnect cycle: connected -> disconnected -> connected.

        Simulates: engine active -> engine removed (crash) -> engine restored.
        """
        # 1. Start connected
        mock_engine = MagicMock()
        mock_engine.get_status.return_value = {}
        handler.set_engine(mock_engine)

        req = JsonRpcRequest(method="get_status", params={}, id=3)
        resp = await handler.handle(req)
        assert resp.result["connected"] is True

        # 2. Simulate disconnect (engine lost)
        handler._engine = None

        req = JsonRpcRequest(method="get_status", params={}, id=4)
        resp = await handler.handle(req)
        assert resp.result["connected"] is False

        # 3. Reconnect (engine restored)
        new_engine = MagicMock()
        new_engine.get_status.return_value = {}
        handler.set_engine(new_engine)

        req = JsonRpcRequest(method="get_status", params={}, id=5)
        resp = await handler.handle(req)
        assert resp.result["connected"] is True

    @pytest.mark.asyncio
    async def test_status_includes_all_required_fields(self, handler: RequestHandler):
        """EXPECTED: Status response must include all fields the UI expects."""
        required_fields = {
            "connected", "syncing", "paused", "error",
            "last_sync", "files_synced", "active_transfers",
        }

        # Test without engine
        req = JsonRpcRequest(method="get_status", params={}, id=6)
        resp = await handler.handle(req)
        assert resp.error is None
        assert required_fields.issubset(resp.result.keys()), (
            f"Missing fields: {required_fields - resp.result.keys()}"
        )

        # Test with engine
        mock_engine = MagicMock()
        mock_engine.get_status.return_value = {}
        handler.set_engine(mock_engine)

        req = JsonRpcRequest(method="get_status", params={}, id=7)
        resp = await handler.handle(req)
        assert resp.error is None
        assert required_fields.issubset(resp.result.keys())


# ══════════════════════════════════════════════════════════════════
# Bug 2: Activity log event_type mapping for UI filter tabs
# ══════════════════════════════════════════════════════════════════


class TestActivityEventTypeMapping:
    """Tests for Bug 2: mkdir must map to 'download', deletes to 'delete', etc."""

    @pytest.mark.asyncio
    async def test_mkdir_maps_to_download(self, handler: RequestHandler, db: Database):
        """EXPECTED: action='mkdir' should return event_type='download' so it
        appears under the UI download filter tab."""
        await db.add_log_entry(SyncLogEntry(
            action="mkdir", path="new_folder", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=10)
        resp = await handler.handle(req)

        assert resp.error is None
        assert len(resp.result) == 1
        assert resp.result[0]["event_type"] == "download", (
            "Bug 2: mkdir should map to event_type='download' for UI filter"
        )

    @pytest.mark.asyncio
    async def test_upload_maps_to_upload(self, handler: RequestHandler, db: Database):
        """EXPECTED: action='upload' should return event_type='upload'."""
        await db.add_log_entry(SyncLogEntry(
            action="upload", path="file.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=11)
        resp = await handler.handle(req)

        assert resp.error is None
        assert resp.result[0]["event_type"] == "upload"

    @pytest.mark.asyncio
    async def test_download_maps_to_download(self, handler: RequestHandler, db: Database):
        """EXPECTED: action='download' should return event_type='download'."""
        await db.add_log_entry(SyncLogEntry(
            action="download", path="file.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=12)
        resp = await handler.handle(req)

        assert resp.error is None
        assert resp.result[0]["event_type"] == "download"

    @pytest.mark.asyncio
    async def test_delete_local_maps_to_delete(self, handler: RequestHandler, db: Database):
        """EXPECTED: action='delete_local' should return event_type='delete'."""
        await db.add_log_entry(SyncLogEntry(
            action="delete_local", path="old.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=13)
        resp = await handler.handle(req)

        assert resp.error is None
        assert resp.result[0]["event_type"] == "delete", (
            "Bug 2: delete_local should map to event_type='delete'"
        )

    @pytest.mark.asyncio
    async def test_delete_remote_maps_to_delete(self, handler: RequestHandler, db: Database):
        """EXPECTED: action='delete_remote' should return event_type='delete'."""
        await db.add_log_entry(SyncLogEntry(
            action="delete_remote", path="old.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=14)
        resp = await handler.handle(req)

        assert resp.error is None
        assert resp.result[0]["event_type"] == "delete", (
            "Bug 2: delete_remote should map to event_type='delete'"
        )

    @pytest.mark.asyncio
    async def test_error_status_maps_to_error_event_type(self, handler: RequestHandler, db: Database):
        """EXPECTED: Any entry with status='error' should return event_type='error',
        regardless of the action."""
        await db.add_log_entry(SyncLogEntry(
            action="upload", path="fail.txt", pair_id="pair_0",
            status="error", detail="Network timeout",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=15)
        resp = await handler.handle(req)

        assert resp.error is None
        assert resp.result[0]["event_type"] == "error", (
            "Bug 2: entries with status='error' should have event_type='error'"
        )

    @pytest.mark.asyncio
    async def test_auth_maps_to_auth(self, handler: RequestHandler, db: Database):
        """EXPECTED: action='auth' should return event_type='auth'."""
        await db.add_log_entry(SyncLogEntry(
            action="auth", path="", pair_id="_system",
            status="success", detail="Authenticated",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=16)
        resp = await handler.handle(req)

        assert resp.error is None
        assert resp.result[0]["event_type"] == "auth"


# ══════════════════════════════════════════════════════════════════
# Bug 3: Activity log entries should show human-readable descriptions
# ══════════════════════════════════════════════════════════════════


class TestActivityHumanReadableDetails:
    """Tests for Bug 3: details field should have labels, not raw strings."""

    @pytest.mark.asyncio
    async def test_upload_success_shows_label(self, handler: RequestHandler, db: Database):
        """EXPECTED: Successful upload shows 'File uploaded' in details."""
        await db.add_log_entry(SyncLogEntry(
            action="upload", path="doc.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=20)
        resp = await handler.handle(req)

        assert resp.error is None
        assert "File uploaded" in resp.result[0]["details"]

    @pytest.mark.asyncio
    async def test_download_success_shows_label(self, handler: RequestHandler, db: Database):
        """EXPECTED: Successful download shows 'File downloaded' in details."""
        await db.add_log_entry(SyncLogEntry(
            action="download", path="doc.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=21)
        resp = await handler.handle(req)

        assert resp.error is None
        assert "File downloaded" in resp.result[0]["details"]

    @pytest.mark.asyncio
    async def test_mkdir_shows_directory_created(self, handler: RequestHandler, db: Database):
        """EXPECTED: mkdir shows 'Directory created' in details."""
        await db.add_log_entry(SyncLogEntry(
            action="mkdir", path="new_dir", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=22)
        resp = await handler.handle(req)

        assert resp.error is None
        assert "Directory created" in resp.result[0]["details"]

    @pytest.mark.asyncio
    async def test_delete_local_shows_label(self, handler: RequestHandler, db: Database):
        """EXPECTED: delete_local shows 'Local file deleted' in details."""
        await db.add_log_entry(SyncLogEntry(
            action="delete_local", path="removed.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=23)
        resp = await handler.handle(req)

        assert resp.error is None
        assert "Local file deleted" in resp.result[0]["details"]

    @pytest.mark.asyncio
    async def test_delete_remote_shows_label(self, handler: RequestHandler, db: Database):
        """EXPECTED: delete_remote shows 'Remote file deleted' in details."""
        await db.add_log_entry(SyncLogEntry(
            action="delete_remote", path="removed.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=24)
        resp = await handler.handle(req)

        assert resp.error is None
        assert "Remote file deleted" in resp.result[0]["details"]

    @pytest.mark.asyncio
    async def test_conflict_shows_label(self, handler: RequestHandler, db: Database):
        """EXPECTED: conflict shows 'Conflict detected' in details."""
        await db.add_log_entry(SyncLogEntry(
            action="conflict", path="edited.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=25)
        resp = await handler.handle(req)

        assert resp.error is None
        assert "Conflict detected" in resp.result[0]["details"]

    @pytest.mark.asyncio
    async def test_auth_shows_label(self, handler: RequestHandler, db: Database):
        """EXPECTED: auth shows 'Authentication' in details."""
        await db.add_log_entry(SyncLogEntry(
            action="auth", path="", pair_id="_system",
            status="success", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=26)
        resp = await handler.handle(req)

        assert resp.error is None
        assert "Authentication" in resp.result[0]["details"]

    @pytest.mark.asyncio
    async def test_error_detail_combined_with_label(self, handler: RequestHandler, db: Database):
        """EXPECTED: When there's an error detail, it's combined with the label:
        'File uploaded: Permission denied'."""
        await db.add_log_entry(SyncLogEntry(
            action="upload", path="fail.txt", pair_id="pair_0",
            status="error", detail="Permission denied",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=27)
        resp = await handler.handle(req)

        assert resp.error is None
        details = resp.result[0]["details"]
        assert "File uploaded" in details, "Should include the action label"
        assert "Permission denied" in details, "Should include the error detail"

    @pytest.mark.asyncio
    async def test_empty_detail_still_gets_label(self, handler: RequestHandler, db: Database):
        """EXPECTED: Entries with empty detail should still get a label, not be empty."""
        await db.add_log_entry(SyncLogEntry(
            action="upload", path="file.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=28)
        resp = await handler.handle(req)

        assert resp.error is None
        assert resp.result[0]["details"] != "", (
            "Bug 3: details should not be empty even when raw detail is empty"
        )
        assert resp.result[0]["details"] == "File uploaded"


# ══════════════════════════════════════════════════════════════════
# Bug 4: Activity log "load more" should return non-overlapping pages
# ══════════════════════════════════════════════════════════════════


class TestActivityLogPagination:
    """Tests for Bug 4: get_recent_log must support offset for pagination."""

    @pytest.mark.asyncio
    async def test_db_get_recent_log_with_offset(self, db: Database):
        """EXPECTED: get_recent_log(limit=2, offset=0) returns first 2 entries,
        get_recent_log(limit=2, offset=2) returns next 2 entries."""
        # Insert 4 entries
        for i in range(4):
            await db.add_log_entry(SyncLogEntry(
                action="upload", path=f"file_{i}.txt", pair_id="pair_0",
                status="ok", detail=f"entry {i}",
            ))

        page1 = await db.get_recent_log(limit=2, offset=0)
        page2 = await db.get_recent_log(limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) == 2

        # Pages should not overlap
        page1_ids = {e.id for e in page1}
        page2_ids = {e.id for e in page2}
        assert page1_ids.isdisjoint(page2_ids), (
            "Bug 4: page 1 and page 2 should not overlap"
        )

    @pytest.mark.asyncio
    async def test_db_get_recent_log_offset_returns_different_entries(self, db: Database):
        """EXPECTED: Different offsets return different entries."""
        for i in range(6):
            await db.add_log_entry(SyncLogEntry(
                action="download", path=f"doc_{i}.txt", pair_id="pair_0",
                status="ok", detail="",
            ))

        page1 = await db.get_recent_log(limit=2, offset=0)
        page2 = await db.get_recent_log(limit=2, offset=2)
        page3 = await db.get_recent_log(limit=2, offset=4)

        page1_paths = {e.path for e in page1}
        page2_paths = {e.path for e in page2}
        page3_paths = {e.path for e in page3}

        # All pages should have unique entries
        assert page1_paths.isdisjoint(page2_paths)
        assert page2_paths.isdisjoint(page3_paths)
        assert page1_paths.isdisjoint(page3_paths)

    @pytest.mark.asyncio
    async def test_handler_passes_offset_to_db(self, handler: RequestHandler, db: Database):
        """EXPECTED: _get_activity_log with offset param returns different entries
        than offset=0."""
        for i in range(4):
            await db.add_log_entry(SyncLogEntry(
                action="upload", path=f"file_{i}.txt", pair_id="pair_0",
                status="ok", detail="",
            ))

        req1 = JsonRpcRequest(
            method="get_activity_log",
            params={"limit": 2, "offset": 0},
            id=30,
        )
        resp1 = await handler.handle(req1)

        req2 = JsonRpcRequest(
            method="get_activity_log",
            params={"limit": 2, "offset": 2},
            id=31,
        )
        resp2 = await handler.handle(req2)

        assert resp1.error is None
        assert resp2.error is None
        assert len(resp1.result) == 2
        assert len(resp2.result) == 2

        # Entries should not overlap between pages
        page1_ids = {e["id"] for e in resp1.result}
        page2_ids = {e["id"] for e in resp2.result}
        assert page1_ids.isdisjoint(page2_ids), (
            "Bug 4: 'load more' returns same entries — offset is ignored"
        )

    @pytest.mark.asyncio
    async def test_db_offset_beyond_data_returns_empty(self, db: Database):
        """EXPECTED: Offset beyond available data returns empty list."""
        await db.add_log_entry(SyncLogEntry(
            action="upload", path="only.txt", pair_id="pair_0",
            status="ok", detail="",
        ))

        result = await db.get_recent_log(limit=10, offset=100)
        assert result == []


# ══════════════════════════════════════════════════════════════════
# Bug 5: Stale socket cleanup on daemon startup
# ══════════════════════════════════════════════════════════════════


@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets not available on Windows")
class TestStaleSocketCleanup:
    """Tests for Bug 5: IpcServer should remove stale socket before binding."""

    @pytest.mark.asyncio
    async def test_start_removes_existing_socket_file(self, short_tmp: Path, handler: RequestHandler):
        """EXPECTED: If a socket file already exists (from a crash), IpcServer.start()
        should remove it before binding a new one."""
        socket_file = short_tmp / "cds.sock"
        # Create a stale socket file (simulating a daemon crash)
        socket_file.write_text("stale")

        server = IpcServer(handler, path=socket_file)
        await server.start()

        try:
            # The server should have replaced the stale file with a real socket
            assert socket_file.exists()
            # It should be a Unix socket now, not a regular file
            assert stat.S_ISSOCK(socket_file.stat().st_mode), (
                "Stale regular file should have been replaced with a Unix socket"
            )
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_start_works_when_no_existing_socket(self, short_tmp: Path, handler: RequestHandler):
        """EXPECTED: IpcServer.start() works fine when no socket file exists."""
        socket_file = short_tmp / "cds.sock"
        assert not socket_file.exists()

        server = IpcServer(handler, path=socket_file)
        await server.start()

        try:
            assert socket_file.exists()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_stop_removes_socket_file(self, short_tmp: Path, handler: RequestHandler):
        """EXPECTED: IpcServer.stop() cleans up the socket file."""
        socket_file = short_tmp / "cds.sock"

        server = IpcServer(handler, path=socket_file)
        await server.start()
        assert socket_file.exists()

        await server.stop()
        assert not socket_file.exists()

    @pytest.mark.asyncio
    async def test_start_creates_parent_directory(self, short_tmp: Path, handler: RequestHandler):
        """EXPECTED: IpcServer.start() creates the parent directory if needed."""
        socket_file = short_tmp / "s" / "n" / "cds.sock"
        assert not socket_file.parent.exists()

        server = IpcServer(handler, path=socket_file)
        await server.start()

        try:
            assert socket_file.exists()
        finally:
            await server.stop()
