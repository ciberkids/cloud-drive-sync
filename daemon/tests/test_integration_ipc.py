"""Integration tests for IPC server end-to-end flow.

These tests start the actual IPC server on a Unix/TCP socket, connect a client,
send JSON-RPC messages over the wire, and verify responses — exercising the
full server → protocol → handler pipeline.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from cloud_drive_sync.config import Account, Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.ipc.handlers import RequestHandler
from cloud_drive_sync.ipc.server import IpcServer

pytestmark = pytest.mark.integration


# ── Helpers ───────────────────────────────────────────────────


async def rpc_call(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    method: str,
    params: dict | None = None,
    req_id: int = 1,
) -> dict:
    """Send a JSON-RPC request over the socket and return the parsed response."""
    msg = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": req_id,
    }) + "\n"
    writer.write(msg.encode())
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    return json.loads(line)


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def config(tmp_path: Path):
    cfg = Config()
    cfg.sync = SyncConfig(
        poll_interval=10,
        conflict_strategy="keep_both",
        pairs=[
            SyncPair(local_path=str(tmp_path / "test_local"), remote_folder_id="root", enabled=True),
            SyncPair(
                local_path=str(tmp_path / "test_backup"),
                remote_folder_id="folder_abc",
                enabled=False,
                sync_mode="upload_only",
            ),
        ],
    )
    cfg.accounts = [
        Account(email="user@example.com", display_name="Test User", provider="gdrive"),
    ]
    # Point config save to temp file so it doesn't touch real config
    config_file = tmp_path / "config.toml"
    cfg.save(config_file)
    original_save = cfg.save
    cfg.save = lambda path=None: original_save(config_file)
    return cfg


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_integration.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
async def ipc_server(short_tmp: Path, config: Config, db: Database):
    """Start an IPC server and yield (server, connection_info).

    On Unix: uses a Unix socket.  On Windows: uses TCP localhost.
    """
    handler = RequestHandler(engine=None, config=config)
    handler.set_db(db)

    if sys.platform == "win32":
        # On Windows, IpcServer uses TCP. Provide a port file path so it
        # doesn't try to create one in a potentially nonexistent runtime dir.
        port_file = short_tmp / "test.port"
        server = IpcServer(handler)
        server._port_file = port_file
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        yield server, ("127.0.0.1", port)
    else:
        sock = short_tmp / "t.sock"
        server = IpcServer(handler, path=sock)
        await server.start()
        yield server, sock
    await server.stop()


@pytest.fixture
async def client(ipc_server):
    """Connect a client to the IPC server and yield (reader, writer)."""
    _server, conn_info = ipc_server
    if sys.platform == "win32":
        host, port = conn_info
        reader, writer = await asyncio.open_connection(host, port)
    else:
        reader, writer = await asyncio.open_unix_connection(str(conn_info))
    yield reader, writer
    writer.close()
    await writer.wait_closed()


# ── Tests ─────────────────────────────────────────────────────


async def test_get_status(client):
    reader, writer = client
    resp = await rpc_call(reader, writer, "get_status")
    assert "error" not in resp or resp.get("error") is None
    result = resp["result"]
    assert result["connected"] is False
    assert result["syncing"] is False
    assert "daemon" in result
    assert "pid" in result["daemon"]


async def test_list_accounts(client):
    reader, writer = client
    resp = await rpc_call(reader, writer, "list_accounts", req_id=2)
    assert resp.get("error") is None
    result = resp["result"]
    assert len(result) == 1
    assert result[0]["email"] == "user@example.com"
    assert result[0]["provider"] == "gdrive"


async def test_get_sync_pairs(client, config):
    reader, writer = client
    resp = await rpc_call(reader, writer, "get_sync_pairs", req_id=3)
    assert resp.get("error") is None
    result = resp["result"]
    assert len(result) == 2
    assert result[0]["local_path"] == config.sync.pairs[0].local_path
    assert result[0]["enabled"] is True
    assert result[1]["sync_mode"] == "upload_only"


async def test_add_sync_pair_success(client, config, short_tmp):
    reader, writer = client
    new_folder = str(short_tmp / "new_folder")
    resp = await rpc_call(
        reader, writer, "add_sync_pair",
        params={"local_path": new_folder, "remote_folder_id": "new_id"},
        req_id=4,
    )
    assert resp.get("error") is None
    result = resp["result"]
    assert result["local_path"] == new_folder
    assert result["remote_folder_id"] == "new_id"
    assert result["enabled"] is True
    assert len(config.sync.pairs) == 3


async def test_add_sync_pair_validation_error(client):
    reader, writer = client
    # Missing required local_path
    resp = await rpc_call(
        reader, writer, "add_sync_pair",
        params={},
        req_id=5,
    )
    assert resp["error"] is not None
    assert resp["error"]["code"] == -32602  # INVALID_PARAMS


async def test_force_sync_without_engine(client):
    reader, writer = client
    resp = await rpc_call(
        reader, writer, "force_sync",
        params={"pair_id": "pair_0"},
        req_id=6,
    )
    assert resp["error"] is not None
    assert resp["error"]["code"] == -32603  # INTERNAL_ERROR


async def test_get_activity_log_empty(client):
    reader, writer = client
    resp = await rpc_call(
        reader, writer, "get_activity_log",
        params={"limit": 10},
        req_id=7,
    )
    assert resp.get("error") is None
    assert resp["result"] == []


async def test_get_activity_log_with_entries(client, db):
    from cloud_drive_sync.db.models import SyncLogEntry

    await db.add_log_entry(SyncLogEntry(
        action="upload", path="doc.pdf", pair_id="pair_0", status="ok", detail="done",
    ))

    reader, writer = client
    resp = await rpc_call(
        reader, writer, "get_activity_log",
        params={"limit": 10},
        req_id=8,
    )
    assert resp.get("error") is None
    assert len(resp["result"]) == 1
    assert resp["result"][0]["event_type"] == "upload"
    assert resp["result"][0]["path"] == "doc.pdf"


async def test_set_sync_mode(client, config):
    reader, writer = client
    resp = await rpc_call(
        reader, writer, "set_sync_mode",
        params={"pair_id": "0", "sync_mode": "download_only"},
        req_id=9,
    )
    assert resp.get("error") is None
    assert resp["result"]["sync_mode"] == "download_only"
    assert config.sync.pairs[0].sync_mode == "download_only"


async def test_set_sync_mode_invalid(client):
    reader, writer = client
    resp = await rpc_call(
        reader, writer, "set_sync_mode",
        params={"pair_id": "0", "sync_mode": "bad_mode"},
        req_id=10,
    )
    assert resp["error"] is not None


async def test_set_account_max_transfers(client, config):
    reader, writer = client
    resp = await rpc_call(
        reader, writer, "set_account_max_transfers",
        params={"email": "user@example.com", "max_concurrent_transfers": 5},
        req_id=11,
    )
    assert resp.get("error") is None
    assert resp["result"]["max_concurrent_transfers"] == 5
    assert config.accounts[0].max_concurrent_transfers == 5


async def test_set_account_max_transfers_unknown_email(client):
    reader, writer = client
    resp = await rpc_call(
        reader, writer, "set_account_max_transfers",
        params={"email": "nobody@example.com", "max_concurrent_transfers": 1},
        req_id=12,
    )
    assert resp["error"] is not None


async def test_unknown_method(client):
    reader, writer = client
    resp = await rpc_call(reader, writer, "nonexistent_method", req_id=13)
    assert resp["error"] is not None
    assert resp["error"]["code"] == -32601  # METHOD_NOT_FOUND


async def test_malformed_json(client):
    reader, writer = client
    writer.write(b"not valid json\n")
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    resp = json.loads(line)
    assert resp["error"] is not None
    assert resp["error"]["code"] == -32700  # PARSE_ERROR


async def test_multiple_sequential_requests(client):
    """Verify the server handles multiple requests on the same connection."""
    reader, writer = client
    for i in range(5):
        resp = await rpc_call(reader, writer, "get_status", req_id=100 + i)
        assert resp.get("error") is None
        assert resp["id"] == 100 + i
        assert resp["result"]["connected"] is False
