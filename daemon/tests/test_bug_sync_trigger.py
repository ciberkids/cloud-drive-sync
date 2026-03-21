"""Tests for Bug 3: Syncing not working - force_sync/pause/resume param issues.

Bug: The UI calls invoke("force_sync"), invoke("pause_sync"), invoke("resume_sync")
     without passing params (or passing None/{}). The Rust Tauri commands pass `None`
     as the params object. But the handlers require `pair_id` in params.

     Also: status_changed and sync_complete notifications are NEVER emitted by
     the daemon, so the UI never auto-refreshes after sync completes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cloud_drive_sync.config import Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.ipc.handlers import RequestHandler
from cloud_drive_sync.ipc.protocol import JsonRpcRequest
from cloud_drive_sync.sync.engine import SyncEngine


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_sync_trigger.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def config(tmp_path: Path):
    local = tmp_path / "local"
    local.mkdir()
    cfg = Config()
    cfg.sync = SyncConfig(
        pairs=[
            SyncPair(local_path=str(local), remote_folder_id="root", enabled=True),
        ],
    )
    return cfg


def _make_engine_mock() -> MagicMock:
    """Create a mock engine with async methods."""
    engine = MagicMock(spec=SyncEngine)
    engine.force_sync = AsyncMock(return_value=True)
    engine.pause_pair = AsyncMock(return_value=True)
    engine.resume_pair = AsyncMock(return_value=True)
    engine.get_status.return_value = {
        "pair_0": {
            "active": True, "paused": False, "last_sync": None,
            "active_transfers": 0, "errors": [],
        },
    }
    return engine


# ── Bug 3a: force_sync fails with empty/None params ───────────────


@pytest.mark.asyncio
async def test_force_sync_with_none_params(db: Database, config: Config):
    """BUG: UI calls force_sync with None params. The handler does
    params.get("pair_id") on None which raises AttributeError, caught
    as INTERNAL_ERROR.

    The handler should handle None params gracefully, ideally by using
    the first/only active pair when pair_id is not specified.
    """
    engine = _make_engine_mock()
    handler = RequestHandler(engine=engine, config=config)
    handler.set_db(db)

    # Simulate what Tauri sends: params is None
    req = JsonRpcRequest(method="force_sync", params=None, id=1)
    resp = await handler.handle(req)

    # BUG: This will be an error (AttributeError on None.get or TypeError)
    # After fix, it should work by defaulting to the active pair
    assert resp.error is None, (
        f"force_sync with None params failed: {resp.error}. "
        "Bug: handler doesn't handle None params from Tauri UI."
    )


@pytest.mark.asyncio
async def test_force_sync_with_empty_params(db: Database, config: Config):
    """BUG: UI calls force_sync with empty dict {}. pair_id is required
    but not provided, resulting in TypeError.

    When there's only one sync pair, the handler should default to it.
    """
    engine = _make_engine_mock()
    handler = RequestHandler(engine=engine, config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="force_sync", params={}, id=2)
    resp = await handler.handle(req)

    # BUG: Returns INVALID_PARAMS error because pair_id is required
    # After fix, should default to the first/only pair
    assert resp.error is None, (
        f"force_sync with empty params failed: {resp.error}. "
        "Bug: pair_id is required but UI doesn't send it."
    )
    engine.force_sync.assert_called_once()


# ── Bug 3b: pause_sync fails with empty/None params ───────────────


@pytest.mark.asyncio
async def test_pause_sync_with_none_params(db: Database, config: Config):
    """BUG: Same issue as force_sync - UI sends None params for pause."""
    engine = _make_engine_mock()
    handler = RequestHandler(engine=engine, config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="pause_sync", params=None, id=3)
    resp = await handler.handle(req)

    assert resp.error is None, (
        f"pause_sync with None params failed: {resp.error}. "
        "Bug: handler doesn't handle None params."
    )


@pytest.mark.asyncio
async def test_pause_sync_with_empty_params(db: Database, config: Config):
    """BUG: pause_sync requires pair_id but UI doesn't send it."""
    engine = _make_engine_mock()
    handler = RequestHandler(engine=engine, config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="pause_sync", params={}, id=4)
    resp = await handler.handle(req)

    assert resp.error is None, (
        f"pause_sync with empty params failed: {resp.error}. "
        "Bug: pair_id required but not sent by UI."
    )
    engine.pause_pair.assert_called_once()


# ── Bug 3c: resume_sync fails with empty/None params ──────────────


@pytest.mark.asyncio
async def test_resume_sync_with_none_params(db: Database, config: Config):
    """BUG: Same issue - UI sends None params for resume."""
    engine = _make_engine_mock()
    handler = RequestHandler(engine=engine, config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="resume_sync", params=None, id=5)
    resp = await handler.handle(req)

    assert resp.error is None, (
        f"resume_sync with None params failed: {resp.error}. "
        "Bug: handler doesn't handle None params."
    )


@pytest.mark.asyncio
async def test_resume_sync_with_empty_params(db: Database, config: Config):
    """BUG: resume_sync requires pair_id but UI doesn't send it."""
    engine = _make_engine_mock()
    handler = RequestHandler(engine=engine, config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="resume_sync", params={}, id=6)
    resp = await handler.handle(req)

    assert resp.error is None, (
        f"resume_sync with empty params failed: {resp.error}. "
        "Bug: pair_id required but not sent by UI."
    )
    engine.resume_pair.assert_called_once()


# ── Bug 3d: Verify force_sync works with valid pair_id ─────────────


@pytest.mark.asyncio
async def test_force_sync_with_valid_pair_id(db: Database, config: Config):
    """force_sync should work when pair_id is properly specified."""
    engine = _make_engine_mock()
    handler = RequestHandler(engine=engine, config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="force_sync", params={"pair_id": "pair_0"}, id=7)
    resp = await handler.handle(req)

    assert resp.error is None
    assert resp.result["status"] == "ok"
    engine.force_sync.assert_called_once_with("pair_0")


# ── Bug 3e: No sync_complete / status_changed notifications ───────


@pytest.mark.asyncio
async def test_engine_emits_sync_complete_notification(db: Database, config: Config, tmp_path: Path):
    """BUG: The SyncEngine never emits 'sync_complete' or 'status_changed'
    notifications after finishing sync operations. The UI relies on these
    to auto-refresh the status display.

    The engine has a _notify_callback but never calls it after sync.
    """
    from cloud_drive_sync.drive.mock_client import MockDriveClient, MockChangePoller, MockFileOperations

    local_dir = tmp_path / "local"
    local_dir.mkdir(exist_ok=True)
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir(exist_ok=True)

    mock_client = MockDriveClient(remote_dir)
    mock_ops = MockFileOperations(mock_client)
    mock_poller = MockChangePoller(mock_client)

    engine = SyncEngine(
        config=config,
        db=db,
        drive_client=mock_client,
        file_ops=mock_ops,
        change_poller=mock_poller,
    )

    # Track notifications
    notifications = []

    async def on_notify(method: str, params: dict = None):
        notifications.append({"method": method, "params": params})

    engine.set_notify_callback(on_notify)

    # Start and let initial sync complete
    await engine.start()
    # Give a brief moment for the initial sync task to run
    await asyncio.sleep(0.5)
    await engine.stop()

    # BUG: No notifications were emitted
    sync_notifications = [
        n for n in notifications
        if n["method"] in ("sync_complete", "status_changed")
    ]
    assert len(sync_notifications) > 0, (
        f"Expected sync_complete/status_changed notifications, got none. "
        f"All notifications: {notifications}. "
        "Bug: engine never calls _notify_callback after sync operations."
    )
