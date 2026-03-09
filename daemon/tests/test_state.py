"""Tests for file state transitions."""

from __future__ import annotations

import pytest

from gdrive_sync.db.models import FileState
from gdrive_sync.sync.state import VALID_TRANSITIONS, can_transition, transition


class TestCanTransition:
    def test_unknown_to_synced(self):
        assert can_transition(FileState.UNKNOWN, FileState.SYNCED) is True

    def test_unknown_to_pending_upload(self):
        assert can_transition(FileState.UNKNOWN, FileState.PENDING_UPLOAD) is True

    def test_unknown_to_pending_download(self):
        assert can_transition(FileState.UNKNOWN, FileState.PENDING_DOWNLOAD) is True

    def test_unknown_to_conflict(self):
        assert can_transition(FileState.UNKNOWN, FileState.CONFLICT) is True

    def test_unknown_to_uploading_invalid(self):
        assert can_transition(FileState.UNKNOWN, FileState.UPLOADING) is False

    def test_unknown_to_error_invalid(self):
        assert can_transition(FileState.UNKNOWN, FileState.ERROR) is False

    def test_synced_to_pending_upload(self):
        assert can_transition(FileState.SYNCED, FileState.PENDING_UPLOAD) is True

    def test_synced_to_pending_download(self):
        assert can_transition(FileState.SYNCED, FileState.PENDING_DOWNLOAD) is True

    def test_synced_to_conflict(self):
        assert can_transition(FileState.SYNCED, FileState.CONFLICT) is True

    def test_synced_to_uploading_invalid(self):
        assert can_transition(FileState.SYNCED, FileState.UPLOADING) is False

    def test_pending_upload_to_uploading(self):
        assert can_transition(FileState.PENDING_UPLOAD, FileState.UPLOADING) is True

    def test_pending_upload_to_error(self):
        assert can_transition(FileState.PENDING_UPLOAD, FileState.ERROR) is True

    def test_pending_upload_to_synced(self):
        assert can_transition(FileState.PENDING_UPLOAD, FileState.SYNCED) is True

    def test_uploading_to_synced(self):
        assert can_transition(FileState.UPLOADING, FileState.SYNCED) is True

    def test_uploading_to_error(self):
        assert can_transition(FileState.UPLOADING, FileState.ERROR) is True

    def test_downloading_to_synced(self):
        assert can_transition(FileState.DOWNLOADING, FileState.SYNCED) is True

    def test_conflict_to_pending_upload(self):
        assert can_transition(FileState.CONFLICT, FileState.PENDING_UPLOAD) is True

    def test_conflict_to_pending_download(self):
        assert can_transition(FileState.CONFLICT, FileState.PENDING_DOWNLOAD) is True

    def test_conflict_to_synced(self):
        assert can_transition(FileState.CONFLICT, FileState.SYNCED) is True

    def test_error_to_pending_upload(self):
        assert can_transition(FileState.ERROR, FileState.PENDING_UPLOAD) is True

    def test_error_to_unknown(self):
        assert can_transition(FileState.ERROR, FileState.UNKNOWN) is True

    def test_self_transition_is_invalid(self):
        for state in FileState:
            assert can_transition(state, state) is False


class TestTransition:
    def test_valid_transition_returns_target(self):
        result = transition(FileState.UNKNOWN, FileState.SYNCED)
        assert result == FileState.SYNCED

    def test_invalid_transition_raises(self):
        with pytest.raises(ValueError, match="Invalid state transition"):
            transition(FileState.UNKNOWN, FileState.UPLOADING)

    def test_all_valid_transitions_work(self):
        for current, targets in VALID_TRANSITIONS.items():
            for target in targets:
                result = transition(current, target)
                assert result == target


class TestValidTransitionsCompleteness:
    def test_all_states_have_transitions(self):
        for state in FileState:
            assert state in VALID_TRANSITIONS

    def test_no_self_loops(self):
        for state, targets in VALID_TRANSITIONS.items():
            assert state not in targets
