"""Tests for the sync engine control methods and lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cloud_drive_sync.config import Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import FileState, SyncEntry
from cloud_drive_sync.drive.changes import RemoteChange
from cloud_drive_sync.drive.mock_client import MockChangePoller, MockDriveClient, MockFileOperations
from cloud_drive_sync.sync.engine import SyncEngine


@pytest.fixture
def demo_dirs(tmp_path: Path):
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    return local, remote


@pytest.fixture
def config(demo_dirs):
    local, _ = demo_dirs
    cfg = Config()
    cfg.sync = SyncConfig(
        poll_interval=60,  # long interval to avoid background polling noise
        conflict_strategy="keep_both",
        max_concurrent_transfers=2,
        debounce_delay=0.1,
        pairs=[SyncPair(local_path=str(local), remote_folder_id="root", enabled=True)],
    )
    return cfg


@pytest.fixture
def mock_client(demo_dirs):
    _, remote = demo_dirs
    return MockDriveClient(remote)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_engine.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
async def engine(config, db, mock_client):
    ops = MockFileOperations(mock_client)
    poller = MockChangePoller(mock_client)
    eng = SyncEngine(config, db, mock_client, file_ops=ops, change_poller=poller)
    yield eng
    await eng.stop()


class TestEngineLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_pair(self, engine: SyncEngine, demo_dirs):
        local, _ = demo_dirs
        (local / "startup.txt").write_text("data")

        await engine.start()
        await asyncio.sleep(0.5)

        assert "pair_0" in engine.pairs
        ps = engine.pairs["pair_0"]
        assert ps.active is True

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, engine: SyncEngine):
        await engine.start()
        await asyncio.sleep(0.2)
        await engine.stop()
        await engine.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_disabled_pair_not_started(self, config: Config, db, mock_client, demo_dirs):
        config.sync.pairs[0].enabled = False
        ops = MockFileOperations(mock_client)
        poller = MockChangePoller(mock_client)
        eng = SyncEngine(config, db, mock_client, file_ops=ops, change_poller=poller)
        try:
            await eng.start()
            await asyncio.sleep(0.2)
            assert len(eng.pairs) == 0
        finally:
            await eng.stop()

    @pytest.mark.asyncio
    async def test_nonexistent_local_path_skipped(self, config: Config, db, mock_client):
        config.sync.pairs[0].local_path = "/nonexistent/path/12345"
        ops = MockFileOperations(mock_client)
        poller = MockChangePoller(mock_client)
        eng = SyncEngine(config, db, mock_client, file_ops=ops, change_poller=poller)
        try:
            await eng.start()
            await asyncio.sleep(0.2)
            assert len(eng.pairs) == 0
        finally:
            await eng.stop()


class TestPauseResume:
    @pytest.mark.asyncio
    async def test_pause_pair(self, engine: SyncEngine):
        await engine.start()
        await asyncio.sleep(0.3)

        result = await engine.pause_pair("pair_0")
        assert result is True
        assert engine.pairs["pair_0"].paused is True

    @pytest.mark.asyncio
    async def test_pause_nonexistent_pair(self, engine: SyncEngine):
        result = await engine.pause_pair("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_pair(self, engine: SyncEngine):
        await engine.start()
        await asyncio.sleep(0.3)

        await engine.pause_pair("pair_0")
        result = await engine.resume_pair("pair_0")
        assert result is True
        assert engine.pairs["pair_0"].paused is False

    @pytest.mark.asyncio
    async def test_resume_nonexistent_pair(self, engine: SyncEngine):
        result = await engine.resume_pair("nonexistent")
        assert result is False


class TestForceSync:
    @pytest.mark.asyncio
    async def test_force_sync(self, engine: SyncEngine, demo_dirs):
        local, _ = demo_dirs
        (local / "force.txt").write_text("data")

        await engine.start()
        await asyncio.sleep(0.3)

        result = await engine.force_sync("pair_0")
        assert result is True

    @pytest.mark.asyncio
    async def test_force_sync_nonexistent(self, engine: SyncEngine):
        result = await engine.force_sync("nonexistent")
        assert result is False


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_after_start(self, engine: SyncEngine, demo_dirs):
        local, _ = demo_dirs
        (local / "status.txt").write_text("data")

        await engine.start()
        await asyncio.sleep(0.5)

        status = engine.get_status()
        assert "pair_0" in status
        assert status["pair_0"]["active"] is True
        assert isinstance(status["pair_0"]["errors"], list)

    @pytest.mark.asyncio
    async def test_get_status_empty(self, engine: SyncEngine):
        status = engine.get_status()
        assert status == {}


class TestConflictResolver:
    @pytest.mark.asyncio
    async def test_conflict_resolver_property(self, engine: SyncEngine):
        assert engine.conflict_resolver is not None
        assert engine.conflict_resolver.strategy == "keep_both"


class TestNotifyCallback:
    @pytest.mark.asyncio
    async def test_set_notify_callback(self, engine: SyncEngine):
        called = []

        async def callback(method, params):
            called.append((method, params))

        engine.set_notify_callback(callback)
        # The callback is stored but not directly callable from tests
        # without triggering a notification event


class TestMultiplePairs:
    @pytest.mark.asyncio
    async def test_multiple_pairs(self, db, tmp_path: Path):
        local1 = tmp_path / "local1"
        local2 = tmp_path / "local2"
        remote1 = tmp_path / "remote1"
        remote2 = tmp_path / "remote2"
        local1.mkdir()
        local2.mkdir()
        remote1.mkdir()
        remote2.mkdir()

        (local1 / "file1.txt").write_text("data1")
        (local2 / "file2.txt").write_text("data2")

        config = Config()
        config.sync = SyncConfig(
            poll_interval=60,
            pairs=[
                SyncPair(local_path=str(local1), remote_folder_id="root", enabled=True),
                SyncPair(local_path=str(local2), remote_folder_id="root", enabled=True),
            ],
        )

        client = MockDriveClient(remote1)
        ops = MockFileOperations(client)
        poller = MockChangePoller(client)
        eng = SyncEngine(config, db, client, file_ops=ops, change_poller=poller)
        try:
            await eng.start()
            await asyncio.sleep(1.0)

            assert "pair_0" in eng.pairs
            assert "pair_1" in eng.pairs
        finally:
            await eng.stop()


class TestRemoteChangeFiltering:
    """Verify that remote changes outside the monitored folder are ignored."""

    @pytest.mark.asyncio
    async def test_unrelated_delete_ignored(self, engine: SyncEngine, db, demo_dirs):
        """A deleted folder outside the monitored folder must not delete locally."""
        local, _ = demo_dirs
        (local / "House").mkdir()

        await engine.start()
        await asyncio.sleep(0.5)

        ps = engine.pairs["pair_0"]

        # Simulate a remote change for an unrelated folder (different parent)
        unrelated_changes = [
            RemoteChange(
                file_id="unrelated_id_123",
                file_name="House",
                mime_type="application/vnd.google-apps.folder",
                removed=True,
                trashed=False,
                parents=[],  # removed files have no parent info
            ),
        ]
        await engine._process_remote_changes(ps, unrelated_changes)

        # Local "House" folder must still exist
        assert (local / "House").exists(), "Local folder was deleted by unrelated remote change!"

    @pytest.mark.asyncio
    async def test_unrelated_new_file_not_downloaded(self, engine: SyncEngine, db, demo_dirs):
        """A new file in an unrelated Drive folder must not be downloaded."""
        local, _ = demo_dirs

        await engine.start()
        await asyncio.sleep(0.5)

        ps = engine.pairs["pair_0"]

        unrelated_changes = [
            RemoteChange(
                file_id="unrelated_file_456",
                file_name="report.pdf",
                mime_type="application/pdf",
                md5="abc123",
                removed=False,
                trashed=False,
                parents=["some_other_folder_id"],
            ),
        ]
        await engine._process_remote_changes(ps, unrelated_changes)

        # No file should have been downloaded
        assert not (local / "report.pdf").exists(), "Unrelated file was downloaded!"

    @pytest.mark.asyncio
    async def test_unrelated_modification_ignored(self, engine: SyncEngine, db, demo_dirs):
        """A modified file in an unrelated Drive folder must not trigger a download."""
        local, _ = demo_dirs

        await engine.start()
        await asyncio.sleep(0.5)

        ps = engine.pairs["pair_0"]

        unrelated_changes = [
            RemoteChange(
                file_id="unrelated_mod_789",
                file_name="budget.xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                md5="new_md5",
                removed=False,
                trashed=False,
                parents=["completely_different_folder"],
            ),
        ]
        await engine._process_remote_changes(ps, unrelated_changes)

        # No file should appear locally
        assert not (local / "budget.xlsx").exists(), "Unrelated modified file was downloaded!"

    @pytest.mark.asyncio
    async def test_tracked_file_change_still_processed(self, engine: SyncEngine, db, demo_dirs):
        """Changes to files we ARE tracking should still be processed."""
        local, _ = demo_dirs
        (local / "tracked.txt").write_text("original")

        await engine.start()
        await asyncio.sleep(0.5)

        ps = engine.pairs["pair_0"]

        # Store a tracked entry in the DB
        entry = SyncEntry(
            path="tracked.txt",
            pair_id="pair_0",
            state=FileState.SYNCED,
            local_md5="orig_md5",
            remote_md5="orig_md5",
            remote_id="tracked_remote_id",
        )
        await db.upsert_sync_entry(entry)

        # This change IS for a tracked file (file_id matches remote_id in DB)
        tracked_changes = [
            RemoteChange(
                file_id="tracked_remote_id",
                file_name="tracked.txt",
                mime_type="text/plain",
                md5="new_remote_md5",
                removed=False,
                trashed=False,
                parents=["root"],
            ),
        ]
        await engine._process_remote_changes(ps, tracked_changes)
        # We just verify it didn't raise — the executor will handle the actual download
