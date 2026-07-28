"""Tests for the sync planner."""

from __future__ import annotations

from cloud_drive_sync.db.models import FileState, SyncEntry
from cloud_drive_sync.local.scanner import LocalFileInfo
from cloud_drive_sync.sync.planner import ActionType, plan_continuous_sync, plan_initial_sync


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

    def test_remote_deletion_untracked_ignored(self):
        """Remote deletion of a file we don't track should NOT delete locally."""
        changes = [{"path": "House", "source": "remote", "deleted": True}]
        actions = plan_continuous_sync(changes, {})
        assert len(actions) == 0

    def test_remote_deletion_untracked_with_other_tracked(self):
        """Untracked remote deletion ignored while tracked entries still work."""
        stored = {
            "tracked.txt": SyncEntry(
                path="tracked.txt", pair_id="p0", state=FileState.SYNCED,
                local_md5="aaa", remote_md5="aaa", remote_id="rid1",
            )
        }
        changes = [
            {"path": "unrelated_folder", "source": "remote", "deleted": True},
            {"path": "tracked.txt", "source": "remote", "deleted": True},
        ]
        actions = plan_continuous_sync(changes, stored)
        assert len(actions) == 1
        assert actions[0].action == ActionType.DELETE_LOCAL
        assert actions[0].path == "tracked.txt"


class TestApplyStrategyOverrides:
    """Tests for apply_strategy_overrides."""

    def _action(self, action_type, path="file.txt", **kwargs):
        from cloud_drive_sync.sync.planner import SyncAction
        return SyncAction(action=action_type, path=path, **kwargs)

    def test_passthrough_for_unknown_strategy(self):
        from cloud_drive_sync.sync.planner import apply_strategy_overrides
        actions = [self._action(ActionType.UPLOAD), self._action(ActionType.DOWNLOAD)]
        assert apply_strategy_overrides(actions, "keep_both") is actions
        assert apply_strategy_overrides(actions, "newest_wins") is actions
        assert apply_strategy_overrides(actions, "") is actions

    # ── local_wins ───────────────────────────────────────────────────

    def test_local_wins_conflict_becomes_upload(self):
        from cloud_drive_sync.sync.planner import apply_strategy_overrides
        result = apply_strategy_overrides([self._action(ActionType.CONFLICT)], "local_wins")
        assert len(result) == 1
        assert result[0].action == ActionType.UPLOAD

    def test_local_wins_download_becomes_delete_remote(self):
        from cloud_drive_sync.sync.planner import apply_strategy_overrides
        result = apply_strategy_overrides([self._action(ActionType.DOWNLOAD)], "local_wins")
        assert result[0].action == ActionType.DELETE_REMOTE

    def test_local_wins_delete_local_becomes_upload(self):
        from cloud_drive_sync.sync.planner import apply_strategy_overrides
        result = apply_strategy_overrides([self._action(ActionType.DELETE_LOCAL)], "local_wins")
        assert result[0].action == ActionType.UPLOAD

    def test_local_wins_preserves_upload_and_delete_remote(self):
        from cloud_drive_sync.sync.planner import apply_strategy_overrides
        actions = [self._action(ActionType.UPLOAD), self._action(ActionType.DELETE_REMOTE)]
        result = apply_strategy_overrides(actions, "local_wins")
        assert result[0].action == ActionType.UPLOAD
        assert result[1].action == ActionType.DELETE_REMOTE

    # ── remote_wins ──────────────────────────────────────────────────

    def test_remote_wins_conflict_becomes_download(self):
        from cloud_drive_sync.sync.planner import apply_strategy_overrides
        result = apply_strategy_overrides([self._action(ActionType.CONFLICT)], "remote_wins")
        assert result[0].action == ActionType.DOWNLOAD

    def test_remote_wins_upload_becomes_delete_local(self):
        from cloud_drive_sync.sync.planner import apply_strategy_overrides
        result = apply_strategy_overrides([self._action(ActionType.UPLOAD)], "remote_wins")
        assert result[0].action == ActionType.DELETE_LOCAL

    def test_remote_wins_delete_remote_becomes_download(self):
        from cloud_drive_sync.sync.planner import apply_strategy_overrides
        result = apply_strategy_overrides([self._action(ActionType.DELETE_REMOTE)], "remote_wins")
        assert result[0].action == ActionType.DOWNLOAD

    def test_remote_wins_move_expands_to_delete_local_and_download(self):
        from cloud_drive_sync.sync.planner import SyncAction, apply_strategy_overrides
        move = SyncAction(action=ActionType.MOVE, path="old.txt", dest_path="new.txt")
        result = apply_strategy_overrides([move], "remote_wins")
        assert len(result) == 2
        actions_out = {a.action for a in result}
        assert ActionType.DELETE_LOCAL in actions_out
        assert ActionType.DOWNLOAD in actions_out
        delete_action = next(a for a in result if a.action == ActionType.DELETE_LOCAL)
        download_action = next(a for a in result if a.action == ActionType.DOWNLOAD)
        assert delete_action.path == "new.txt"
        assert download_action.path == "old.txt"

    def test_remote_wins_preserves_download_and_delete_local(self):
        from cloud_drive_sync.sync.planner import apply_strategy_overrides
        actions = [self._action(ActionType.DOWNLOAD), self._action(ActionType.DELETE_LOCAL)]
        result = apply_strategy_overrides(actions, "remote_wins")
        assert result[0].action == ActionType.DOWNLOAD
        assert result[1].action == ActionType.DELETE_LOCAL
