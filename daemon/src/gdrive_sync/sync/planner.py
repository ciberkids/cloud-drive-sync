"""Sync planning: diff local vs remote and produce action lists."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from pathlib import Path

from gdrive_sync.db.models import SyncEntry
from gdrive_sync.local.scanner import LocalFileInfo
from gdrive_sync.util.logging import get_logger

log = get_logger("sync.planner")

FOLDER_MIME = "application/vnd.google-apps.folder"

# Google-native document types that cannot be downloaded as binary files.
# Folders are NOT in this set — they must be synced as local directories.
_GOOGLE_NATIVE_SKIP_MIMES = frozenset(
    {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
        "application/vnd.google-apps.form",
        "application/vnd.google-apps.drawing",
        "application/vnd.google-apps.script",
        "application/vnd.google-apps.site",
        "application/vnd.google-apps.jam",
        "application/vnd.google-apps.map",
    }
)


def _is_google_native_doc(mime: str) -> bool:
    """Return True for Google-native document mimeTypes that cannot be downloaded."""
    return mime in _GOOGLE_NATIVE_SKIP_MIMES


def _is_folder(mime: str) -> bool:
    """Return True when the mimeType indicates a Google Drive folder."""
    return mime == FOLDER_MIME


class ActionType(enum.Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    DELETE_LOCAL = "delete_local"
    DELETE_REMOTE = "delete_remote"
    CONFLICT = "conflict"
    NOOP = "noop"
    MKDIR = "mkdir"


@dataclass
class SyncAction:
    """A planned sync action for a single file."""

    action: ActionType
    path: str
    local_info: LocalFileInfo | None = None
    remote_info: dict[str, Any] | None = None
    stored_entry: SyncEntry | None = None
    reason: str = ""


def _is_hidden(path: str) -> bool:
    """Check if any path component starts with a dot."""
    return any(part.startswith(".") for part in Path(path).parts)


def plan_initial_sync(
    local_files: dict[str, LocalFileInfo],
    remote_files: list[dict[str, Any]],
    ignore_hidden: bool = True,
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
        if ignore_hidden and _is_hidden(path):
            continue
        local = local_files.get(path)
        remote = remote_by_path.get(path)

        if local and not remote:
            actions.append(SyncAction(ActionType.UPLOAD, path, local_info=local, reason="local only"))
        elif remote and not local:
            mime = remote.get("mimeType", "")
            if _is_google_native_doc(mime):
                log.debug("Skipping Google-native file: %s", path)
                continue
            if _is_folder(mime):
                actions.append(
                    SyncAction(ActionType.MKDIR, path, remote_info=remote, reason="remote folder")
                )
            else:
                actions.append(
                    SyncAction(ActionType.DOWNLOAD, path, remote_info=remote, reason="remote only")
                )
        elif local and remote:
            mime = remote.get("mimeType", "")
            if _is_folder(mime):
                # Folder exists both locally and remotely — nothing to do
                actions.append(SyncAction(ActionType.NOOP, path, reason="folder in sync"))
                continue
            remote_md5 = remote.get("md5Checksum")
            if remote_md5 and local.md5 == remote_md5:
                actions.append(SyncAction(ActionType.NOOP, path, reason="already in sync"))
            elif remote_md5 is None:
                # Google native doc — skip
                if _is_google_native_doc(mime):
                    continue
                # Unknown type with no md5 — skip
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
        "Initial plan: %d uploads, %d downloads, %d mkdir, %d conflicts, %d noop",
        sum(1 for a in actions if a.action == ActionType.UPLOAD),
        sum(1 for a in actions if a.action == ActionType.DOWNLOAD),
        sum(1 for a in actions if a.action == ActionType.MKDIR),
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
        mime = change.get("mimeType", "")
        if not mime:
            # Also check inside remote_info for mimeType
            ri = change.get("remote_info")
            if ri:
                mime = ri.get("mimeType", "")
        is_dir = _is_folder(mime) or change.get("is_directory", False)

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
                if stored:
                    actions.append(
                        SyncAction(
                            ActionType.DELETE_LOCAL,
                            path,
                            stored_entry=stored,
                            reason="remote deletion",
                        )
                    )
                else:
                    log.debug("Ignoring remote deletion for untracked path: %s", path)
            continue

        # Handle directories / folders
        if is_dir:
            if source == "local":
                if stored is None:
                    actions.append(
                        SyncAction(
                            ActionType.MKDIR,
                            path,
                            reason="new local directory",
                        )
                    )
                else:
                    actions.append(SyncAction(ActionType.NOOP, path, reason="directory already tracked"))
            else:  # remote folder
                if stored is None:
                    actions.append(
                        SyncAction(
                            ActionType.MKDIR,
                            path,
                            remote_info=change.get("remote_info"),
                            reason="new remote folder",
                        )
                    )
                else:
                    actions.append(SyncAction(ActionType.NOOP, path, reason="folder already tracked"))
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
