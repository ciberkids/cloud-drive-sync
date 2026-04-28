"""Nextcloud CloudClient implementation using WebDAV via nc-py-api."""

from __future__ import annotations

import asyncio
import mimetypes
import unicodedata
from datetime import datetime, timezone
from typing import Any

from cloud_drive_sync.providers.base import CloudClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.nextcloud.client")


class NextcloudClient(CloudClient):
    """Nextcloud WebDAV client implementing CloudClient.

    Uses nc-py-api for communication with a Nextcloud server.
    File IDs are Nextcloud numeric ``fileid`` values (as strings).
    Folder identifiers in ``list_files`` are WebDAV paths (e.g. ``/`` or ``/Documents``).
    """

    def __init__(self, nc: Any, server_url: str, username: str = "", app_password: str = "") -> None:
        """Initialise with a connected ``Nextcloud`` (nc-py-api) instance.

        Args:
            nc: A ``nextcloud_client.Nextcloud`` object (already authenticated).
            server_url: Base URL of the Nextcloud instance.
            username: Nextcloud username (stored for direct WebDAV streaming).
            app_password: Nextcloud app password (stored for direct WebDAV streaming).
        """
        self._nc = nc
        self._server_url = server_url.rstrip("/")
        self._username = username or (nc.user if hasattr(nc, "user") else "")
        self._app_password = app_password

    # ── CloudClient capability properties ───────────────────────────

    @property
    def supports_trash(self) -> bool:
        return True

    @property
    def supports_export(self) -> bool:
        return False

    @property
    def hash_field(self) -> str:
        return "md5Checksum"

    @property
    def hash_algorithm(self) -> str:
        return "md5"

    @property
    def folder_mime_type(self) -> str | None:
        return None  # Path-based provider

    @property
    def native_doc_mimes(self) -> frozenset[str]:
        return frozenset()

    # ── Internal helpers ────────────────────────────────────────────

    def _normalise_path(self, path: str) -> str:
        """Ensure path starts with / and has no trailing slash (except root).

        Only leading whitespace and trailing slashes are stripped — trailing
        spaces inside path segments are preserved so that legacy Nextcloud
        folders created with trailing spaces (issue #26) remain addressable.
        """
        path = path.lstrip()
        if not path or path == "/":
            return "/"
        if not path.startswith("/"):
            path = "/" + path
        return path.rstrip("/")

    def _resolve_path(self, file_id: str) -> str:
        """Return the WebDAV path for a file_id.

        New IDs are WebDAV paths (start with /). nc-py-api returns user_path
        without a leading slash (e.g. "Documents", "Documents/ManuAndI") — these
        are relative WebDAV paths and must be normalised. Legacy IDs from older
        sync databases are compound Nextcloud fileids like ``00000162ocmvvvbtlon4``
        — extract the numeric prefix and call by_id as a fallback.
        """
        if file_id == "root" or file_id == "/":
            return "/"
        if file_id.startswith("/"):
            return self._normalise_path(file_id)
        # Detect relative WebDAV paths: contain "/" or ".", or too short to be a
        # compound fileid (genuine fileids are ≥10 chars with ≥8 digit prefix).
        numeric = "".join(c for c in file_id if c.isdigit())
        is_relative_path = "/" in file_id or "." in file_id or len(numeric) < 8 or len(file_id) < 10
        if is_relative_path:
            return self._normalise_path(file_id)
        # Legacy compound fileid: strip non-digit suffix and look up by integer ID
        node = self._nc.files.by_id(int(numeric))
        if node is None:
            raise FileNotFoundError(f"Nextcloud file not found: fileid={file_id}")
        return node.user_path

    def _file_to_dict(self, fs_node: Any, relative_path: str = "") -> dict[str, Any]:
        """Convert an nc-py-api FsNode to the normalised metadata dict."""
        info = fs_node.info  # FsNodeInfo typed object — use attribute access, not .get()
        is_dir = bool(fs_node.is_dir)

        # Use the WebDAV user_path as the stable ID — it is the key accepted by
        # all nc-py-api path-based operations (upload, download, mkdir, delete, move).
        # The compound fileid (e.g. "00000162ocmvvvbtlon4") is NOT a valid path
        # segment in /dav/files/ and must never be used as one.
        raw_path = fs_node.user_path or fs_node.file_id or str(getattr(info, "fileid", ""))
        file_id = self._normalise_path(raw_path) if raw_path else raw_path

        # Determine MIME type
        if is_dir:
            mime_type = "httpd/unix-directory"
        else:
            mime_type = getattr(info, "mimetype", "") or (
                mimetypes.guess_type(fs_node.name)[0] or "application/octet-stream"
            )

        # Modified time via FsNodeInfo.last_modified (datetime property)
        mod_time = getattr(info, "last_modified", None)
        if isinstance(mod_time, datetime):
            mod_time_str = mod_time.astimezone(timezone.utc).isoformat()
        elif mod_time is not None:
            mod_time_str = str(mod_time)
        else:
            mod_time_str = datetime.now(timezone.utc).isoformat()

        size = getattr(info, "size", 0) or 0

        # nc-py-api v0.30 FsNodeInfo does not expose raw checksums
        md5 = ""

        result: dict[str, Any] = {
            "id": file_id,
            "name": fs_node.name,
            "mimeType": mime_type,
            "md5Checksum": md5,
            "modifiedTime": mod_time_str,
            "size": int(size),
        }
        if relative_path:
            result["relativePath"] = relative_path

        return result

    # ── CloudClient methods ─────────────────────────────────────────

    @async_retry(max_retries=3, base_delay=1.0)
    async def list_files(
        self,
        folder_id: str = "root",
        page_token: str | None = None,
        page_size: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        """List files in a Nextcloud folder.

        ``folder_id`` is a WebDAV path (e.g. ``/``, ``/Documents``).
        Pagination is emulated via offset-based slicing of the result set;
        ``page_token`` is an integer offset encoded as a string.
        """
        path = "/" if folder_id == "root" else self._normalise_path(folder_id)
        offset = int(page_token) if page_token else 0

        def _list():
            return self._nc.files.listdir(path)

        all_nodes = await asyncio.to_thread(_list)

        # Apply offset-based pagination
        page = all_nodes[offset : offset + page_size]
        files = [self._file_to_dict(node) for node in page]

        result: dict[str, Any] = {"files": files}
        next_offset = offset + page_size
        if next_offset < len(all_nodes):
            result["nextPageToken"] = str(next_offset)

        return result

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_file(self, file_id: str) -> dict[str, Any]:
        """Get metadata for a single file by its ID (path or legacy fileid)."""

        def _get():
            path = self._resolve_path(file_id)
            parent = path.rsplit("/", 1)[0] or "/"
            nodes = self._nc.files.listdir(parent)
            name = path.rsplit("/", 1)[-1]
            node = next((n for n in nodes if n.name == name), None)
            if node is None:
                raise FileNotFoundError(f"Nextcloud file not found: {file_id}")
            return node

        node = await asyncio.to_thread(_get)
        return self._file_to_dict(node)

    @async_retry(max_retries=3, base_delay=1.0)
    async def create_file(
        self,
        name: str,
        parent_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        is_folder: bool = False,
    ) -> dict[str, Any]:
        # Normalise to NFC and strip whitespace to avoid creating new folders
        # with leading/trailing spaces, and so the post-operation search matches.
        name = unicodedata.normalize("NFC", name.strip())
        parent_path = "/" if parent_id == "root" else self._normalise_path(parent_id)
        target = f"{parent_path}/{name}" if parent_path != "/" else f"/{name}"

        if is_folder:
            def _mkdir():
                try:
                    self._nc.files.mkdir(target)
                except Exception as e:
                    if getattr(e, "status_code", None) != 405:  # 405 = already exists (RFC 4918)
                        raise
                nodes = self._nc.files.listdir(parent_path)
                for node in nodes:
                    if unicodedata.normalize("NFC", node.name.strip()) == name:
                        return node
                raise RuntimeError(f"Failed to find created folder: {target}")

            node = await asyncio.to_thread(_mkdir)
            return self._file_to_dict(node)
        else:
            if content_path:
                def _upload():
                    self._nc.files.upload(target, content_path)
                    return self._nc.files.listdir(parent_path)

                nodes = await asyncio.to_thread(_upload)
                for node in nodes:
                    if unicodedata.normalize("NFC", node.name.strip()) == name:
                        return self._file_to_dict(node)
                raise RuntimeError(f"Failed to find uploaded file: {target}")
            else:
                # Create empty file
                def _touch():
                    self._nc.files.upload(target, b"")
                    return self._nc.files.listdir(parent_path)

                nodes = await asyncio.to_thread(_touch)
                for node in nodes:
                    if unicodedata.normalize("NFC", node.name.strip()) == name:
                        return self._file_to_dict(node)
                raise RuntimeError(f"Failed to find created file: {target}")

    @async_retry(max_retries=3, base_delay=1.0)
    async def update_file(
        self,
        file_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        def _update():
            remote_path = self._resolve_path(file_id)

            if content_path:
                self._nc.files.upload(remote_path, content_path)

            if new_name:
                parent = remote_path.rsplit("/", 1)[0] or "/"
                new_path = f"{parent}/{new_name}"
                self._nc.files.move(remote_path, new_path)
                remote_path = new_path

            # Re-fetch metadata via parent listing
            parent_dir = remote_path.rsplit("/", 1)[0] or "/"
            nodes = self._nc.files.listdir(parent_dir)
            target_name = remote_path.rsplit("/", 1)[-1]
            node = next((n for n in nodes if n.name == target_name), None)
            if node is None:
                raise RuntimeError(f"Updated file not found: {remote_path}")
            return node

        result_node = await asyncio.to_thread(_update)
        return self._file_to_dict(result_node)

    @async_retry(max_retries=3, base_delay=1.0)
    async def move_file(
        self,
        file_id: str,
        new_parent_id: str,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        def _move():
            src_path = self._resolve_path(file_id)
            dest_parent = "/" if new_parent_id == "root" else self._resolve_path(new_parent_id)
            name = new_name or src_path.rsplit("/", 1)[-1]
            dest_path = f"{dest_parent}/{name}" if dest_parent != "/" else f"/{name}"

            self._nc.files.move(src_path, dest_path)

            # Re-fetch metadata via parent listing
            nodes = self._nc.files.listdir(dest_parent)
            node = next((n for n in nodes if n.name == name), None)
            if node is None:
                raise RuntimeError(f"Moved file not found: {dest_path}")
            return node

        result_node = await asyncio.to_thread(_move)
        return self._file_to_dict(result_node)

    @async_retry(max_retries=3, base_delay=1.0)
    async def delete_file(self, file_id: str) -> None:
        def _delete():
            self._nc.files.delete(self._resolve_path(file_id))

        await asyncio.to_thread(_delete)

    @async_retry(max_retries=3, base_delay=1.0)
    async def trash_file(self, file_id: str) -> dict[str, Any]:
        def _trash():
            path = self._resolve_path(file_id)
            # Get metadata before deleting
            parent_dir = path.rsplit("/", 1)[0] or "/"
            nodes = self._nc.files.listdir(parent_dir)
            name = path.rsplit("/", 1)[-1]
            node = next((n for n in nodes if n.name == name), None)
            if node is None:
                raise FileNotFoundError(f"Nextcloud file not found: {file_id}")
            meta = self._file_to_dict(node)
            # files.delete() moves to trash if trash is enabled on the server,
            # otherwise permanently deletes — nc-py-api ≥0.17 removed trashbin.delete
            self._nc.files.delete(path)
            return meta

        return await asyncio.to_thread(_trash)

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_about(self) -> dict[str, Any]:
        def _about():
            user = self._nc.users.get_user()  # no arg = current user
            quota = getattr(user, "quota", {}) or {}
            return {
                "user": {
                    "displayName": getattr(user, "display_name", None) or self._nc.user,
                    "emailAddress": getattr(user, "email", "") or "",
                },
                "storageQuota": {
                    "limit": str(quota.get("total", 0)) if isinstance(quota, dict) else "0",
                    "usage": str(quota.get("used", 0)) if isinstance(quota, dict) else "0",
                },
            }

        return await asyncio.to_thread(_about)

    async def find_child_folder(self, parent_id: str, name: str) -> str | None:
        """Check if a child folder exists under parent_id/name.

        Returns the child folder's ID (WebDAV path) if found, else None.
        """
        # Normalise to NFC and strip whitespace so legacy folders with leading/
        # trailing spaces (e.g. " additional costs") are still found when the
        # search name also has surrounding whitespace (fixes #34 / #26 follow-up).
        name_nfc = unicodedata.normalize("NFC", name.strip())

        def _check():
            try:
                parent_path = self._resolve_path(parent_id)
                nodes = self._nc.files.listdir(parent_path)
                for node in nodes:
                    is_dir = node.is_dir if hasattr(node, "is_dir") else False
                    # Strip trailing whitespace from server name before comparing so
                    # legacy folders with trailing spaces (issue #26) are still found.
                    if unicodedata.normalize("NFC", node.name.strip()) == name_nfc and is_dir:
                        return self._normalise_path(node.user_path)
                return None
            except Exception as e:
                status = getattr(e, "status_code", None)
                if not isinstance(e, FileNotFoundError) and status not in (404, 405):
                    raise
                return None

        return await asyncio.to_thread(_check)

    async def list_all_recursive(
        self, folder_id: str = "root", prefix: str = "", _is_root: bool = True
    ) -> list[dict[str, Any]]:
        """Recursively list all files and folders.

        Raises ``FileNotFoundError`` when the root folder itself is missing (404)
        so the engine can create it and retry. 404s on subfolders are silently
        skipped (existing behaviour).
        """
        try:
            items = await self.list_all_files(folder_id)
        except Exception as e:
            if getattr(e, "status_code", None) == 404:
                if _is_root:
                    raise FileNotFoundError(
                        f"Remote root folder not found: {folder_id}"
                    ) from e
                log.warning("Skipping inaccessible Nextcloud folder %r (404)", folder_id)
                return []
            raise
        result: list[dict[str, Any]] = []
        for item in items:
            rel = f"{prefix}/{item['name']}" if prefix else item["name"]
            item["relativePath"] = rel
            if item.get("mimeType") == "httpd/unix-directory":
                result.append(item)
                try:
                    children = await self.list_all_recursive(item["id"], rel, _is_root=False)
                    result.extend(children)
                except Exception as e:
                    if getattr(e, "status_code", None) == 404:
                        log.warning("Skipping inaccessible Nextcloud subfolder %r (404)", rel)
                    else:
                        raise
            else:
                result.append(item)
        return result

    async def ensure_root_folder(self, folder_path: str) -> None:
        """Create ``folder_path`` and all missing parent segments.

        Safe to call when folders already exist — each segment that returns
        405 (already exists per RFC 4918) is silently accepted.  Used by the
        engine to recover from a missing remote sync root (issue #36).
        """
        if folder_path in ("root", "/", ""):
            return
        path = self._normalise_path(folder_path)
        segments = [s for s in path.split("/") if s]
        current = "/"
        for segment in segments:
            await self.create_file(name=segment, parent_id=current, is_folder=True)
            current = f"/{segment}" if current == "/" else f"{current}/{segment}"
        log.info("Ensured remote root folder: %s", path)
