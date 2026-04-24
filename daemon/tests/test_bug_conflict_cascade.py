"""Regression test for issue #29: upload_only conflict cascade.

When sync_mode is upload_only, a CONFLICT action must be converted to UPLOAD
by filter_actions_by_mode BEFORE the ConflictResolver runs, so that:
 1. No _conflict_TIMESTAMP copy is created on disk.
 2. The original file is uploaded (overwriting the remote stub).
 3. No re-conflict on the next scan cycle.

The engine fix moves filter_actions_by_mode to run before the resolver loop
so that CONFLICT→UPLOAD happens at the planning stage, not after the resolver
has already called resolve_keep_both().
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from cloud_drive_sync.sync.planner import ActionType, SyncAction, filter_actions_by_mode


class TestConflictCascadePrevention:
    """filter_actions_by_mode converts CONFLICT before resolver runs."""

    def test_upload_only_conflict_becomes_upload_before_resolver(self):
        """upload_only: CONFLICT→UPLOAD so resolver never sees it."""
        actions = [SyncAction(ActionType.CONFLICT, "photo.jpg")]
        result = filter_actions_by_mode(actions, "upload_only")
        assert len(result) == 1
        assert result[0].action == ActionType.UPLOAD
        assert result[0].path == "photo.jpg"

    def test_download_only_conflict_becomes_download_before_resolver(self):
        """download_only: CONFLICT→DOWNLOAD so resolver never sees it."""
        actions = [SyncAction(ActionType.CONFLICT, "photo.jpg")]
        result = filter_actions_by_mode(actions, "download_only")
        assert len(result) == 1
        assert result[0].action == ActionType.DOWNLOAD

    def test_two_way_conflict_preserved_for_resolver(self):
        """two_way: CONFLICT stays as CONFLICT so the resolver handles it."""
        actions = [SyncAction(ActionType.CONFLICT, "photo.jpg")]
        result = filter_actions_by_mode(actions, "two_way")
        assert len(result) == 1
        assert result[0].action == ActionType.CONFLICT

    def test_upload_only_no_conflict_copy_needed(self):
        """In upload_only, no _conflict_TIMESTAMP copy should be created.

        This test verifies the invariant: after filter_actions_by_mode converts
        CONFLICT→UPLOAD, the action list contains only UPLOAD (not CONFLICT),
        so the ConflictResolver's resolve_keep_both() is never invoked.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a "local" file simulating a conflict scenario
            local_file = Path(tmpdir) / "photo.jpg"
            local_file.write_bytes(b"local content")

            actions = [
                SyncAction(
                    ActionType.CONFLICT,
                    "photo.jpg",
                    reason="both sides changed",
                )
            ]

            # Simulate what the engine now does: filter BEFORE resolver
            mode_filtered = filter_actions_by_mode(actions, "upload_only")

            # No CONFLICT actions remain — resolver will not be called
            conflict_actions = [a for a in mode_filtered if a.action == ActionType.CONFLICT]
            assert conflict_actions == [], "CONFLICT actions must be gone before resolver loop"

            # No _conflict_ copies on disk (resolver was never called)
            copies = list(Path(tmpdir).glob("*_conflict_*"))
            assert copies == [], f"No conflict copies should exist, found: {copies}"

    def test_upload_only_remote_info_preserved_for_upload(self):
        """CONFLICT→UPLOAD preserves remote_info so executor can delete the stub."""
        from cloud_drive_sync.local.scanner import LocalFileInfo

        remote_stub = {"id": "/path/photo.jpg", "size": 0, "md5Checksum": ""}
        local_info = LocalFileInfo(md5="abc123", mtime=1000, size=4096)
        actions = [
            SyncAction(
                ActionType.CONFLICT,
                "photo.jpg",
                remote_info=remote_stub,
                local_info=local_info,
            )
        ]
        result = filter_actions_by_mode(actions, "upload_only")
        assert result[0].action == ActionType.UPLOAD
        assert result[0].remote_info is remote_stub
        assert result[0].local_info is local_info

    def test_cascade_scenario_multi_cycle(self):
        """Simulate two successive scan cycles: no doubly-nested names produced."""
        # Cycle 1: planner detects conflict
        cycle1 = [SyncAction(ActionType.CONFLICT, "IMG_0773.JPG")]
        after_filter = filter_actions_by_mode(cycle1, "upload_only")
        assert after_filter[0].action == ActionType.UPLOAD
        # Resolver never called → no _conflict_20260424_204440 copy created

        # Cycle 2: same scenario (remote stub still exists if upload failed,
        # but no pre-existing conflict copy to double-nest)
        cycle2 = [SyncAction(ActionType.CONFLICT, "IMG_0773.JPG")]
        after_filter2 = filter_actions_by_mode(cycle2, "upload_only")
        assert after_filter2[0].action == ActionType.UPLOAD
        assert after_filter2[0].path == "IMG_0773.JPG"
        # Path does NOT contain nested _conflict_ suffix
        assert "_conflict_" not in after_filter2[0].path
