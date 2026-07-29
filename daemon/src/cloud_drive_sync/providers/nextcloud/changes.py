"""Nextcloud CloudChangePoller implementation using ETag-based polling.

Nextcloud does not have a delta/changes API like Google Drive. Instead, we poll
the root folder's ETag and, when it changes, walk the tree to discover what
changed.  Folder ETags in Nextcloud propagate upward, so a change in any
descendant causes the root ETag to change.

The page token is a JSON blob: ``{"etags": {"<path>": "<etag>", ...}}``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from cloud_drive_sync.providers.base import CloudChangePoller
from cloud_drive_sync.providers.gdrive.changes import RemoteChange
from cloud_drive_sync.providers.nextcloud.client import NextcloudClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.nextcloud.changes")

# Minimal PROPFIND properties needed for ETag-based change detection.
# Excludes oc:checksums and oc:share-types, which cause Nextcloud's PHP-FPM
# workers to run at 100% CPU for large directories (issue #44).
_ETAG_MAP_PROPERTIES = [
    "d:resourcetype",
    "d:getetag",
    "d:getlastmodified",
    "oc:fileid",
    "oc:id",
]


def _build_etag_map(nc: Any, path: str = "/") -> dict[str, dict[str, Any]]:
    """Recursively build a map of ``{path: {etag, fileid, name, is_dir, ...}}``.

    All FsNodeInfo attribute access is done here so callers never receive a
    FsNodeInfo object — only plain Python scalars (fixes #33 / #11 pattern).
    """
    result: dict[str, dict[str, Any]] = {}
    try:
        nodes = nc.files._listdir(
            nc.files._session.user, path, _ETAG_MAP_PROPERTIES, 1, True
        )
    except Exception:
        log.warning("Failed to list directory: %s", path)
        return result

    for node in nodes:
        info = node.info
        is_dir = bool(node.is_dir) if hasattr(node, "is_dir") else False
        etag = str(getattr(info, "etag", "") or "")
        fileid = str(getattr(info, "fileid", "") or getattr(node, "file_id", "") or "")
        node_path = node.user_path if hasattr(node, "user_path") else f"{path}/{node.name}".replace("//", "/")

        # Resolve mimetype to a plain string — never store FsNodeInfo
        if is_dir:
            mimetype = "httpd/unix-directory"
        else:
            mimetype = str(getattr(info, "mimetype", "") or "application/octet-stream")

        last_modified = str(getattr(info, "last_modified", "") or "")
        # nc-py-api v0.30 does not expose checksums; store empty string
        checksum = str(getattr(info, "checksum", "") or "")

        result[node_path] = {
            "etag": etag,
            "fileid": fileid,
            "name": node.name,
            "is_dir": is_dir,
            "mimetype": mimetype,
            "last_modified": last_modified,
            "checksum": checksum,
        }

        if is_dir:
            children = _build_etag_map(nc, node_path)
            result.update(children)

    return result


class NextcloudChangePoller(CloudChangePoller):
    """Detects remote changes by comparing ETag snapshots.

    Costs one PROPFIND per directory per poll. ``NextcloudPushPoller`` wraps this
    and avoids most of those calls when the server has notify_push (issue #56);
    this remains the fallback and the reconciliation pass.
    """

    def __init__(self, client: NextcloudClient) -> None:
        self._client = client

    @async_retry(max_retries=3, base_delay=2.0)
    async def get_start_page_token(self) -> str:
        """Build an initial ETag snapshot of the entire tree.

        The returned "token" is a JSON-encoded mapping of paths to ETags.
        """

        def _snapshot():
            etag_map = _build_etag_map(self._client._nc)
            # Store only path -> etag for the token (keep it small)
            return {path: data["etag"] for path, data in etag_map.items()}

        etags = await asyncio.to_thread(_snapshot)
        token = json.dumps({"etags": etags}, separators=(",", ":"))
        log.debug("Initial ETag snapshot: %d entries", len(etags))
        return token

    @async_retry(max_retries=3, base_delay=2.0)
    async def poll_changes(self, page_token: str) -> tuple[list[RemoteChange], str]:
        """Compare current ETag state against the saved snapshot.

        Returns a list of ``RemoteChange`` objects and a new token.
        """
        try:
            old_state = json.loads(page_token)
            old_etags: dict[str, str] = old_state.get("etags", {})
        except (json.JSONDecodeError, TypeError):
            log.warning("Invalid page token, rebuilding snapshot")
            old_etags = {}

        def _scan():
            return _build_etag_map(self._client._nc)

        current_map = await asyncio.to_thread(_scan)

        changes: list[RemoteChange] = []
        current_etags: dict[str, str] = {}

        for path, data in current_map.items():
            etag = data["etag"]
            current_etags[path] = etag

            old_etag = old_etags.get(path)
            if old_etag is None:
                # New file or folder
                log.debug("New: %s (fileid=%s)", path, data["fileid"])
                changes.append(
                    RemoteChange(
                        file_id=data["fileid"],
                        file_name=data["name"],
                        mime_type=data["mimetype"],
                        md5=self._extract_md5(data["checksum"]),
                        modified_time=data["last_modified"],
                        removed=False,
                        trashed=False,
                        parents=[],
                    )
                )
            elif etag != old_etag and not data["is_dir"]:
                # Modified file (skip directories — their ETags change when children change)
                log.debug("Modified: %s (fileid=%s)", path, data["fileid"])
                changes.append(
                    RemoteChange(
                        file_id=data["fileid"],
                        file_name=data["name"],
                        mime_type=data["mimetype"],
                        md5=self._extract_md5(data["checksum"]),
                        modified_time=data["last_modified"],
                        removed=False,
                        trashed=False,
                        parents=[],
                    )
                )

        # Detect removals: paths in old_etags but not in current
        current_paths = set(current_map.keys())
        for old_path in old_etags:
            if old_path not in current_paths:
                # We don't have the fileid from the deleted file, but we can
                # reconstruct a name from the path
                name = old_path.rsplit("/", 1)[-1] if "/" in old_path else old_path
                log.debug("Removed: %s", old_path)
                changes.append(
                    RemoteChange(
                        file_id="",  # Unknown — file is gone
                        file_name=name,
                        removed=True,
                        trashed=False,
                        parents=[],
                    )
                )

        new_token = json.dumps({"etags": current_etags}, separators=(",", ":"))
        log.debug("Polled %d changes, %d entries in snapshot", len(changes), len(current_etags))
        return changes, new_token

    @staticmethod
    def _extract_md5(checksum: str) -> str:
        """Extract MD5 from Nextcloud checksum field (format: ``MD5:hex``)."""
        if checksum and ":" in checksum:
            parts = checksum.split(":")
            for i, part in enumerate(parts):
                if part.upper() == "MD5" and i + 1 < len(parts):
                    return parts[i + 1].strip()
        return ""
