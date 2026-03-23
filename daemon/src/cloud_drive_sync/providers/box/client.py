"""Box CloudClient implementation."""

from __future__ import annotations

import asyncio
from typing import Any

from cloud_drive_sync.providers.base import CloudClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.box.client")

_BOX_NOTE_MIME = "application/vnd.box.note"


def _normalize_item(item: Any) -> dict[str, Any]:
    """Convert a Box SDK item into a normalized metadata dict."""
    item_type = item.type if hasattr(item, "type") else getattr(item, "item_type", "file")
    is_folder = item_type == "folder"
    mime_type = "folder" if is_folder else (getattr(item, "content_type", None) or "application/octet-stream")

    modified = getattr(item, "content_modified_at", None) or getattr(item, "modified_at", None)
    if modified and hasattr(modified, "isoformat"):
        modified = modified.isoformat()

    return {
        "id": str(item.id),
        "name": item.name,
        "mimeType": mime_type,
        "sha1": getattr(item, "sha1", None),
        "modifiedTime": modified,
        "size": int(item.size) if getattr(item, "size", None) is not None else 0,
        "parents": [str(item.parent.id)] if getattr(item, "parent", None) else [],
        "trashed": getattr(item, "trashed_at", None) is not None,
    }


class BoxClient(CloudClient):
    """Box API wrapper implementing CloudClient.

    Uses the box-sdk-gen library (lazy-imported).
    """

    def __init__(self, box_client: Any) -> None:
        self._client = box_client

    # ── CloudClient capability properties ────────────────────────────

    @property
    def supports_trash(self) -> bool:
        return True

    @property
    def supports_export(self) -> bool:
        return False

    @property
    def hash_field(self) -> str:
        return "sha1"

    @property
    def hash_algorithm(self) -> str:
        return "sha1"

    @property
    def folder_mime_type(self) -> str | None:
        return None

    @property
    def native_doc_mimes(self) -> frozenset[str]:
        return frozenset({_BOX_NOTE_MIME})

    # ── Internal helpers ─────────────────────────────────────────────

    @property
    def client(self) -> Any:
        """Expose the underlying Box SDK client for operations/changes modules."""
        return self._client

    async def _run(self, func, *args, **kwargs):
        """Run a blocking Box SDK call in a thread."""
        return await asyncio.to_thread(func, *args, **kwargs)

    # ── CloudClient methods ──────────────────────────────────────────

    @async_retry(max_retries=3, base_delay=1.0)
    async def list_files(
        self,
        folder_id: str = "0",
        page_token: str | None = None,
        page_size: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        offset = int(page_token) if page_token else 0

        items_result = await self._run(
            self._client.folders.get_folder_items,
            folder_id,
            fields=["id", "type", "name", "sha1", "size", "modified_at",
                     "content_modified_at", "parent", "trashed_at", "content_type"],
            offset=offset,
            limit=page_size,
        )

        files = [_normalize_item(entry) for entry in items_result.entries]
        total = items_result.total_count
        next_offset = offset + len(files)
        next_page_token = str(next_offset) if next_offset < total else None

        result: dict[str, Any] = {"files": files}
        if next_page_token:
            result["nextPageToken"] = next_page_token
        return result

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_file(self, file_id: str) -> dict[str, Any]:
        item = await self._run(
            self._client.files.get_file_by_id,
            file_id,
            fields=["id", "type", "name", "sha1", "size", "modified_at",
                     "content_modified_at", "parent", "trashed_at", "content_type"],
        )
        return _normalize_item(item)

    @async_retry(max_retries=3, base_delay=1.0)
    async def create_file(
        self,
        name: str,
        parent_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        is_folder: bool = False,
    ) -> dict[str, Any]:
        from box_sdk_gen.managers.folders import CreateFolderParent
        from box_sdk_gen.managers.uploads import UploadFileAttributes, UploadFileAttributesParentField

        if is_folder:
            folder = await self._run(
                self._client.folders.create_folder,
                name,
                CreateFolderParent(id=parent_id),
            )
            return _normalize_item(folder)

        if content_path:
            import os

            file_size = os.path.getsize(content_path)
            attrs = UploadFileAttributes(name=name, parent=UploadFileAttributesParentField(id=parent_id))

            if file_size > 50 * 1024 * 1024:
                result = await self._chunked_upload(content_path, attrs, file_size)
            else:
                with open(content_path, "rb") as f:
                    files_obj = await self._run(
                        self._client.uploads.upload_file,
                        attrs,
                        f,
                    )
                result = files_obj.entries[0]
            return _normalize_item(result)

        # Create empty file by uploading empty bytes
        import io

        attrs = UploadFileAttributes(name=name, parent=UploadFileAttributesParentField(id=parent_id))
        files_obj = await self._run(
            self._client.uploads.upload_file,
            attrs,
            io.BytesIO(b""),
        )
        return _normalize_item(files_obj.entries[0])

    @async_retry(max_retries=3, base_delay=1.0)
    async def update_file(
        self,
        file_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        if new_name:

            updated = await self._run(
                self._client.files.update_file_by_id,
                file_id,
                name=new_name,
            )
            if not content_path:
                return _normalize_item(updated)

        if content_path:
            import os

            file_size = os.path.getsize(content_path)

            if file_size > 50 * 1024 * 1024:
                from box_sdk_gen.managers.uploads import UploadFileVersionAttributes

                attrs = UploadFileVersionAttributes(name=new_name or "")
                result = await self._chunked_upload_version(content_path, file_id, attrs, file_size)
            else:
                with open(content_path, "rb") as f:
                    files_obj = await self._run(
                        self._client.uploads.upload_file_version,
                        file_id,
                        f,
                    )
                result = files_obj.entries[0]
            return _normalize_item(result)

        # No content, no name — just return current metadata
        return await self.get_file(file_id)

    @async_retry(max_retries=3, base_delay=1.0)
    async def move_file(
        self,
        file_id: str,
        new_parent_id: str,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        from box_sdk_gen.managers.files import UpdateFileByIdParent

        kwargs: dict[str, Any] = {
            "parent": UpdateFileByIdParent(id=new_parent_id),
        }
        if new_name:
            kwargs["name"] = new_name

        updated = await self._run(
            self._client.files.update_file_by_id,
            file_id,
            **kwargs,
        )
        return _normalize_item(updated)

    @async_retry(max_retries=3, base_delay=1.0)
    async def delete_file(self, file_id: str) -> None:
        await self._run(self._client.files.delete_file_by_id, file_id)

    @async_retry(max_retries=3, base_delay=1.0)
    async def trash_file(self, file_id: str) -> dict[str, Any]:
        trashed = await self._run(self._client.trashed_files.trash_file, file_id)
        return _normalize_item(trashed)

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_about(self) -> dict[str, Any]:
        user = await self._run(self._client.users.get_user_me)
        return {
            "user": {
                "displayName": user.name,
                "emailAddress": getattr(user, "login", "unknown"),
            },
            "storageQuota": {
                "limit": str(getattr(user, "space_amount", 0)),
                "usage": str(getattr(user, "space_used", 0)),
            },
        }

    async def find_child_folder(self, parent_id: str, name: str) -> str | None:
        offset = 0
        while True:
            items_result = await self._run(
                self._client.folders.get_folder_items,
                parent_id,
                fields=["id", "type", "name"],
                offset=offset,
                limit=100,
            )
            for entry in items_result.entries:
                if entry.type == "folder" and entry.name == name:
                    return str(entry.id)
            offset += len(items_result.entries)
            if offset >= items_result.total_count:
                break
        return None

    async def list_all_recursive(
        self, folder_id: str = "0", prefix: str = ""
    ) -> list[dict[str, Any]]:
        items = await self.list_all_files(folder_id)
        result: list[dict[str, Any]] = []
        for item in items:
            rel = f"{prefix}/{item['name']}" if prefix else item["name"]
            item["relativePath"] = rel
            if item.get("mimeType") == "folder":
                result.append(item)
                children = await self.list_all_recursive(item["id"], rel)
                result.extend(children)
            else:
                result.append(item)
        return result

    # ── Chunked upload helpers ───────────────────────────────────────

    async def _chunked_upload(self, file_path: str, attrs: Any, file_size: int) -> Any:
        """Upload a large file using chunked upload sessions."""
        from box_sdk_gen.managers.chunked_uploads import CreateFileUploadSessionAttributes

        session_attrs = CreateFileUploadSessionAttributes(
            folder_id=attrs.parent.id,
            file_name=attrs.name,
            file_size=file_size,
        )
        session = await self._run(
            self._client.chunked_uploads.create_file_upload_session,
            session_attrs,
        )
        return await self._upload_chunks(session, file_path, file_size)

    async def _chunked_upload_version(
        self, file_path: str, file_id: str, attrs: Any, file_size: int
    ) -> Any:
        """Upload a new version of a large file using chunked upload."""
        session = await self._run(
            self._client.chunked_uploads.create_file_upload_session_for_existing_file,
            file_id,
            file_size=file_size,
        )
        return await self._upload_chunks(session, file_path, file_size)

    async def _upload_chunks(self, session: Any, file_path: str, file_size: int) -> Any:
        """Upload file in chunks and commit the session."""
        import hashlib

        part_size = session.part_size
        parts = []
        sha1 = hashlib.sha1()

        with open(file_path, "rb") as f:
            offset = 0
            while offset < file_size:
                chunk = f.read(part_size)
                sha1.update(chunk)
                chunk_size = len(chunk)
                content_range = f"bytes {offset}-{offset + chunk_size - 1}/{file_size}"

                part = await self._run(
                    self._client.chunked_uploads.upload_file_part,
                    session.id,
                    chunk,
                    content_range=content_range,
                )
                parts.append(part.part)
                offset += chunk_size

        import base64

        digest = base64.b64encode(sha1.digest()).decode("utf-8")

        from box_sdk_gen.managers.chunked_uploads import CreateFileUploadSessionCommitParts

        commit_parts = CreateFileUploadSessionCommitParts(parts=parts)
        files_obj = await self._run(
            self._client.chunked_uploads.create_file_upload_session_commit,
            session.id,
            commit_parts,
            digest=f"sha={digest}",
        )
        return files_obj.entries[0]
