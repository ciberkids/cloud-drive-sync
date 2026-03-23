"""Dropbox CloudClient implementation."""

from __future__ import annotations

import asyncio
from typing import Any

from cloud_drive_sync.providers.base import CloudClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.dropbox.client")


def _metadata_to_dict(entry: Any) -> dict[str, Any]:
    """Convert a Dropbox metadata entry to a normalized dict."""
    import dropbox

    is_folder = isinstance(entry, dropbox.files.FolderMetadata)
    result: dict[str, Any] = {
        "id": entry.path_lower or entry.path_display,
        "name": entry.name,
        "mimeType": "folder" if is_folder else "application/octet-stream",
    }

    if isinstance(entry, dropbox.files.FileMetadata):
        result["content_hash"] = entry.content_hash
        result["modifiedTime"] = entry.server_modified.isoformat() + "Z"
        result["size"] = entry.size
    else:
        result["content_hash"] = None
        result["modifiedTime"] = None
        result["size"] = 0

    return result


class DropboxClient(CloudClient):
    """Dropbox API wrapper implementing CloudClient.

    Dropbox is path-based: folder_id parameters are Dropbox paths
    (e.g. "/Documents"), and root is represented as "".
    """

    def __init__(self, dbx: Any) -> None:
        self._dbx = dbx

    @property
    def dbx(self) -> Any:
        return self._dbx

    # ── CloudClient capability properties ───────────────────────────

    @property
    def supports_trash(self) -> bool:
        return True

    @property
    def supports_export(self) -> bool:
        return False

    @property
    def hash_field(self) -> str:
        return "content_hash"

    @property
    def hash_algorithm(self) -> str:
        return "content_hash"

    @property
    def folder_mime_type(self) -> str | None:
        return None

    @property
    def native_doc_mimes(self) -> frozenset[str]:
        return frozenset()

    # ── Internal helpers ────────────────────────────────────────────

    async def _run(self, func, *args, **kwargs):
        """Run a blocking Dropbox SDK call in a thread."""
        return await asyncio.to_thread(func, *args, **kwargs)

    # ── CloudClient methods ─────────────────────────────────────────

    @async_retry(max_retries=3, base_delay=1.0)
    async def list_files(
        self,
        folder_id: str = "root",
        page_token: str | None = None,
        page_size: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        import dropbox

        # Normalize: "root" → "" for Dropbox root
        path = "" if folder_id in ("root", "") else folder_id

        if page_token:
            # Continue a previous listing
            result = await self._run(self._dbx.files_list_folder_continue, page_token)
        else:
            result = await self._run(
                self._dbx.files_list_folder, path, limit=page_size
            )

        files = [
            _metadata_to_dict(entry)
            for entry in result.entries
            if isinstance(entry, (dropbox.files.FileMetadata, dropbox.files.FolderMetadata))
        ]

        response: dict[str, Any] = {"files": files}
        if result.has_more:
            response["nextPageToken"] = result.cursor
        return response

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_file(self, file_id: str) -> dict[str, Any]:
        metadata = await self._run(self._dbx.files_get_metadata, file_id)
        return _metadata_to_dict(metadata)

    @async_retry(max_retries=3, base_delay=1.0)
    async def create_file(
        self,
        name: str,
        parent_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        is_folder: bool = False,
    ) -> dict[str, Any]:
        import dropbox

        parent = "" if parent_id in ("root", "") else parent_id
        full_path = f"{parent}/{name}"

        if is_folder:
            result = await self._run(self._dbx.files_create_folder_v2, full_path)
            return _metadata_to_dict(result.metadata)
        else:
            if not content_path:
                # Upload empty file
                content = b""
            else:
                with open(content_path, "rb") as f:
                    content = f.read()

            mode = dropbox.files.WriteMode.add
            result = await self._run(
                self._dbx.files_upload, content, full_path, mode=mode
            )
            return _metadata_to_dict(result)

    @async_retry(max_retries=3, base_delay=1.0)
    async def update_file(
        self,
        file_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        import dropbox

        current_path = file_id

        if new_name:
            # Move/rename the file
            parent = "/".join(current_path.rsplit("/", 1)[:-1])
            new_path = f"{parent}/{new_name}" if parent else f"/{new_name}"
            result = await self._run(
                self._dbx.files_move_v2, current_path, new_path
            )
            current_path = result.metadata.path_lower

        if content_path:
            with open(content_path, "rb") as f:
                content = f.read()
            mode = dropbox.files.WriteMode.overwrite
            result = await self._run(
                self._dbx.files_upload, content, current_path, mode=mode
            )
            return _metadata_to_dict(result)

        # If only renamed, get fresh metadata
        metadata = await self._run(self._dbx.files_get_metadata, current_path)
        return _metadata_to_dict(metadata)

    @async_retry(max_retries=3, base_delay=1.0)
    async def delete_file(self, file_id: str) -> None:
        await self._run(self._dbx.files_permanently_delete, file_id)

    @async_retry(max_retries=3, base_delay=1.0)
    async def trash_file(self, file_id: str) -> dict[str, Any]:
        result = await self._run(self._dbx.files_delete_v2, file_id)
        return _metadata_to_dict(result.metadata)

    async def list_all_recursive(
        self, folder_id: str = "root", prefix: str = ""
    ) -> list[dict[str, Any]]:
        import dropbox

        path = "" if folder_id in ("root", "") else folder_id
        result = await self._run(
            self._dbx.files_list_folder, path, recursive=True, limit=2000
        )

        all_entries: list[Any] = list(result.entries)
        while result.has_more:
            result = await self._run(
                self._dbx.files_list_folder_continue, result.cursor
            )
            all_entries.extend(result.entries)

        items: list[dict[str, Any]] = []
        base_path = path  # e.g. "" or "/documents"

        for entry in all_entries:
            if not isinstance(entry, (dropbox.files.FileMetadata, dropbox.files.FolderMetadata)):
                continue
            item = _metadata_to_dict(entry)

            # Compute relative path: strip the base folder prefix
            entry_path = entry.path_lower or entry.path_display
            if base_path:
                rel = entry_path[len(base_path):]
            else:
                rel = entry_path
            # Strip leading slash
            rel = rel.lstrip("/")
            if prefix:
                rel = f"{prefix}/{rel}"
            item["relativePath"] = rel
            items.append(item)

        return items

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_about(self) -> dict[str, Any]:
        account = await self._run(self._dbx.users_get_current_account)
        space = await self._run(self._dbx.users_get_space_usage)

        used = space.used
        if hasattr(space.allocation, "allocated"):
            allocated = space.allocation.allocated
        else:
            # Individual allocation
            allocated = space.allocation.get_individual().allocated

        return {
            "user": {
                "displayName": account.name.display_name,
                "emailAddress": account.email,
            },
            "storageQuota": {
                "usage": str(used),
                "limit": str(allocated),
            },
        }

    @async_retry(max_retries=3, base_delay=1.0)
    async def move_file(
        self,
        file_id: str,
        new_parent_id: str,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        # file_id is the current path; new_parent_id is the destination folder path
        from_path = file_id
        dest_parent = "" if new_parent_id in ("root", "") else new_parent_id

        # Use the current filename unless a new name is provided
        if new_name:
            dest_name = new_name
        else:
            dest_name = from_path.rsplit("/", 1)[-1]

        to_path = f"{dest_parent}/{dest_name}"

        result = await self._run(self._dbx.files_move_v2, from_path, to_path)
        return _metadata_to_dict(result.metadata)

    async def find_child_folder(self, parent_id: str, name: str) -> str | None:
        import dropbox

        parent = "" if parent_id in ("root", "") else parent_id
        target_path = f"{parent}/{name}"

        try:
            metadata = await self._run(self._dbx.files_get_metadata, target_path)
            if isinstance(metadata, dropbox.files.FolderMetadata):
                return metadata.path_lower
        except Exception:
            pass
        return None
