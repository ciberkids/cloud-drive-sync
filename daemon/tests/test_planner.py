"""Tests for the sync planner."""

from __future__ import annotations

import pytest

from gdrive_sync.db.models import FileState, SyncEntry
from gdrive_sync.local.scanner import LocalFileInfo
from gdrive_sync.sync.planner import ActionType, plan_continuous_sync, plan_initial_sync


class TestInitialSync:
    def test_local_only_files_upload(self):
        local = {"file.txt": LocalFileInfo(md5="aaa", mtime=1000, size=100)}
        remote: list = []
        actions = plan_initial_sync(local, remote)
        assert len(actions) == 1
        assert actions[0].action == ActionType.UPLOAD
        assert actions[0].path == "file.txt"

    def test_remote_only_files_download(self):
        local: dict = {}
        remote = [{"name": "doc.pdf", "relativePath": "doc.pdf", "md5Checksum": "bbb", "mimeType": "application/pdf"}]
        actions = plan_initial_sync(local, remote)
        assert len(actions) == 1
        assert actions[0].action == ActionType.DOWNLOAD

    def test_matching_files_noop(self):
        local = {"file.txt": LocalFileInfo(md5="same", mtime=1000, size=100)}
        remote = [{"name": "file.txt", "relativePath": "file.txt", "md5Checksum": "same", "mimeType": "text/plain"}]
        actions = plan_initial_sync(local, remote)
        assert len(actions) == 1
        assert actions[0].action == ActionType.NOOP

    def test_different_md5_conflict(self):
        local = {"file.txt": LocalFileInfo(md5="aaa", mtime=1000, size=100)}
        remote = [{"name": "file.txt", "relativePath": "file.txt", "md5Checksum": "bbb", "mimeType": "text/plain"}]
        actions = plan_initial_sync(local, remote)
        assert len(actions) == 1
        assert actions[0].action == ActionType.CONFLICT

    def test_google_docs_skipped(self):
        local: dict = {}
        remote = [
            {
                "name": "My Doc",
                "relativePath": "My Doc",
                "mimeType": "application/vnd.google-apps.document",
            }
        ]
        actions = plan_initial_sync(local, remote)
        assert len(actions) == 0

    def test_mixed_scenario(self):
        local = {
            "only_local.txt": LocalFileInfo(md5="l1", mtime=1000, size=10),
            "both.txt": LocalFileInfo(md5="same", mtime=1000, size=10),
        }
        remote = [
            {"name": "only_remote.txt", "relativePath": "only_remote.txt", "md5Checksum": "r1", "mimeType": "text/plain"},
            {"name": "both.txt", "relativePath": "both.txt", "md5Checksum": "same", "mimeType": "text/plain"},
        ]
        actions = plan_initial_sync(local, remote)
        types = {a.path: a.action for a in actions}
        assert types["only_local.txt"] == ActionType.UPLOAD
        assert types["only_remote.txt"] == ActionType.DOWNLOAD
        assert types["both.txt"] == ActionType.NOOP


class TestContinuousSync:
    def test_new_local_file_upload(self):
        changes = [{"path": "new.txt", "source": "local", "deleted": False, "md5": "aaa", "mtime": 100}]
        actions = plan_continuous_sync(changes, {})
        assert len(actions) == 1
        assert actions[0].action == ActionType.UPLOAD

    def test_local_deletion_deletes_remote(self):
        stored = {
            "del.txt": SyncEntry(
                path="del.txt", pair_id="p0", state=FileState.SYNCED,
                local_md5="aaa", remote_md5="aaa", remote_id="rid1",
            )
        }
        changes = [{"path": "del.txt", "source": "local", "deleted": True}]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.DELETE_REMOTE

    def test_remote_deletion_deletes_local(self):
        stored = {
            "del.txt": SyncEntry(
                path="del.txt", pair_id="p0", state=FileState.SYNCED,
                local_md5="aaa", remote_md5="aaa",
            )
        }
        changes = [{"path": "del.txt", "source": "remote", "deleted": True}]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.DELETE_LOCAL

    def test_local_modify_uploads(self):
        stored = {
            "file.txt": SyncEntry(
                path="file.txt", pair_id="p0", state=FileState.SYNCED,
                local_md5="old", remote_md5="old", remote_id="rid1",
            )
        }
        changes = [{"path": "file.txt", "source": "local", "deleted": False, "md5": "new", "mtime": 200}]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.UPLOAD

    def test_remote_modify_downloads(self):
        stored = {
            "file.txt": SyncEntry(
                path="file.txt", pair_id="p0", state=FileState.SYNCED,
                local_md5="old", remote_md5="old", remote_id="rid1",
            )
        }
        changes = [{"path": "file.txt", "source": "remote", "deleted": False, "md5": "new", "mtime": 200, "remote_info": {"id": "rid1"}}]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.DOWNLOAD

    def test_both_modified_conflict(self):
        stored = {
            "file.txt": SyncEntry(
                path="file.txt", pair_id="p0", state=FileState.SYNCED,
                local_md5="base", remote_md5="remote_changed", remote_id="rid1",
            )
        }
        changes = [{"path": "file.txt", "source": "local", "deleted": False, "md5": "local_changed", "mtime": 200}]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.CONFLICT

    def test_new_remote_file_download(self):
        changes = [
            {
                "path": "new_remote.txt",
                "source": "remote",
                "deleted": False,
                "md5": "rrr",
                "mtime": 100,
                "remote_info": {"id": "rid_new"},
            }
        ]
        actions = plan_continuous_sync(changes, {})
        assert len(actions) == 1
        assert actions[0].action == ActionType.DOWNLOAD
