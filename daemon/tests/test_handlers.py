"""Tests for IPC request handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

from gdrive_sync.config import Config, SyncConfig, SyncPair
from gdrive_sync.db.database import Database
from gdrive_sync.ipc.handlers import RequestHandler
from gdrive_sync.ipc.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JsonRpcRequest,
)


@pytest.fixture
def config(tmp_path: Path):
    cfg = Config()
    cfg.sync = SyncConfig(
        poll_interval=10,
        conflict_strategy="keep_both",
        pairs=[
            SyncPair(local_path="/tmp/test_local", remote_folder_id="root", enabled=True),
            SyncPair(local_path="/tmp/test_backup", remote_folder_id="folder_abc", enabled=False, sync_mode="upload_only"),
        ],
    )
    return cfg


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_handlers.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def handler(config: Config, db: Database):
    h = RequestHandler(engine=None, config=config)
    h.set_db(db)
    return h


# ── get_status without engine ─────────────────────────────────

@pytest.mark.asyncio
async def test_get_status_no_engine(handler: RequestHandler):
    req = JsonRpcRequest(method="get_status", params={}, id=1)
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result["connected"] is False
    assert resp.result["syncing"] is False


# ── get_sync_pairs ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sync_pairs(handler: RequestHandler):
    req = JsonRpcRequest(method="get_sync_pairs", params={}, id=2)
    resp = await handler.handle(req)
    assert resp.error is None
    assert len(resp.result) == 2
    assert resp.result[0]["local_path"] == "/tmp/test_local"
    assert resp.result[0]["enabled"] is True
    assert resp.result[1]["sync_mode"] == "upload_only"


# ── add_sync_pair ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_sync_pair(handler: RequestHandler, tmp_path: Path, config: Config):
    # Point config save to temp file
    config_file = tmp_path / "config.toml"
    config.save(config_file)

    # Monkey-patch save to use temp path
    original_save = config.save
    config.save = lambda path=None: original_save(config_file)

    req = JsonRpcRequest(
        method="add_sync_pair",
        params={"local_path": "/tmp/new_folder", "remote_folder_id": "new_id"},
        id=3,
    )
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result["local_path"] == "/tmp/new_folder"
    assert resp.result["remote_folder_id"] == "new_id"
    assert len(config.sync.pairs) == 3


@pytest.mark.asyncio
async def test_add_sync_pair_missing_local_path(handler: RequestHandler):
    req = JsonRpcRequest(method="add_sync_pair", params={}, id=4)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert resp.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_add_duplicate_sync_pair(handler: RequestHandler, tmp_path: Path, config: Config):
    config_file = tmp_path / "config.toml"
    config.save(config_file)
    original_save = config.save
    config.save = lambda path=None: original_save(config_file)

    req = JsonRpcRequest(
        method="add_sync_pair",
        params={"local_path": "/tmp/test_local", "remote_folder_id": "root"},
        id=5,
    )
    resp = await handler.handle(req)
    assert resp.error is not None
    assert resp.error.code == INVALID_PARAMS


# ── remove_sync_pair ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_sync_pair(handler: RequestHandler, tmp_path: Path, config: Config):
    config_file = tmp_path / "config.toml"
    config.save(config_file)
    original_save = config.save
    config.save = lambda path=None: original_save(config_file)

    req = JsonRpcRequest(method="remove_sync_pair", params={"id": "0"}, id=6)
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result["status"] == "removed"
    assert len(config.sync.pairs) == 1


@pytest.mark.asyncio
async def test_remove_invalid_pair_id(handler: RequestHandler):
    req = JsonRpcRequest(method="remove_sync_pair", params={"id": "999"}, id=7)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert resp.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_remove_non_numeric_pair_id(handler: RequestHandler):
    req = JsonRpcRequest(method="remove_sync_pair", params={"id": "abc"}, id=8)
    resp = await handler.handle(req)
    assert resp.error is not None


# ── set_conflict_strategy ─────────────────────────────────────

@pytest.mark.asyncio
async def test_set_conflict_strategy(handler: RequestHandler, tmp_path: Path, config: Config):
    config_file = tmp_path / "config.toml"
    config.save(config_file)
    original_save = config.save
    config.save = lambda path=None: original_save(config_file)

    req = JsonRpcRequest(
        method="set_conflict_strategy",
        params={"strategy": "newest_wins"},
        id=9,
    )
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result["strategy"] == "newest_wins"
    assert config.sync.conflict_strategy == "newest_wins"


@pytest.mark.asyncio
async def test_set_invalid_conflict_strategy(handler: RequestHandler):
    req = JsonRpcRequest(
        method="set_conflict_strategy",
        params={"strategy": "invalid"},
        id=10,
    )
    resp = await handler.handle(req)
    assert resp.error is not None
    assert resp.error.code == INVALID_PARAMS


# ── set_sync_mode ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_sync_mode(handler: RequestHandler, tmp_path: Path, config: Config):
    config_file = tmp_path / "config.toml"
    config.save(config_file)
    original_save = config.save
    config.save = lambda path=None: original_save(config_file)

    req = JsonRpcRequest(
        method="set_sync_mode",
        params={"pair_id": "0", "sync_mode": "download_only"},
        id=11,
    )
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result["sync_mode"] == "download_only"
    assert config.sync.pairs[0].sync_mode == "download_only"


@pytest.mark.asyncio
async def test_set_invalid_sync_mode(handler: RequestHandler):
    req = JsonRpcRequest(
        method="set_sync_mode",
        params={"pair_id": "0", "sync_mode": "invalid_mode"},
        id=12,
    )
    resp = await handler.handle(req)
    assert resp.error is not None


# ── Methods requiring engine ──────────────────────────────────

@pytest.mark.asyncio
async def test_force_sync_without_engine(handler: RequestHandler):
    req = JsonRpcRequest(method="force_sync", params={"pair_id": "pair_0"}, id=13)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert resp.error.code == INTERNAL_ERROR


@pytest.mark.asyncio
async def test_pause_sync_without_engine(handler: RequestHandler):
    req = JsonRpcRequest(method="pause_sync", params={"pair_id": "pair_0"}, id=14)
    resp = await handler.handle(req)
    assert resp.error is not None


@pytest.mark.asyncio
async def test_resume_sync_without_engine(handler: RequestHandler):
    req = JsonRpcRequest(method="resume_sync", params={"pair_id": "pair_0"}, id=15)
    resp = await handler.handle(req)
    assert resp.error is not None


@pytest.mark.asyncio
async def test_resolve_conflict_without_engine(handler: RequestHandler):
    req = JsonRpcRequest(
        method="resolve_conflict",
        params={"conflict_id": 1, "resolution": "keep_both"},
        id=16,
    )
    resp = await handler.handle(req)
    assert resp.error is not None


# ── Unknown method ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_method(handler: RequestHandler):
    req = JsonRpcRequest(method="nonexistent_method", params={}, id=17)
    resp = await handler.handle(req)
    assert resp.error is not None
    assert resp.error.code == METHOD_NOT_FOUND


# ── Activity log ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_activity_log_empty(handler: RequestHandler):
    req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=18)
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result == []


@pytest.mark.asyncio
async def test_get_activity_log_with_entries(handler: RequestHandler, db: Database):
    from gdrive_sync.db.models import SyncLogEntry

    await db.add_log_entry(SyncLogEntry(
        action="upload", path="file.txt", pair_id="pair_0", status="ok", detail="done",
    ))

    req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=19)
    resp = await handler.handle(req)
    assert resp.error is None
    assert len(resp.result) == 1
    assert resp.result[0]["event_type"] == "upload"
    assert resp.result[0]["status"] == "ok"


# ── Conflicts ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_conflicts_empty(handler: RequestHandler):
    req = JsonRpcRequest(method="get_conflicts", params={}, id=20)
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result == []


@pytest.mark.asyncio
async def test_get_conflicts_with_entries(handler: RequestHandler, db: Database):
    from gdrive_sync.db.models import ConflictRecord

    await db.add_conflict(ConflictRecord(
        path="c.txt", pair_id="p0",
        local_md5="aaa", remote_md5="bbb",
        local_mtime=100.0, remote_mtime=200.0,
    ))

    req = JsonRpcRequest(method="get_conflicts", params={}, id=21)
    resp = await handler.handle(req)
    assert resp.error is None
    assert len(resp.result) == 1
    assert resp.result[0]["path"] == "c.txt"


# ── Auth ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_auth_no_callback(handler: RequestHandler):
    req = JsonRpcRequest(method="start_auth", params={}, id=22)
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result["status"] == "no_auth_callback"


@pytest.mark.asyncio
async def test_start_auth_with_callback(handler: RequestHandler):
    handler.set_auth_callback(lambda: {"status": "ok", "message": "demo"})

    req = JsonRpcRequest(method="start_auth", params={}, id=23)
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result["status"] == "ok"


# ── Logout ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout(handler: RequestHandler, db: Database, tmp_path: Path):
    from unittest.mock import patch

    with patch("gdrive_sync.util.paths.credentials_path", return_value=tmp_path / "creds.enc"), \
         patch("gdrive_sync.util.paths.data_dir", return_value=tmp_path):
        # Create dummy credential files
        (tmp_path / "creds.enc").write_text("secret")
        (tmp_path / "token_salt").write_text("salt")

        req = JsonRpcRequest(method="logout", params={}, id=24)
        resp = await handler.handle(req)
        assert resp.error is None
        assert resp.result["status"] == "logged_out"

        # Files should be deleted
        assert not (tmp_path / "creds.enc").exists()
        assert not (tmp_path / "token_salt").exists()

    # Log entry should be created
    logs = await db.get_recent_log(limit=10)
    auth_logs = [entry for entry in logs if entry.action == "auth" and entry.detail == "Logged out"]
    assert len(auth_logs) == 1


# ── list_remote_folders ───────────────────────────────────────

@pytest.mark.asyncio
async def test_list_remote_folders_not_authenticated(handler: RequestHandler):
    req = JsonRpcRequest(method="list_remote_folders", params={}, id=25)
    resp = await handler.handle(req)
    assert resp.error is None
    assert resp.result["folders"] == []
    assert "error" in resp.result
