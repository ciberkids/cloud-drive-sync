"""Nextcloud CloudClient implementation using WebDAV via nc-py-api."""

from __future__ import annotations

import asyncio
import mimetypes
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

    def __init__(self, nc: Any, server_url: str) -> None:
        """Initialise with a connected ``Nextcloud`` (nc-py-api) instance.

        Args:
            nc: A ``nextcloud_client.Nextcloud`` object (already authenticated).
            server_url: Base URL of the Nextcloud instance.
        """
        self._nc = nc
        self._server_url = server_url.rstrip("/")

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
        """Ensure path starts with / and has no trailing slash (except root)."""
        path = path.strip()
        if not path or path == "/":
            return "/"
        if not path.startswith("/"):
            path = "/" + path
        return path.rstrip("/")

    def _file_to_dict(self, fs_node: Any, relative_path: str = "") -> dict[str, Any]:
        """Convert an nc-py-api FsNode to the normalised metadata dict."""
        info = fs_node.info
        is_dir = fs_node.is_dir if hasattr(fs_node, "is_dir") else info.get("is_dir", False)

        # Nextcloud exposes fileid as an integer
        file_id = str(info.get("fileid", "") or fs_node.file_id if hasattr(fs_node, "file_id") else "")

        # Determine MIME type
        if is_dir:
            mime_type = "httpd/unix-directory"
        else:
            mime_type = info.get("mimetype", "") or (
                mimetypes.guess_type(fs_node.name)[0] or "application/octet-stream"
            )

        # Modified time — prefer last_modified, fall back to info fields
        mod_time = None
        if hasattr(fs_node, "last_modified") and fs_node.last_modified:
            mod_time = fs_node.last_modified
        elif info.get("last_modified"):
            mod_time = info["last_modified"]

        if isinstance(mod_time, datetime):
            mod_time_str = mod_time.astimezone(timezone.utc).isoformat()
        elif mod_time is not None:
            mod_time_str = str(mod_time)
        else:
            mod_time_str = datetime.now(timezone.utc).isoformat()

        size = info.get("size", 0)
        if size is None:
            size = 0

        # Checksum — nc-py-api may expose checksums in info
        md5 = info.get("checksum", "") or ""
        # Nextcloud stores checksums as "MD5:abc123" or "SHA1:..." etc.
        if md5 and ":" in md5:
            parts = md5.split(":")
            for i, part in enumerate(parts):
                if part.upper() == "MD5" and i + 1 < len(parts):
                    md5 = parts[i + 1].strip()
                    break
            else:
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
        """Get metadata for a single file by its fileid."""

        def _get():
            return self._nc.files.by_id(int(file_id))

        node = await asyncio.to_thread(_get)
        if node is None:
            raise FileNotFoundError(f"Nextcloud file not found: fileid={file_id}")
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
        parent_path = "/" if parent_id == "root" else self._normalise_path(parent_id)
        target = f"{parent_path}/{name}" if parent_path != "/" else f"/{name}"

        if is_folder:
            def _mkdir():
                self._nc.files.mkdir(target)
                return self._nc.files.listdir(parent_path)

            nodes = await asyncio.to_thread(_mkdir)
            # Find the newly created folder
            for node in nodes:
                if node.name == name:
                    return self._file_to_dict(node)
            # Fallback: re-list and find by name
            raise RuntimeError(f"Failed to find created folder: {target}")
        else:
            if content_path:
                def _upload():
                    self._nc.files.upload(target, content_path)
                    return self._nc.files.listdir(parent_path)

                nodes = await asyncio.to_thread(_upload)
                for node in nodes:
                    if node.name == name:
                        return self._file_to_dict(node)
                raise RuntimeError(f"Failed to find uploaded file: {target}")
            else:
                # Create empty file
                def _touch():
                    self._nc.files.upload(target, b"")
                    return self._nc.files.listdir(parent_path)

                nodes = await asyncio.to_thread(_touch)
                for node in nodes:
                    if node.name == name:
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
            node = self._nc.files.by_id(int(file_id))
            if node is None:
                raise FileNotFoundError(f"Nextcloud file not found: fileid={file_id}")

            remote_path = node.user_path

            if content_path:
                self._nc.files.upload(remote_path, content_path)

            if new_name:
                parent = remote_path.rsplit("/", 1)[0] or "/"
                new_path = f"{parent}/{new_name}"
                self._nc.files.move(remote_path, new_path)
                remote_path = new_path

            # Re-fetch to get updated metadata
            updated = self._nc.files.by_id(int(file_id))
            return updated

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
            node = self._nc.files.by_id(int(file_id))
            if node is None:
                raise FileNotFoundError(f"Nextcloud file not found: fileid={file_id}")

            src_path = node.user_path
            dest_parent = "/" if new_parent_id == "root" else self._normalise_path(new_parent_id)
            name = new_name or node.name
            dest_path = f"{dest_parent}/{name}" if dest_parent != "/" else f"/{name}"

            self._nc.files.move(src_path, dest_path)

            # Re-fetch updated metadata
            updated = self._nc.files.by_id(int(file_id))
            return updated

        result_node = await asyncio.to_thread(_move)
        return self._file_to_dict(result_node)

    @async_retry(max_retries=3, base_delay=1.0)
    async def delete_file(self, file_id: str) -> None:
        def _delete():
            node = self._nc.files.by_id(int(file_id))
            if node is None:
                raise FileNotFoundError(f"Nextcloud file not found: fileid={file_id}")
            self._nc.files.delete(node.user_path)

        await asyncio.to_thread(_delete)

    @async_retry(max_retries=3, base_delay=1.0)
    async def trash_file(self, file_id: str) -> dict[str, Any]:
        def _trash():
            node = self._nc.files.by_id(int(file_id))
            if node is None:
                raise FileNotFoundError(f"Nextcloud file not found: fileid={file_id}")
            meta = self._file_to_dict(node)
            self._nc.files.trashbin.delete(node.user_path)
            return meta

        return await asyncio.to_thread(_trash)

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_about(self) -> dict[str, Any]:
        def _about():
            user = self._nc.users.get_current()
            quota = user.quota if hasattr(user, "quota") else {}
            return {
                "user": {
                    "displayName": user.display_name if hasattr(user, "display_name") else str(user),
                    "emailAddress": user.email if hasattr(user, "email") else "",
                },
                "storageQuota": {
                    "limit": str(quota.get("total", 0)) if isinstance(quota, dict) else "0",
                    "usage": str(quota.get("used", 0)) if isinstance(quota, dict) else "0",
                },
            }

        return await asyncio.to_thread(_about)

    async def find_child_folder(self, parent_id: str, name: str) -> str | None:
        """Check if a child folder exists under parent_path/name.

        ``parent_id`` is a WebDAV path. Returns the child folder path if found.
        """
        parent_path = "/" if parent_id == "root" else self._normalise_path(parent_id)
        child_path = f"{parent_path}/{name}" if parent_path != "/" else f"/{name}"

        def _check():
            try:
                nodes = self._nc.files.listdir(parent_path)
                for node in nodes:
                    is_dir = node.is_dir if hasattr(node, "is_dir") else node.info.get("is_dir", False)
                    if node.name == name and is_dir:
                        return child_path
                return None
            except Exception:
                return None

        return await asyncio.to_thread(_check)

    async def list_all_recursive(
        self, folder_id: str = "root", prefix: str = ""
    ) -> list[dict[str, Any]]:
        """Recursively list all files and folders."""
        items = await self.list_all_files(folder_id)
        result: list[dict[str, Any]] = []
        for item in items:
            rel = f"{prefix}/{item['name']}" if prefix else item["name"]
            item["relativePath"] = rel
            if item.get("mimeType") == "httpd/unix-directory":
                result.append(item)
                # For subfolders, use the WebDAV path
                folder_path = "/" if folder_id == "root" else self._normalise_path(folder_id)
                child_path = (
                    f"{folder_path}/{item['name']}"
                    if folder_path != "/"
                    else f"/{item['name']}"
                )
                children = await self.list_all_recursive(child_path, rel)
                result.extend(children)
            else:
                result.append(item)
        return result
