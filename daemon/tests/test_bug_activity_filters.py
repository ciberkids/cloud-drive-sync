"""Tests for Bugs 4, 7, and 8: Activity log filtering issues.

Bug 4: Activity shows files not in current synced folder. Old log entries from
       removed sync pairs remain visible because get_activity_log doesn't filter
       by currently-active pairs.

Bug 7: Activity "error" section empty despite errors in "all". Errors are logged
       with action type (e.g., "upload") and status: "error", but the UI error
       filter checks event_type === "error" instead of status === "error".
       Also, "delete" filter checks event_type === "delete" but actions use
       "delete_local" / "delete_remote".

Bug 8: Auth section empty. In demo mode, no auth events are logged during
       start_auth because _start_auth doesn't log an auth event. Only _logout
       logs auth events.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cloud_drive_sync.config import Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import SyncLogEntry
from cloud_drive_sync.ipc.handlers import RequestHandler
from cloud_drive_sync.ipc.protocol import JsonRpcRequest


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_activity.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def config(tmp_path: Path):
    cfg = Config()
    cfg.sync = SyncConfig(
        pairs=[
            SyncPair(local_path=str(tmp_path / "active_folder"), remote_folder_id="root", enabled=True),
        ],
    )
    return cfg


@pytest.fixture
def handler(config: Config, db: Database):
    h = RequestHandler(engine=None, config=config)
    h.set_db(db)
    return h


# ── Bug 4: Stale activity from removed pairs ──────────────────────


@pytest.mark.asyncio
async def test_activity_log_shows_entries_from_removed_pairs(handler: RequestHandler, db: Database):
    """BUG: After removing a sync pair, its activity log entries still appear.

    Old log entries from previous sync pairs (e.g., pair_99 which no longer
    exists in config) show up in the activity log because get_activity_log
    doesn't filter by currently-active pair IDs.
    """
    # Log entries from the active pair
    await db.add_log_entry(SyncLogEntry(
        action="upload", path="active/doc.txt", pair_id="pair_0",
        status="ok", detail="",
    ))

    # Log entries from a REMOVED pair (no longer in config)
    await db.add_log_entry(SyncLogEntry(
        action="upload", path="old_project/money_prospect.xlsx", pair_id="pair_99",
        status="ok", detail="",
    ))
    await db.add_log_entry(SyncLogEntry(
        action="download", path="old_project/report.pdf", pair_id="pair_99",
        status="ok", detail="",
    ))

    req = JsonRpcRequest(method="get_activity_log", params={"limit": 50}, id=1)
    resp = await handler.handle(req)

    assert resp.error is None
    entries = resp.result

    # After fix, only the active pair's entry should be visible
    stale_entries = [
        e for e in entries if "old_project" in e["path"]
    ]
    assert len(stale_entries) == 0, (
        f"Found {len(stale_entries)} stale entries from removed pairs. "
        "Bug: get_activity_log doesn't filter by active pair IDs."
    )


@pytest.mark.asyncio
async def test_activity_log_no_pair_id_in_response(handler: RequestHandler, db: Database):
    """The activity log response doesn't include pair_id, making it impossible
    for the UI to filter client-side."""
    await db.add_log_entry(SyncLogEntry(
        action="upload", path="file.txt", pair_id="pair_0",
        status="ok", detail="",
    ))

    req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=2)
    resp = await handler.handle(req)

    assert resp.error is None
    assert len(resp.result) == 1
    entry = resp.result[0]

    # BUG: pair_id is not included in the response dict
    # The handler maps action->event_type, path, details, status, but not pair_id
    assert "pair_id" in entry, (
        "pair_id missing from activity log response. "
        "Bug: handler doesn't include pair_id in get_activity_log response."
    )


# ── Bug 7: Error filter checks event_type instead of status ───────


@pytest.mark.asyncio
async def test_error_entries_have_action_as_event_type_not_error(handler: RequestHandler, db: Database):
    """BUG: Error entries have event_type set to the action (e.g., 'upload'),
    NOT 'error'. The UI error filter checks event_type === 'error', so it
    never finds any error entries.

    The fix should either:
    - Change UI to filter by status === 'error', OR
    - Add a separate event_type field for errors
    """
    # An upload that failed with an error
    await db.add_log_entry(SyncLogEntry(
        action="upload", path="broken.txt", pair_id="pair_0",
        status="error", detail="Network timeout",
    ))

    # A download that failed
    await db.add_log_entry(SyncLogEntry(
        action="download", path="missing.txt", pair_id="pair_0",
        status="error", detail="File not found",
    ))

    # A successful upload (for comparison)
    await db.add_log_entry(SyncLogEntry(
        action="upload", path="good.txt", pair_id="pair_0",
        status="ok", detail="",
    ))

    req = JsonRpcRequest(method="get_activity_log", params={"limit": 50}, id=3)
    resp = await handler.handle(req)

    assert resp.error is None
    entries = resp.result

    # Check what the UI error filter would see
    # UI does: entries.filter(e => e.event_type === "error")
    ui_error_filter = [e for e in entries if e["event_type"] == "error"]

    # After handler fix, error entries should have event_type="error"
    assert len(ui_error_filter) == 2, (
        f"UI error filter found {len(ui_error_filter)} entries, expected 2. "
        "Bug: event_type is set to action name, not 'error', so UI filter misses them."
    )


@pytest.mark.asyncio
async def test_error_entries_include_detail(handler: RequestHandler, db: Database):
    """Error entries should include the error detail/message."""
    await db.add_log_entry(SyncLogEntry(
        action="upload", path="fail.txt", pair_id="pair_0",
        status="error", detail="Permission denied: insufficient Drive quota",
    ))

    req = JsonRpcRequest(method="get_activity_log", params={"limit": 10}, id=4)
    resp = await handler.handle(req)

    assert resp.error is None
    assert len(resp.result) == 1
    entry = resp.result[0]
    assert entry["status"] == "error"
    assert "Permission denied: insufficient Drive quota" in entry["details"]


@pytest.mark.asyncio
async def test_delete_filter_mismatches_action_names(handler: RequestHandler, db: Database):
    """BUG: UI "delete" filter checks event_type === "delete", but the actual
    action types are "delete_local" and "delete_remote" (from ActionType enum).

    The UI filter will never match because no event has event_type === "delete".
    """
    await db.add_log_entry(SyncLogEntry(
        action="delete_local", path="removed_local.txt", pair_id="pair_0",
        status="ok", detail="",
    ))
    await db.add_log_entry(SyncLogEntry(
        action="delete_remote", path="removed_remote.txt", pair_id="pair_0",
        status="ok", detail="",
    ))

    req = JsonRpcRequest(method="get_activity_log", params={"limit": 50}, id=5)
    resp = await handler.handle(req)

    assert resp.error is None
    entries = resp.result

    # What the UI "delete" filter does: event_type === "delete"
    ui_delete_filter = [e for e in entries if e["event_type"] == "delete"]

    # After handler fix, delete entries should have event_type="delete"
    assert len(ui_delete_filter) == 2, (
        f"UI delete filter found {len(ui_delete_filter)} entries, expected 2. "
        "Bug: event_type is 'delete_local'/'delete_remote', not 'delete'."
    )


# ── Bug 8: Auth section empty ─────────────────────────────────────


@pytest.mark.asyncio
async def test_start_auth_does_not_log_auth_event(handler: RequestHandler, db: Database):
    """BUG: _start_auth doesn't log an auth event to the activity log.

    In demo mode (and real auth), when authentication succeeds, no auth
    log entry is created. Only _logout logs an auth event. This means the
    'auth' filter in the UI is always empty after login.
    """
    # Set up a successful auth callback (like demo mode)
    handler.set_auth_callback(lambda: {"status": "ok", "message": "demo auth"})

    req = JsonRpcRequest(method="start_auth", params={}, id=6)
    resp = await handler.handle(req)

    assert resp.error is None
    assert resp.result["status"] == "ok"

    # Check if an auth log entry was created
    logs = await db.get_recent_log(limit=50)
    auth_logs = [entry for entry in logs if entry.action == "auth"]

    # BUG: No auth log entry is created by _start_auth
    assert len(auth_logs) == 1, (
        f"Expected 1 auth log entry after start_auth, found {len(auth_logs)}. "
        "Bug: _start_auth doesn't log an auth event."
    )


@pytest.mark.asyncio
async def test_auth_events_visible_in_activity_log(handler: RequestHandler, db: Database):
    """Auth events should be visible through the activity log handler."""
    # Manually insert an auth event (like what the fix should do)
    await db.add_log_entry(SyncLogEntry(
        action="auth", path="", pair_id="_system",
        status="success", detail="Authenticated via OAuth",
    ))

    req = JsonRpcRequest(method="get_activity_log", params={"limit": 50}, id=7)
    resp = await handler.handle(req)

    assert resp.error is None
    entries = resp.result

    # UI auth filter: event_type === "auth"
    auth_entries = [e for e in entries if e["event_type"] == "auth"]
    assert len(auth_entries) == 1, "Auth entry should be findable by event_type"
    assert auth_entries[0]["status"] == "success"


@pytest.mark.asyncio
async def test_logout_logs_auth_event(handler: RequestHandler, db: Database, tmp_path: Path):
    """Verify that logout DOES log an auth event (this works correctly)."""
    from unittest.mock import patch

    with patch("cloud_drive_sync.util.paths.credentials_path", return_value=tmp_path / "creds.enc"), \
         patch("cloud_drive_sync.util.paths.data_dir", return_value=tmp_path):

        req = JsonRpcRequest(method="logout", params={}, id=8)
        resp = await handler.handle(req)

    assert resp.error is None

    logs = await db.get_recent_log(limit=50)
    auth_logs = [entry for entry in logs if entry.action == "auth"]
    assert len(auth_logs) == 1, "Logout should create an auth log entry"
    assert auth_logs[0].detail == "Logged out"


@pytest.mark.asyncio
async def test_auth_failed_event_logged(handler: RequestHandler, db: Database):
    """BUG: If auth fails, no error event is logged either.

    A failed auth attempt should log an auth event with status 'error'.
    """
    # Callback that raises (simulating auth failure)
    def failing_auth():
        raise RuntimeError("OAuth flow cancelled by user")

    handler.set_auth_callback(failing_auth)

    req = JsonRpcRequest(method="start_auth", params={}, id=9)
    await handler.handle(req)

    # The handler catches exceptions and returns error response
    # But it should also log the failure
    logs = await db.get_recent_log(limit=50)
    auth_logs = [entry for entry in logs if entry.action == "auth"]

    # BUG: No auth event logged on failure either
    assert len(auth_logs) == 1, (
        f"Expected 1 auth failure log entry, found {len(auth_logs)}. "
        "Bug: auth failures are not logged."
    )
    if auth_logs:
        assert auth_logs[0].status == "error"
