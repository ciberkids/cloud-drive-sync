"""Regression test: locally deleted file must not be re-downloaded on next initial sync.

Bug: in two-way / newest_wins mode, deleting a file locally then restarting the
daemon caused plan_initial_sync to see 'remote only' and generate DOWNLOAD,
re-fetching the file the user intentionally removed.

Fix: plan_initial_sync now accepts stored_entries; a remote-only path whose stored
entry exists (was previously synced) and whose local tree is non-empty is treated
as locally deleted and generates DELETE_REMOTE instead.
"""

from __future__ import annotations

from cloud_drive_sync.db.models import FileState, SyncEntry
from cloud_drive_sync.local.scanner import LocalFileInfo
from cloud_drive_sync.sync.planner import ActionType, plan_initial_sync


def _remote(path: str, md5: str = "abc", rid: str = "rid1") -> dict:
    return {
        "name": path,
        "relativePath": path,
        "md5Checksum": md5,
        "mimeType": "text/plain",
        "id": rid,
    }


def _stored(path: str, remote_id: str = "rid1", md5: str = "abc") -> SyncEntry:
    return SyncEntry(
        path=path,
        pair_id="p0",
        state=FileState.SYNCED,
        local_md5=md5,
        remote_md5=md5,
        remote_id=remote_id,
    )


class TestLocalDeleteNotRedownloaded:
    def test_remote_only_with_stored_entry_generates_delete_remote(self):
        """Core regression: remote-only + in DB + local tree non-empty → DELETE_REMOTE."""
        local = {"other.txt": LocalFileInfo(md5="x", mtime=100, size=10)}
        remote = [_remote("deleted.txt")]
        stored = {"deleted.txt": _stored("deleted.txt")}

        actions = plan_initial_sync(local, remote, stored_entries=stored)

        types = {a.path: a.action for a in actions}
        assert types["deleted.txt"] == ActionType.DELETE_REMOTE
        assert types["other.txt"] == ActionType.UPLOAD

    def test_remote_only_with_stored_entry_empty_local_generates_download(self):
        """Safety guard: if local tree is empty, don't mass-delete (detached mount)."""
        local: dict = {}
        remote = [_remote("important.txt")]
        stored = {"important.txt": _stored("important.txt")}

        actions = plan_initial_sync(local, remote, stored_entries=stored)

        assert len(actions) == 1
        assert actions[0].action == ActionType.DOWNLOAD

    def test_remote_only_no_stored_entry_generates_download(self):
        """New file on remote (never synced) still generates DOWNLOAD."""
        local = {"other.txt": LocalFileInfo(md5="x", mtime=100, size=10)}
        remote = [_remote("new_from_remote.txt")]

        actions = plan_initial_sync(local, remote, stored_entries={})

        types = {a.path: a.action for a in actions}
        assert types["new_from_remote.txt"] == ActionType.DOWNLOAD

    def test_stored_entry_without_remote_id_generates_download(self):
        """Stored entry without remote_id (never actually uploaded) → still DOWNLOAD."""
        local = {"other.txt": LocalFileInfo(md5="x", mtime=100, size=10)}
        remote = [_remote("partial.txt")]
        stored = {"partial.txt": SyncEntry(path="partial.txt", pair_id="p0", remote_id=None)}

        actions = plan_initial_sync(local, remote, stored_entries=stored)

        types = {a.path: a.action for a in actions}
        assert types["partial.txt"] == ActionType.DOWNLOAD

    def test_delete_remote_reason_string(self):
        """The generated DELETE_REMOTE action must carry an informative reason."""
        local = {"other.txt": LocalFileInfo(md5="x", mtime=100, size=10)}
        remote = [_remote("gone.txt")]
        stored = {"gone.txt": _stored("gone.txt")}

        actions = plan_initial_sync(local, remote, stored_entries=stored)

        delete_action = next(a for a in actions if a.path == "gone.txt")
        assert "deleted" in delete_action.reason.lower()

    def test_no_stored_entries_arg_preserves_existing_behaviour(self):
        """Calling plan_initial_sync without stored_entries keeps DOWNLOAD (backward compat)."""
        local = {"other.txt": LocalFileInfo(md5="x", mtime=100, size=10)}
        remote = [_remote("remote_only.txt")]

        actions = plan_initial_sync(local, remote)

        types = {a.path: a.action for a in actions}
        assert types["remote_only.txt"] == ActionType.DOWNLOAD
