"""Tests for Bug 6: files_synced and active_transfers always zero in status.

Bug: In handlers.py _get_status(), files_synced is HARDCODED to 0 (line 115).
The Database.count_by_state() method exists but is never called.
After syncing files, the status should reflect the actual count of synced files.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gdrive_sync.config import Config, SyncConfig, SyncPair
from gdrive_sync.db.database import Database
from gdrive_sync.db.models import FileState, SyncEntry
from gdrive_sync.ipc.handlers import RequestHandler
from gdrive_sync.ipc.protocol import JsonRpcRequest


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_status.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def config(tmp_path: Path):
    cfg = Config()
    cfg.sync = SyncConfig(
        pairs=[
            SyncPair(local_path=str(tmp_path / "local"), remote_folder_id="root", enabled=True),
        ],
    )
    return cfg


def _make_engine_mock(active_transfers: int = 0) -> MagicMock:
    """Create a mock SyncEngine that returns pair status."""
    engine = MagicMock()
    engine.get_status.return_value = {
        "pair_0": {
            "local_path": "/tmp/local",
            "remote_folder_id": "root",
            "active": True,
            "paused": False,
            "last_sync": "2026-01-01T00:00:00+00:00",
            "active_transfers": active_transfers,
            "errors": [],
        }
    }
    return engine


# ── Bug 6: files_synced is hardcoded to 0 ─────────────────────────


@pytest.mark.asyncio
async def test_files_synced_reflects_actual_synced_count(db: Database, config: Config):
    """BUG: files_synced is hardcoded to 0 even after files are synced.

    After upserting SYNCED entries in the database, the status endpoint should
    report the actual count. Currently it always returns 0.
    """
    # Insert some synced files into the database
    for i in range(5):
        entry = SyncEntry(
            path=f"file_{i}.txt",
            pair_id="pair_0",
            local_md5=f"md5_{i}",
            remote_md5=f"md5_{i}",
            remote_id=f"remote_{i}",
            state=FileState.SYNCED,
        )
        await db.upsert_sync_entry(entry)

    handler = RequestHandler(engine=_make_engine_mock(), config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="get_status", params={}, id=1)
    resp = await handler.handle(req)

    assert resp.error is None
    # BUG: This assertion FAILS because files_synced is hardcoded to 0
    assert resp.result["files_synced"] == 5, (
        f"Expected files_synced=5, got {resp.result['files_synced']}. "
        "Bug: _get_status() hardcodes files_synced=0 instead of querying the database."
    )


@pytest.mark.asyncio
async def test_files_synced_zero_when_no_synced_files(db: Database, config: Config):
    """When no files are synced, files_synced should be 0."""
    handler = RequestHandler(engine=_make_engine_mock(), config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="get_status", params={}, id=2)
    resp = await handler.handle(req)

    assert resp.error is None
    # This passes even with the bug (0 == 0) but validates the base case
    assert resp.result["files_synced"] == 0


@pytest.mark.asyncio
async def test_files_synced_counts_only_synced_state(db: Database, config: Config):
    """files_synced should only count entries with SYNCED state, not ERROR/CONFLICT etc.

    BUG: This test fails because files_synced is hardcoded to 0.
    """
    # 3 synced files
    for i in range(3):
        await db.upsert_sync_entry(SyncEntry(
            path=f"good_{i}.txt", pair_id="pair_0",
            local_md5=f"md5_{i}", remote_md5=f"md5_{i}",
            state=FileState.SYNCED,
        ))

    # 2 error files - should NOT be counted
    for i in range(2):
        await db.upsert_sync_entry(SyncEntry(
            path=f"bad_{i}.txt", pair_id="pair_0",
            local_md5=f"md5_err_{i}", remote_md5=f"md5_err_{i}",
            state=FileState.ERROR,
        ))

    # 1 conflict file - should NOT be counted
    await db.upsert_sync_entry(SyncEntry(
        path="conflict.txt", pair_id="pair_0",
        local_md5="md5_a", remote_md5="md5_b",
        state=FileState.CONFLICT,
    ))

    handler = RequestHandler(engine=_make_engine_mock(), config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="get_status", params={}, id=3)
    resp = await handler.handle(req)

    assert resp.error is None
    # BUG: Fails because files_synced is hardcoded to 0
    assert resp.result["files_synced"] == 3, (
        f"Expected files_synced=3 (only SYNCED state), got {resp.result['files_synced']}"
    )


@pytest.mark.asyncio
async def test_files_synced_across_multiple_pairs(db: Database, config: Config):
    """files_synced should sum across all active sync pairs.

    BUG: Fails because files_synced is hardcoded to 0.
    """
    # Add a second pair to the config
    config.sync.pairs.append(
        SyncPair(local_path="/tmp/backup", remote_folder_id="folder_abc", enabled=True)
    )

    # Files in pair_0
    for i in range(3):
        await db.upsert_sync_entry(SyncEntry(
            path=f"file_{i}.txt", pair_id="pair_0",
            state=FileState.SYNCED, local_md5=f"a{i}", remote_md5=f"a{i}",
        ))

    # Files in pair_1
    for i in range(2):
        await db.upsert_sync_entry(SyncEntry(
            path=f"file_{i}.txt", pair_id="pair_1",
            state=FileState.SYNCED, local_md5=f"b{i}", remote_md5=f"b{i}",
        ))

    engine = MagicMock()
    engine.get_status.return_value = {
        "pair_0": {
            "active": True, "paused": False, "last_sync": None,
            "active_transfers": 0, "errors": [],
        },
        "pair_1": {
            "active": True, "paused": False, "last_sync": None,
            "active_transfers": 0, "errors": [],
        },
    }

    handler = RequestHandler(engine=engine, config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="get_status", params={}, id=4)
    resp = await handler.handle(req)

    assert resp.error is None
    # BUG: Fails because files_synced is hardcoded to 0
    assert resp.result["files_synced"] == 5, (
        f"Expected files_synced=5 (3+2 across pairs), got {resp.result['files_synced']}"
    )


@pytest.mark.asyncio
async def test_active_transfers_reflects_executor(db: Database, config: Config):
    """active_transfers should reflect the sum of executor active_count values.

    This test verifies that engine.get_status() active_transfers are summed.
    """
    engine = MagicMock()
    engine.get_status.return_value = {
        "pair_0": {
            "active": True, "paused": False, "last_sync": None,
            "active_transfers": 3,
            "errors": [],
        },
    }
    # get_active_transfers returns the live transfer list;
    # active_transfers count is derived from its length.
    engine.get_active_transfers.return_value = [
        {"pair_id": "pair_0", "path": "a.txt"},
        {"pair_id": "pair_0", "path": "b.txt"},
        {"pair_id": "pair_0", "path": "c.txt"},
    ]

    handler = RequestHandler(engine=engine, config=config)
    handler.set_db(db)

    req = JsonRpcRequest(method="get_status", params={}, id=5)
    resp = await handler.handle(req)

    assert resp.error is None
    assert resp.result["active_transfers"] == 3
