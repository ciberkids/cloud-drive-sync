"""Google Drive CloudClient implementation."""

from __future__ import annotations

import threading
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from cloud_drive_sync.providers.base import CloudClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.gdrive.client")

FIELDS_FILE = "id, name, mimeType, md5Checksum, modifiedTime, parents, size, trashed"

FOLDER_MIME = "application/vnd.google-apps.folder"

_GOOGLE_NATIVE_DOC_MIMES = frozenset(
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


class GoogleDriveClient(CloudClient):
    """Google Drive API v3 wrapper implementing CloudClient."""

    def __init__(self, credentials: Credentials, proxy=None) -> None:
        if proxy and (proxy.http_proxy or proxy.https_proxy):
            # Build a proxied HTTP transport
            from cloud_drive_sync.util.proxy import parse_proxy_url
            proxy_url = proxy.https_proxy or proxy.http_proxy
            proxy_info = parse_proxy_url(proxy_url)
            if proxy_info:
                import httplib2
                import google_auth_httplib2
                http = httplib2.Http(proxy_info=proxy_info)
                authed_http = google_auth_httplib2.AuthorizedHttp(credentials, http=http)
                self._service = build("drive", "v3", http=authed_http, cache_discovery=False)
            else:
                self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        else:
            self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        # httplib2 is not thread-safe; serialize all API calls that go
        # through asyncio.to_thread to prevent heap corruption / segfaults.
        self._api_lock = threading.Lock()

    # ── CloudClient capability properties ───────────────────────────

    @property
    def supports_trash(self) -> bool:
        return True

    @property
    def supports_export(self) -> bool:
        return True

    @property
    def hash_field(self) -> str:
        return "md5Checksum"

    @property
    def hash_algorithm(self) -> str:
        return "md5"

    @property
    def folder_mime_type(self) -> str | None:
        return FOLDER_MIME

    @property
    def native_doc_mimes(self) -> frozenset[str]:
        return _GOOGLE_NATIVE_DOC_MIMES

    # ── Internal helpers ────────────────────────────────────────────

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

    # ── CloudClient methods ─────────────────────────────────────────

    @async_retry(max_retries=3, base_delay=1.0)
    async def list_files(
        self,
        folder_id: str = "root",
        page_token: str | None = None,
        page_size: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
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
        metadata: dict[str, Any] = {"name": name, "parents": [parent_id]}
        if is_folder:
            metadata["mimeType"] = FOLDER_MIME
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
    async def move_file(
        self,
        file_id: str,
        new_parent_id: str,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        # Fetch current parents so we can remove the old one
        current = await self.get_file(file_id)
        old_parents = ",".join(current.get("parents", []))

        body: dict[str, Any] = {}
        if new_name:
            body["name"] = new_name

        request = self._service.files().update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=old_parents,
            body=body if body else None,
            fields=FIELDS_FILE,
            supportsAllDrives=True,
        )
        return await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def delete_file(self, file_id: str) -> None:
        request = self._service.files().delete(fileId=file_id, supportsAllDrives=True)
        await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def trash_file(self, file_id: str) -> dict[str, Any]:
        request = self._service.files().update(
            fileId=file_id, body={"trashed": True}, fields=FIELDS_FILE,
            supportsAllDrives=True,
        )
        return await self._execute(request)

    @async_retry(max_retries=3, base_delay=1.0)
    async def export_file(self, file_id: str, mime_type: str) -> bytes:
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
        request = self._service.about().get(fields="user, storageQuota")
        return await self._execute(request)

    async def find_child_folder(self, parent_id: str, name: str) -> str | None:
        query = (
            f"'{parent_id}' in parents "
            f"and name = '{name.replace(chr(39), chr(92) + chr(39))}' "
            f"and mimeType = '{FOLDER_MIME}' "
            f"and trashed = false"
        )
        result = await self.list_files(query=query, page_size=1)
        files = result.get("files", [])
        return files[0]["id"] if files else None

    @async_retry(max_retries=3, base_delay=1.0)
    async def list_shared_drives(self) -> list[dict[str, Any]]:
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

    async def list_all_recursive(
        self, folder_id: str = "root", prefix: str = ""
    ) -> list[dict[str, Any]]:
        items = await self.list_all_files(folder_id)
        result: list[dict[str, Any]] = []
        for item in items:
            rel = f"{prefix}/{item['name']}" if prefix else item["name"]
            item["relativePath"] = rel
            if item.get("mimeType") == FOLDER_MIME:
                result.append(item)
                children = await self.list_all_recursive(item["id"], rel)
                result.extend(children)
            else:
                result.append(item)
        return result
