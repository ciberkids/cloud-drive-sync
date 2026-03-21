"""Google Drive API v3 wrapper."""

from __future__ import annotations

import threading
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from gdrive_sync.util.logging import get_logger
from gdrive_sync.util.retry import async_retry

log = get_logger("drive.client")

FIELDS_FILE = "id, name, mimeType, md5Checksum, modifiedTime, parents, size, trashed"


class DriveClient:
    """Thin wrapper around the Google Drive API v3."""

    def __init__(self, credentials: Credentials) -> None:
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        # httplib2 is not thread-safe; serialize all API calls that go
        # through asyncio.to_thread to prevent heap corruption / segfaults.
        self._api_lock = threading.Lock()

    @property
    def service(self):
        return self._service

    async def _execute(self, request):
        """Execute a Drive API request, serialized to avoid httplib2 thread-safety issues."""
        import asyncio

        def _run():
            with self._api_lock:
                return request.execute()

        return await asyncio.to_thread(_run)

    @async_retry(max_retries=3, base_delay=1.0)
    async def list_files(
        self,
        folder_id: str = "root",
        page_token: str | None = None,
        page_size: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        """List files in a folder.

        Returns a dict with 'files' list and optional 'nextPageToken'.
        """
        q = query or f"'{folder_id}' in parents and trashed = false"
        request = self._service.files().list(
            q=q,
            pageSize=page_size,
            fields=f"nextPageToken, files({FIELDS_FILE})",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        return await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_file(self, file_id: str) -> dict[str, Any]:
        """Get metadata for a single file."""
        request = self._service.files().get(fileId=file_id, fields=FIELDS_FILE, supportsAllDrives=True)
        return await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def create_file(
        self,
        name: str,
        parent_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        is_folder: bool = False,
    ) -> dict[str, Any]:
        """Create a file or folder on Drive."""
        metadata: dict[str, Any] = {"name": name, "parents": [parent_id]}
        if is_folder:
            metadata["mimeType"] = "application/vnd.google-apps.folder"
            request = self._service.files().create(body=metadata, fields=FIELDS_FILE, supportsAllDrives=True)
        else:
            media = None
            if content_path:
                media = MediaFileUpload(
                    content_path,
                    mimetype=mime_type or "application/octet-stream",
                    resumable=True,
                )
            request = self._service.files().create(
                body=metadata, media_body=media, fields=FIELDS_FILE, supportsAllDrives=True
            )
        return await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def update_file(
        self,
        file_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        """Update a file's content and/or metadata."""
        body: dict[str, Any] = {}
        if new_name:
            body["name"] = new_name

        media = None
        if content_path:
            media = MediaFileUpload(
                content_path,
                mimetype=mime_type or "application/octet-stream",
                resumable=True,
            )

        request = self._service.files().update(
            fileId=file_id, body=body if body else None, media_body=media, fields=FIELDS_FILE,
            supportsAllDrives=True,
        )
        return await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def delete_file(self, file_id: str) -> None:
        """Permanently delete a file (bypass trash)."""
        request = self._service.files().delete(fileId=file_id, supportsAllDrives=True)
        await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def trash_file(self, file_id: str) -> dict[str, Any]:
        """Move a file to trash."""
        request = self._service.files().update(
            fileId=file_id, body={"trashed": True}, fields=FIELDS_FILE,
            supportsAllDrives=True,
        )
        return await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def export_file(self, file_id: str, mime_type: str) -> bytes:
        """Export a Google Docs/Sheets/Slides file to a specific mime type."""
        import asyncio
        import io

        request = self._service.files().export_media(fileId=file_id, mimeType=mime_type)

        def _download():
            with self._api_lock:
                buffer = io.BytesIO()
                downloader = MediaIoBaseDownload(buffer, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                return buffer.getvalue()

        return await asyncio.to_thread(_download)

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_about(self) -> dict[str, Any]:
        """Get storage quota and user info."""
        request = self._service.about().get(fields="user, storageQuota")
        return await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def list_shared_drives(self) -> list[dict[str, Any]]:
        """List all Shared Drives the user has access to."""
        all_drives: list[dict[str, Any]] = []
        page_token = None
        while True:
            request = self._service.drives().list(
                pageSize=100,
                fields="nextPageToken, drives(id, name)",
                pageToken=page_token,
            )
            result = await self._execute(request)
            all_drives.extend(result.get("drives", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return all_drives

    async def list_all_files(self, folder_id: str = "root") -> list[dict[str, Any]]:
        """List all files in a folder, handling pagination."""
        all_files: list[dict[str, Any]] = []
        page_token = None
        while True:
            result = await self.list_files(folder_id=folder_id, page_token=page_token)
            all_files.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return all_files

    async def list_all_recursive(
        self, folder_id: str = "root", prefix: str = ""
    ) -> list[dict[str, Any]]:
        """Recursively list all files and folders, adding a 'relativePath' field."""
        items = await self.list_all_files(folder_id)
        result: list[dict[str, Any]] = []
        for item in items:
            rel = f"{prefix}/{item['name']}" if prefix else item["name"]
            item["relativePath"] = rel
            if item.get("mimeType") == "application/vnd.google-apps.folder":
                result.append(item)
                children = await self.list_all_recursive(item["id"], rel)
                result.extend(children)
            else:
                result.append(item)
        return result
