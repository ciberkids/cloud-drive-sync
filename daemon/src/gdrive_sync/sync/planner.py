"""Sync planning: diff local vs remote and produce action lists."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from gdrive_sync.db.models import FileState, SyncEntry
from gdrive_sync.local.scanner import LocalFileInfo
from gdrive_sync.util.logging import get_logger

log = get_logger("sync.planner")


class ActionType(enum.Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    DELETE_LOCAL = "delete_local"
    DELETE_REMOTE = "delete_remote"
    CONFLICT = "conflict"
    NOOP = "noop"


@dataclass
class SyncAction:
    """A planned sync action for a single file."""

    action: ActionType
    path: str
    local_info: LocalFileInfo | None = None
    remote_info: dict[str, Any] | None = None
    stored_entry: SyncEntry | None = None
    reason: str = ""


def plan_initial_sync(
    local_files: dict[str, LocalFileInfo],
    remote_files: list[dict[str, Any]],
) -> list[SyncAction]:
    """Plan actions for the very first sync (no stored state).

    Strategy:
    - File only local -> upload
    - File only remote -> download
    - File both sides, same md5 -> synced (noop)
    - File both sides, different md5 -> conflict
    """
    actions: list[SyncAction] = []
    remote_by_path: dict[str, dict[str, Any]] = {}
    for rf in remote_files:
        rp = rf.get("relativePath", rf.get("name", ""))
        remote_by_path[rp] = rf

    all_paths = set(local_files.keys()) | set(remote_by_path.keys())

    for path in sorted(all_paths):
        local = local_files.get(path)
        remote = remote_by_path.get(path)

        if local and not remote:
            actions.append(SyncAction(ActionType.UPLOAD, path, local_info=local, reason="local only"))
        elif remote and not local:
            # Skip Google Docs native types for download (they have no md5)
            mime = remote.get("mimeType", "")
            if mime.startswith("application/vnd.google-apps."):
                log.debug("Skipping Google-native file: %s", path)
                continue
            actions.append(
                SyncAction(ActionType.DOWNLOAD, path, remote_info=remote, reason="remote only")
            )
        elif local and remote:
            remote_md5 = remote.get("md5Checksum")
            if remote_md5 and local.md5 == remote_md5:
                actions.append(SyncAction(ActionType.NOOP, path, reason="already in sync"))
            elif remote_md5 is None:
                # Google native doc — skip
                continue
            else:
                actions.append(
                    SyncAction(
                        ActionType.CONFLICT,
                        path,
                        local_info=local,
                        remote_info=remote,
                        reason="initial sync md5 mismatch",
                    )
                )

    log.info(
        "Initial plan: %d uploads, %d downloads, %d conflicts, %d noop",
        sum(1 for a in actions if a.action == ActionType.UPLOAD),
        sum(1 for a in actions if a.action == ActionType.DOWNLOAD),
        sum(1 for a in actions if a.action == ActionType.CONFLICT),
        sum(1 for a in actions if a.action == ActionType.NOOP),
    )
    return actions


def filter_actions_by_mode(
    actions: list[SyncAction],
    sync_mode: str,
) -> list[SyncAction]:
    """Remove actions not allowed by the sync mode.

    Modes:
    - ``two_way`` – keep everything (default)
    - ``upload_only`` – drop DOWNLOAD and DELETE_LOCAL
    - ``download_only`` – drop UPLOAD and DELETE_REMOTE
    """
    if sync_mode == "two_way":
        return actions

    blocked: set[ActionType]
    if sync_mode == "upload_only":
        blocked = {ActionType.DOWNLOAD, ActionType.DELETE_LOCAL}
    elif sync_mode == "download_only":
        blocked = {ActionType.UPLOAD, ActionType.DELETE_REMOTE}
    else:
        return actions

    filtered = [a for a in actions if a.action not in blocked]
    dropped = len(actions) - len(filtered)
    if dropped:
        log.info("Sync mode %s: dropped %d actions", sync_mode, dropped)
    return filtered


def plan_continuous_sync(
    changes: list[dict[str, Any]],
    stored_entries: dict[str, SyncEntry],
) -> list[SyncAction]:
    """Plan actions for incremental sync based on detected changes.

    Uses three-way diff: compare local state, remote state, and the stored
    base state to determine whether to upload, download, or flag a conflict.

    Each change dict must have:
        - 'path': relative path
        - 'source': 'local' or 'remote'
        - 'md5': current md5 (or None if deleted)
        - 'mtime': modification time
        - 'deleted': bool
    And optionally for remote:
        - 'remote_id': str
        - 'remote_info': full remote metadata dict
    """
    actions: list[SyncAction] = []

    for change in changes:
        path = change["path"]
        source = change["source"]
        stored = stored_entries.get(path)

        if change.get("deleted"):
            if source == "local":
                if stored and stored.remote_id:
                    actions.append(
                        SyncAction(
                            ActionType.DELETE_REMOTE,
                            path,
                            stored_entry=stored,
                            reason="local deletion",
                        )
                    )
            else:  # remote deletion
                actions.append(
                    SyncAction(
                        ActionType.DELETE_LOCAL,
                        path,
                        stored_entry=stored,
                        reason="remote deletion",
                    )
                )
            continue

        new_md5 = change.get("md5")

        if source == "local":
            if stored is None:
                # New local file
                actions.append(
                    SyncAction(
                        ActionType.UPLOAD,
                        path,
                        local_info=LocalFileInfo(
                            md5=new_md5 or "",
                            mtime=change.get("mtime", 0),
                            size=change.get("size", 0),
                        ),
                        reason="new local file",
                    )
                )
            elif stored.remote_md5 and new_md5 != stored.local_md5:
                # Local modified — check if remote also changed
                if stored.remote_md5 != stored.local_md5:
                    # Remote was already different from stored base → conflict
                    actions.append(
                        SyncAction(ActionType.CONFLICT, path, stored_entry=stored, reason="both sides changed")
                    )
                else:
                    actions.append(
                        SyncAction(
                            ActionType.UPLOAD,
                            path,
                            local_info=LocalFileInfo(
                                md5=new_md5 or "",
                                mtime=change.get("mtime", 0),
                                size=change.get("size", 0),
                            ),
                            stored_entry=stored,
                            reason="local modified",
                        )
                    )
            else:
                actions.append(SyncAction(ActionType.NOOP, path, reason="no effective change"))

        else:  # remote
            if stored is None:
                actions.append(
                    SyncAction(
                        ActionType.DOWNLOAD,
                        path,
                        remote_info=change.get("remote_info"),
                        reason="new remote file",
                    )
                )
            elif new_md5 and new_md5 != stored.remote_md5:
                if stored.local_md5 != stored.remote_md5:
                    actions.append(
                        SyncAction(ActionType.CONFLICT, path, stored_entry=stored, reason="both sides changed")
                    )
                else:
                    actions.append(
                        SyncAction(
                            ActionType.DOWNLOAD,
                            path,
                            remote_info=change.get("remote_info"),
                            stored_entry=stored,
                            reason="remote modified",
                        )
                    )
            else:
                actions.append(SyncAction(ActionType.NOOP, path, reason="no effective change"))

    return actions
