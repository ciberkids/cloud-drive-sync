"""Conflict detection and resolution strategies."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path

from gdrive_sync.db.models import ConflictRecord, SyncEntry
from gdrive_sync.sync.planner import ActionType, SyncAction
from gdrive_sync.util.logging import get_logger

log = get_logger("sync.conflict")


def detect_conflict(
    local_md5: str | None,
    remote_md5: str | None,
    stored_entry: SyncEntry | None,
) -> bool:
    """Detect whether a file is in conflict using three-way comparison.

    A conflict exists when both local and remote have changed relative
    to the stored base state.
    """
    if stored_entry is None:
        # No base: conflict if both sides exist and differ
        return bool(local_md5 and remote_md5 and local_md5 != remote_md5)

    local_changed = local_md5 != stored_entry.local_md5
    remote_changed = remote_md5 != stored_entry.remote_md5
    return local_changed and remote_changed


def resolve_keep_both(local_path: Path) -> Path:
    """Create a conflict copy with a timestamp suffix.

    Renames the local file to include a conflict timestamp, freeing
    the original name for the remote version.

    Returns:
        Path to the renamed conflict copy.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = local_path.stem
    suffix = local_path.suffix
    conflict_name = f"{stem}_conflict_{ts}{suffix}"
    conflict_path = local_path.parent / conflict_name
    shutil.copy2(local_path, conflict_path)
    log.info("Keep-both: created conflict copy %s", conflict_path)
    return conflict_path


def resolve_newest_wins(
    local_mtime: float,
    remote_mtime: float,
) -> ActionType:
    """Pick the newer version based on modification times.

    Returns:
        UPLOAD if local is newer, DOWNLOAD if remote is newer.
    """
    if local_mtime >= remote_mtime:
        log.info("Newest-wins: local is newer (%.0f >= %.0f)", local_mtime, remote_mtime)
        return ActionType.UPLOAD
    log.info("Newest-wins: remote is newer (%.0f < %.0f)", local_mtime, remote_mtime)
    return ActionType.DOWNLOAD


async def resolve_ask_user(
    path: str,
    conflict: ConflictRecord,
    notify_callback=None,
) -> SyncAction | None:
    """Notify connected clients about a conflict and wait for a resolution.

    The IPC server sets a Future that will be resolved when the user
    picks a strategy via the resolve_conflict RPC method.

    Args:
        path: Relative path of the conflicted file.
        conflict: The conflict record from the database.
        notify_callback: Async callable to send an IPC notification.

    Returns:
        A SyncAction if the user resolved the conflict, or None if still pending.
    """
    if notify_callback:
        await notify_callback(
            "conflict_detected",
            {
                "id": conflict.id,
                "path": path,
                "local_md5": conflict.local_md5,
                "remote_md5": conflict.remote_md5,
            },
        )
    log.info("Conflict on %s deferred to user", path)
    return None


class ConflictResolver:
    """Coordinates conflict resolution using the configured strategy."""

    def __init__(self, strategy: str = "keep_both") -> None:
        self._strategy = strategy
        self._pending_resolutions: dict[int, asyncio.Future[str]] = {}

    @property
    def strategy(self) -> str:
        return self._strategy

    @strategy.setter
    def strategy(self, value: str) -> None:
        self._strategy = value

    async def resolve(
        self,
        path: str,
        local_path: Path,
        local_mtime: float,
        remote_mtime: float,
        conflict: ConflictRecord,
        notify_callback=None,
    ) -> SyncAction | None:
        """Apply the configured strategy to a conflict.

        Returns a SyncAction or None if waiting for user input.
        """
        if self._strategy == "keep_both":
            resolve_keep_both(local_path)
            return SyncAction(
                action=ActionType.DOWNLOAD,
                path=path,
                reason="keep_both: downloading remote to original name",
            )
        elif self._strategy == "newest_wins":
            action_type = resolve_newest_wins(local_mtime, remote_mtime)
            return SyncAction(action=action_type, path=path, reason="newest_wins")
        elif self._strategy == "ask_user":
            return await resolve_ask_user(path, conflict, notify_callback)
        else:
            log.error("Unknown conflict strategy: %s", self._strategy)
            return None

    def set_user_resolution(self, conflict_id: int, resolution: str) -> None:
        """Called when the user resolves a conflict via IPC."""
        future = self._pending_resolutions.get(conflict_id)
        if future and not future.done():
            future.set_result(resolution)
