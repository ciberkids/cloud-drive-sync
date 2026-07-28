"""Sync planning: diff local vs remote and produce action lists."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cloud_drive_sync.db.models import SyncEntry
from cloud_drive_sync.local.scanner import LocalFileInfo
from cloud_drive_sync.util.logging import get_logger

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


def _is_native_doc(mime: str, native_mimes: frozenset[str] | None = None) -> bool:
    """Return True for native document mimeTypes that cannot be downloaded.

    Args:
        mime: The MIME type to check.
        native_mimes: Optional provider-specific set. Falls back to Google-native set.
    """
    mimes = native_mimes if native_mimes is not None else _GOOGLE_NATIVE_SKIP_MIMES
    return mime in mimes


# Keep backward-compatible alias
def _is_google_native_doc(mime: str) -> bool:
    """Return True for Google-native document mimeTypes that cannot be downloaded."""
    return _is_native_doc(mime, _GOOGLE_NATIVE_SKIP_MIMES)


def _is_folder(file_info_or_mime: Any, folder_mime: str | None = None) -> bool:
    """Return True when the item represents a folder.

    Supports both:
    - A MIME string (legacy): checks against folder_mime
    - A dict with 'is_folder' key (provider-normalized)
    """
    if isinstance(file_info_or_mime, dict):
        if file_info_or_mime.get("is_folder"):
            return True
        mime = file_info_or_mime.get("mimeType", "")
    else:
        mime = file_info_or_mime
    fm = folder_mime if folder_mime is not None else FOLDER_MIME
    return fm is not None and mime == fm


class ActionType(enum.Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    DELETE_LOCAL = "delete_local"
    DELETE_REMOTE = "delete_remote"
    CONFLICT = "conflict"
    NOOP = "noop"
    MKDIR = "mkdir"
    MOVE = "move"


@dataclass
class SyncAction:
    """A planned sync action for a single file."""

    action: ActionType
    path: str
    local_info: LocalFileInfo | None = None
    remote_info: dict[str, Any] | None = None
    stored_entry: SyncEntry | None = None
    reason: str = ""
    dest_path: str | None = None


def detect_moves(actions: list[SyncAction]) -> list[SyncAction]:
    """Match DELETE_REMOTE + UPLOAD pairs by md5 hash and replace with MOVE actions.

    When a file is moved or renamed locally, the planner sees a DELETE_REMOTE for
    the old path and an UPLOAD for the new path.  If both refer to the same content
    (same md5 hash) we can collapse them into a single MOVE action, which is far
    cheaper than a delete + re-upload round-trip.

    Returns a new list with matched pairs replaced by MOVE actions.
    """
    # Index DELETE_REMOTE actions by the md5 from their stored entry
    delete_by_md5: dict[str, list[SyncAction]] = {}
    for a in actions:
        if a.action == ActionType.DELETE_REMOTE and a.stored_entry:
            md5 = a.stored_entry.local_md5 or a.stored_entry.remote_md5
            if md5:
                delete_by_md5.setdefault(md5, []).append(a)

    if not delete_by_md5:
        return actions

    matched_deletes: set[str] = set()   # paths of matched DELETE_REMOTE
    matched_uploads: set[str] = set()   # paths of matched UPLOAD
    move_actions: list[SyncAction] = []

    for a in actions:
        if a.action == ActionType.UPLOAD and a.local_info:
            md5 = a.local_info.md5
            if md5 and md5 in delete_by_md5:
                candidates = delete_by_md5[md5]
                # Pick the first unmatched delete
                for d in candidates:
                    if d.path not in matched_deletes:
                        matched_deletes.add(d.path)
                        matched_uploads.add(a.path)
                        move_actions.append(
                            SyncAction(
                                action=ActionType.MOVE,
                                path=d.path,
                                dest_path=a.path,
                                local_info=a.local_info,
                                stored_entry=d.stored_entry,
                                reason=f"move detected (md5={md5[:8]}...)",
                            )
                        )
                        break

    if not move_actions:
        return actions

    # Rebuild the action list: keep unmatched actions, insert moves
    result: list[SyncAction] = []
    for a in actions:
        if a.action == ActionType.DELETE_REMOTE and a.path in matched_deletes:
            continue
        if a.action == ActionType.UPLOAD and a.path in matched_uploads:
            continue
        result.append(a)
    result.extend(move_actions)

    log.info(
        "Move detection: %d move(s) detected from %d delete+upload pairs",
        len(move_actions),
        len(move_actions),
    )
    return result


def _is_hidden(path: str) -> bool:
    """Check if any path component starts with a dot."""
    return any(part.startswith(".") for part in Path(path).parts)


def plan_initial_sync(
    local_files: dict[str, LocalFileInfo],
    remote_files: list[dict[str, Any]],
    ignore_hidden: bool = True,
    *,
    native_doc_mimes: frozenset[str] | None = None,
    folder_mime: str | None = None,
    convert_native_docs: bool = False,
    stored_entries: dict[str, SyncEntry] | None = None,
) -> list[SyncAction]:
    """Plan actions for the very first sync (no stored state).

    Strategy:
    - File only local -> upload
    - File only remote -> download
    - File both sides, same hash -> synced (noop)
    - File both sides, different hash -> conflict

    When stored_entries is provided, remote-only files that have a stored entry
    (i.e. were previously synced) and whose local side is genuinely absent (not
    just a detached mount — guarded by local_files being non-empty) are treated
    as locally deleted and generate DELETE_REMOTE instead of DOWNLOAD.

    Args:
        native_doc_mimes: Provider-specific native doc MIME set. Defaults to Google set.
        folder_mime: Provider-specific folder MIME type. Defaults to Google folder MIME.
        convert_native_docs: If True, generate DOWNLOAD for exportable native docs
            instead of skipping them.
        stored_entries: Previously persisted sync state keyed by path.
    """
    skip_mimes = native_doc_mimes if native_doc_mimes is not None else _GOOGLE_NATIVE_SKIP_MIMES
    fm = folder_mime if folder_mime is not None else FOLDER_MIME

    # If converting native docs, separate exportable from non-exportable
    exportable_mimes: frozenset[str] = frozenset()
    if convert_native_docs:
        from cloud_drive_sync.providers.gdrive.conversion import EXPORTABLE_MIMES
        exportable_mimes = EXPORTABLE_MIMES
        # Only skip non-exportable native docs
        skip_mimes = skip_mimes - exportable_mimes

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
            if local.is_dir:
                actions.append(SyncAction(ActionType.MKDIR, path, local_info=local, reason="local directory"))
            else:
                actions.append(SyncAction(ActionType.UPLOAD, path, local_info=local, reason="local only"))
        elif remote and not local:
            mime = remote.get("mimeType", "")
            if _is_native_doc(mime, skip_mimes):
                log.debug("Skipping native file: %s", path)
                continue
            if _is_folder(mime, fm):
                actions.append(
                    SyncAction(ActionType.MKDIR, path, remote_info=remote, reason="remote folder")
                )
            elif convert_native_docs and mime in exportable_mimes:
                actions.append(
                    SyncAction(ActionType.DOWNLOAD, path, remote_info=remote, reason="native doc export")
                )
            else:
                stored = stored_entries.get(path) if stored_entries else None
                if stored and stored.remote_id and local_files:
                    # Previously synced, missing locally with non-empty local tree →
                    # file was deleted locally, propagate deletion to remote.
                    actions.append(
                        SyncAction(
                            ActionType.DELETE_REMOTE,
                            path,
                            remote_info=remote,
                            stored_entry=stored,
                            reason="locally deleted (not found on re-sync)",
                        )
                    )
                else:
                    actions.append(
                        SyncAction(ActionType.DOWNLOAD, path, remote_info=remote, reason="remote only")
                    )
        elif local and remote:
            mime = remote.get("mimeType", "")
            if _is_folder(mime, fm) or local.is_dir:
                actions.append(SyncAction(ActionType.NOOP, path, reason="folder in sync"))
                continue
            # Try the provider's hash field first, fall back to md5Checksum
            remote_hash = remote.get("md5Checksum")
            if remote_hash and local.md5 == remote_hash:
                actions.append(SyncAction(ActionType.NOOP, path, reason="already in sync"))
            elif remote_hash is None:
                if _is_native_doc(mime, skip_mimes):
                    continue
                if convert_native_docs and mime in exportable_mimes:
                    actions.append(
                        SyncAction(ActionType.DOWNLOAD, path, remote_info=remote, reason="native doc export")
                    )
                    continue
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

    actions = detect_moves(actions)

    log.info(
        "Initial plan: %d uploads, %d downloads, %d mkdir, %d moves, %d conflicts, %d noop",
        sum(1 for a in actions if a.action == ActionType.UPLOAD),
        sum(1 for a in actions if a.action == ActionType.DOWNLOAD),
        sum(1 for a in actions if a.action == ActionType.MKDIR),
        sum(1 for a in actions if a.action == ActionType.MOVE),
        sum(1 for a in actions if a.action == ActionType.CONFLICT),
        sum(1 for a in actions if a.action == ActionType.NOOP),
    )
    return actions


def filter_actions_by_mode(
    actions: list[SyncAction],
    sync_mode: str,
) -> list[SyncAction]:
    """Remove or redirect actions based on the sync mode.

    Modes:
    - ``two_way`` – keep everything (default)
    - ``upload_only`` – drop DOWNLOAD and DELETE_LOCAL; CONFLICT → UPLOAD
    - ``download_only`` – drop UPLOAD and DELETE_REMOTE; CONFLICT → DOWNLOAD

    In one-directional modes the authoritative side is unambiguous, so
    conflicts are resolved deterministically rather than deferred.
    """
    if sync_mode == "two_way":
        return actions

    if sync_mode == "upload_only":
        blocked = {ActionType.DOWNLOAD, ActionType.DELETE_LOCAL}
        conflict_resolution = ActionType.UPLOAD
    elif sync_mode == "download_only":
        blocked = {ActionType.UPLOAD, ActionType.DELETE_REMOTE}
        conflict_resolution = ActionType.DOWNLOAD
    else:
        return actions

    result: list[SyncAction] = []
    dropped = 0
    resolved = 0
    for action in actions:
        if action.action == ActionType.CONFLICT:
            result.append(SyncAction(
                action=conflict_resolution,
                path=action.path,
                remote_info=action.remote_info,
                local_info=action.local_info,
                stored_entry=action.stored_entry,
                reason=f"{sync_mode}: conflict resolved as {conflict_resolution.value}",
            ))
            resolved += 1
        elif action.action in blocked:
            dropped += 1
        else:
            result.append(action)

    if dropped:
        log.info("Sync mode %s: dropped %d actions", sync_mode, dropped)
    if resolved:
        log.info("Sync mode %s: resolved %d conflicts as %s", sync_mode, resolved, conflict_resolution.value)
    return result


def plan_continuous_sync(
    changes: list[dict[str, Any]],
    stored_entries: dict[str, SyncEntry],
    *,
    native_doc_mimes: frozenset[str] | None = None,
    folder_mime: str | None = None,
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
    fm = folder_mime if folder_mime is not None else FOLDER_MIME

    actions: list[SyncAction] = []

    for change in changes:
        path = change["path"]
        source = change["source"]
        stored = stored_entries.get(path)
        mime = change.get("mimeType", "")
        if not mime:
            ri = change.get("remote_info")
            if ri:
                mime = ri.get("mimeType", "")
        is_dir = _is_folder(mime, fm) or change.get("is_directory", False)

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

    actions = detect_moves(actions)
    return actions


def apply_strategy_overrides(
    actions: list[SyncAction],
    strategy: str,
) -> list[SyncAction]:
    """Transform actions to enforce a mirror-style conflict strategy.

    Must run AFTER ``apply_sync_rules`` and BEFORE ``filter_actions_by_mode``.

    ``local_wins`` — local is the absolute source of truth:
      CONFLICT → UPLOAD, DOWNLOAD → DELETE_REMOTE, DELETE_LOCAL → UPLOAD

    ``remote_wins`` — remote is the absolute source of truth:
      CONFLICT → DOWNLOAD, UPLOAD → DELETE_LOCAL, DELETE_REMOTE → DOWNLOAD,
      MOVE → [DELETE_LOCAL(dest_path), DOWNLOAD(path)]

    All other strategies return the action list unchanged.
    """
    if strategy not in ("local_wins", "remote_wins"):
        return actions

    result: list[SyncAction] = []

    if strategy == "local_wins":
        for action in actions:
            if action.action == ActionType.CONFLICT:
                result.append(SyncAction(
                    action=ActionType.UPLOAD,
                    path=action.path,
                    local_info=action.local_info,
                    stored_entry=action.stored_entry,
                    reason="local_wins: conflict resolved as upload",
                ))
            elif action.action == ActionType.DOWNLOAD:
                result.append(SyncAction(
                    action=ActionType.DELETE_REMOTE,
                    path=action.path,
                    remote_info=action.remote_info,
                    stored_entry=action.stored_entry,
                    reason="local_wins: remote-only file deleted",
                ))
            elif action.action == ActionType.DELETE_LOCAL:
                result.append(SyncAction(
                    action=ActionType.UPLOAD,
                    path=action.path,
                    stored_entry=action.stored_entry,
                    reason="local_wins: re-upload after remote deletion",
                ))
            else:
                result.append(action)

    else:  # remote_wins
        for action in actions:
            if action.action == ActionType.CONFLICT:
                result.append(SyncAction(
                    action=ActionType.DOWNLOAD,
                    path=action.path,
                    remote_info=action.remote_info,
                    stored_entry=action.stored_entry,
                    reason="remote_wins: conflict resolved as download",
                ))
            elif action.action == ActionType.UPLOAD:
                result.append(SyncAction(
                    action=ActionType.DELETE_LOCAL,
                    path=action.path,
                    local_info=action.local_info,
                    stored_entry=action.stored_entry,
                    reason="remote_wins: local-only file deleted",
                ))
            elif action.action == ActionType.DELETE_REMOTE:
                result.append(SyncAction(
                    action=ActionType.DOWNLOAD,
                    path=action.path,
                    stored_entry=action.stored_entry,
                    reason="remote_wins: re-download after local deletion",
                ))
            elif action.action == ActionType.MOVE:
                # Undo local rename: delete the renamed copy, re-download the original
                if action.dest_path:
                    result.append(SyncAction(
                        action=ActionType.DELETE_LOCAL,
                        path=action.dest_path,
                        local_info=action.local_info,
                        stored_entry=action.stored_entry,
                        reason="remote_wins: undo local rename (delete dest)",
                    ))
                result.append(SyncAction(
                    action=ActionType.DOWNLOAD,
                    path=action.path,
                    stored_entry=action.stored_entry,
                    reason="remote_wins: undo local rename (re-download src)",
                ))
            else:
                result.append(action)

    log.info(
        "Strategy override (%s): %d actions in, %d out",
        strategy, len(actions), len(result),
    )
    return result


def apply_sync_rules(actions: list[SyncAction], rules) -> list[SyncAction]:
    """Filter sync actions based on advanced sync rules.

    Args:
        actions: List of planned sync actions.
        rules: A SyncRules instance with max_file_size_mb, include_regex,
               exclude_regex, and min_date fields.

    Returns:
        Filtered list of actions.
    """
    if rules is None:
        return actions

    max_bytes = rules.max_file_size_mb * 1024 * 1024 if rules.max_file_size_mb > 0 else 0
    include_patterns = [re.compile(p) for p in rules.include_regex if p]
    exclude_patterns = [re.compile(p) for p in rules.exclude_regex if p]

    # Compared as a POSIX timestamp rather than a datetime. min_date is ISO 8601,
    # so fromisoformat returns an *aware* datetime when the string carries an
    # offset ("2026-01-01T00:00:00Z") and a naive one otherwise, while file mtimes
    # are always naive local — comparing the two raised TypeError and aborted the
    # whole sync pass. .timestamp() reads a naive value as local time, which is
    # what the naive comparison already did, and handles the aware case correctly.
    min_ts: float | None = None
    if rules.min_date:
        try:
            min_ts = datetime.fromisoformat(rules.min_date).timestamp()
        except (ValueError, OSError, OverflowError):
            log.warning("Invalid min_date in sync rules: %s", rules.min_date)

    # If no rules are active, skip filtering
    if not max_bytes and not include_patterns and not exclude_patterns and min_ts is None:
        return actions

    filtered: list[SyncAction] = []
    for action in actions:
        # Always keep NOOP, MKDIR, and deletion actions
        if action.action in (ActionType.NOOP, ActionType.MKDIR, ActionType.DELETE_LOCAL, ActionType.DELETE_REMOTE):
            filtered.append(action)
            continue

        path = action.path

        # Max file size check
        if max_bytes > 0:
            size = 0
            if action.local_info and hasattr(action.local_info, "size"):
                size = action.local_info.size or 0
            if not size and action.remote_info:
                size = int(action.remote_info.get("size", 0) or 0)
            if size > max_bytes:
                log.debug("Sync rule: skipping %s (size %d > max %d)", path, size, max_bytes)
                continue

        # Exclude regex check
        if exclude_patterns and any(p.search(path) for p in exclude_patterns):
            log.debug("Sync rule: excluding %s (matched exclude pattern)", path)
            continue

        # Include regex check — if include patterns given, path must match at least one
        if include_patterns and not any(p.search(path) for p in include_patterns):
            log.debug("Sync rule: skipping %s (no include pattern matched)", path)
            continue

        # Min date check
        if min_ts is not None:
            mtime = 0.0
            if action.local_info and hasattr(action.local_info, "mtime"):
                mtime = action.local_info.mtime or 0.0
            if mtime > 0 and mtime < min_ts:
                log.debug("Sync rule: skipping %s (mtime before min_date)", path)
                continue

        filtered.append(action)

    dropped = len(actions) - len(filtered)
    if dropped:
        log.info("Sync rules: filtered out %d actions", dropped)
    return filtered
