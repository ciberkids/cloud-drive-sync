"""Tests for filter_actions_by_mode in the planner."""

from __future__ import annotations

from cloud_drive_sync.sync.planner import ActionType, SyncAction, filter_actions_by_mode


def _make_actions():
    """Create one action of each type."""
    return [
        SyncAction(ActionType.UPLOAD, "upload.txt"),
        SyncAction(ActionType.DOWNLOAD, "download.txt"),
        SyncAction(ActionType.DELETE_LOCAL, "del_local.txt"),
        SyncAction(ActionType.DELETE_REMOTE, "del_remote.txt"),
        SyncAction(ActionType.CONFLICT, "conflict.txt"),
        SyncAction(ActionType.NOOP, "noop.txt"),
    ]


class TestFilterActionsByMode:
    def test_two_way_keeps_all(self):
        actions = _make_actions()
        result = filter_actions_by_mode(actions, "two_way")
        assert len(result) == len(actions)

    def test_upload_only_drops_downloads_and_delete_local(self):
        actions = _make_actions()
        result = filter_actions_by_mode(actions, "upload_only")
        types = {a.action for a in result}
        assert ActionType.DOWNLOAD not in types
        assert ActionType.DELETE_LOCAL not in types
        assert ActionType.UPLOAD in types
        assert ActionType.DELETE_REMOTE in types
        assert ActionType.CONFLICT in types
        assert ActionType.NOOP in types

    def test_download_only_drops_uploads_and_delete_remote(self):
        actions = _make_actions()
        result = filter_actions_by_mode(actions, "download_only")
        types = {a.action for a in result}
        assert ActionType.UPLOAD not in types
        assert ActionType.DELETE_REMOTE not in types
        assert ActionType.DOWNLOAD in types
        assert ActionType.DELETE_LOCAL in types
        assert ActionType.CONFLICT in types
        assert ActionType.NOOP in types

    def test_unknown_mode_keeps_all(self):
        actions = _make_actions()
        result = filter_actions_by_mode(actions, "unknown_mode")
        assert len(result) == len(actions)

    def test_empty_actions(self):
        result = filter_actions_by_mode([], "upload_only")
        assert result == []

    def test_upload_only_with_only_downloads(self):
        actions = [
            SyncAction(ActionType.DOWNLOAD, "a.txt"),
            SyncAction(ActionType.DOWNLOAD, "b.txt"),
        ]
        result = filter_actions_by_mode(actions, "upload_only")
        assert result == []

    def test_download_only_with_only_uploads(self):
        actions = [
            SyncAction(ActionType.UPLOAD, "a.txt"),
            SyncAction(ActionType.UPLOAD, "b.txt"),
        ]
        result = filter_actions_by_mode(actions, "download_only")
        assert result == []

    def test_preserves_action_data(self):
        from cloud_drive_sync.local.scanner import LocalFileInfo

        info = LocalFileInfo(md5="abc", mtime=1000, size=100)
        actions = [
            SyncAction(ActionType.UPLOAD, "keep.txt", local_info=info, reason="test"),
        ]
        result = filter_actions_by_mode(actions, "two_way")
        assert result[0].local_info is info
        assert result[0].reason == "test"
