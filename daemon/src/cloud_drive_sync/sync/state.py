"""File state enum and valid state transitions."""

from __future__ import annotations

from cloud_drive_sync.db.models import FileState

# Map of current state -> set of valid next states
VALID_TRANSITIONS: dict[FileState, set[FileState]] = {
    FileState.UNKNOWN: {
        FileState.SYNCED,
        FileState.PENDING_UPLOAD,
        FileState.PENDING_DOWNLOAD,
        FileState.CONFLICT,
    },
    FileState.SYNCED: {
        FileState.PENDING_UPLOAD,
        FileState.PENDING_DOWNLOAD,
        FileState.CONFLICT,
    },
    FileState.PENDING_UPLOAD: {
        FileState.UPLOADING,
        FileState.CONFLICT,
        FileState.ERROR,
        FileState.SYNCED,
    },
    FileState.PENDING_DOWNLOAD: {
        FileState.DOWNLOADING,
        FileState.CONFLICT,
        FileState.ERROR,
        FileState.SYNCED,
    },
    FileState.UPLOADING: {
        FileState.SYNCED,
        FileState.ERROR,
        FileState.CONFLICT,
        FileState.PENDING_UPLOAD,
    },
    FileState.DOWNLOADING: {
        FileState.SYNCED,
        FileState.ERROR,
        FileState.CONFLICT,
        FileState.PENDING_DOWNLOAD,
    },
    FileState.CONFLICT: {
        FileState.PENDING_UPLOAD,
        FileState.PENDING_DOWNLOAD,
        FileState.SYNCED,
    },
    FileState.ERROR: {
        FileState.PENDING_UPLOAD,
        FileState.PENDING_DOWNLOAD,
        FileState.SYNCED,
        FileState.UNKNOWN,
    },
}


def can_transition(current: FileState, target: FileState) -> bool:
    """Check whether transitioning from current to target state is valid."""
    return target in VALID_TRANSITIONS.get(current, set())


def transition(current: FileState, target: FileState) -> FileState:
    """Attempt a state transition; raise ValueError if invalid."""
    if not can_transition(current, target):
        msg = f"Invalid state transition: {current.value} -> {target.value}"
        raise ValueError(msg)
    return target
