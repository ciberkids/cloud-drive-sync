"""OneDrive CloudClient implementation using Microsoft Graph API."""

from __future__ import annotations

import asyncio
from typing import Any

from cloud_drive_sync.providers.base import CloudClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.onedrive.client")

# Graph API base URL
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Fields to select when querying DriveItems
SELECT_FIELDS = "id,name,file,folder,parentReference,lastModifiedDateTime,size"

# Upload session threshold: files > 4MB use upload sessions
UPLOAD_SESSION_THRESHOLD = 4 * 1024 * 1024


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Graph DriveItem to a provider-agnostic dict."""
    # Determine MIME type: 'folder' if folder facet present, else actual or fallback
    if "folder" in item:
        mime_type = "folder"
    elif item.get("file", {}).get("mimeType"):
        mime_type = item["file"]["mimeType"]
    else:
        mime_type = "application/octet-stream"

    # Extract quickXorHash from file.hashes
    quick_xor = None
    if "file" in item and "hashes" in item.get("file", {}):
        quick_xor = item["file"]["hashes"].get("quickXorHash")

    # Extract parent ID
    parents = []
    if "parentReference" in item and "id" in item["parentReference"]:
        parents = [item["parentReference"]["id"]]

    return {
        "id": item["id"],
        "name": item.get("name", ""),
        "mimeType": mime_type,
        "quickXorHash": quick_xor,
        "modifiedTime": item.get("lastModifiedDateTime"),
        "size": item.get("size", 0),
        "parents": parents,
    }


class OneDriveClient(CloudClient):
    """Microsoft OneDrive API wrapper implementing CloudClient via Microsoft Graph."""

    def __init__(self, credential: Any) -> None:
        """Initialize with an azure-identity credential object."""
        self._credential = credential
        self._access_token: str | None = None
        self._token_expires: float = 0

    # ── CloudClient capability properties ───────────────────────────

    @property
    def supports_trash(self) -> bool:
        return True

    @property
    def supports_export(self) -> bool:
        return False

    @property
    def hash_field(self) -> str:
        return "quickXorHash"

    @property
    def hash_algorithm(self) -> str:
        return "quickxor"

    @property
    def folder_mime_type(self) -> str | None:
        return None

    @property
    def native_doc_mimes(self) -> frozenset[str]:
        return frozenset()

    # ── HTTP helpers ────────────────────────────────────────────────

    async def _get_token(self) -> str:
        """Get a valid access token, refreshing if necessary."""
        import time

        if self._access_token and time.monotonic() < self._token_expires:
            return self._access_token

        def _acquire():
            return self._credential.get_token("https://graph.microsoft.com/.default")

        token_result = await asyncio.to_thread(_acquire)
        self._access_token = token_result.token
        # Expire 60s early to avoid edge cases
        self._token_expires = time.monotonic() + max(token_result.expires_on - time.time() - 60, 0)
        return self._access_token

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        stream: bool = False,
    ) -> Any:
        """Make an authenticated request to the Graph API."""
        import httpx

        token = await self._get_token()
        req_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            req_headers.update(headers)

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.request(
                method,
                url,
                json=json,
                content=data,
                headers=req_headers,
                params=params,
            )
            resp.raise_for_status()
            if resp.status_code == 204:
                return None
            if stream or not resp.content:
                return resp
            return resp.json()

    async def _graph_get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """GET from Graph API."""
        return await self._request("GET", f"{GRAPH_BASE}{path}", params=params)

    async def _graph_post(self, path: str, json: dict | None = None, **kwargs) -> Any:
        """POST to Graph API."""
        return await self._request("POST", f"{GRAPH_BASE}{path}", json=json, **kwargs)

    async def _graph_patch(self, path: str, json: dict | None = None, **kwargs) -> Any:
        """PATCH to Graph API."""
        return await self._request("PATCH", f"{GRAPH_BASE}{path}", json=json, **kwargs)

    async def _graph_delete(self, path: str) -> None:
        """DELETE via Graph API."""
        await self._request("DELETE", f"{GRAPH_BASE}{path}")

    async def _download_content(self, url: str) -> bytes:
        """Download raw bytes from a URL."""
        import httpx

        token = await self._get_token()
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            return resp.content

    # ── CloudClient methods ─────────────────────────────────────────

    @async_retry(max_retries=3, base_delay=1.0)
    async def list_files(
        self,
        folder_id: str = "root",
        page_token: str | None = None,
        page_size: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        if page_token:
            # page_token is a full @odata.nextLink URL
            result = await self._request("GET", page_token)
        else:
            if folder_id == "root":
                path = "/me/drive/root/children"
            else:
                path = f"/me/drive/items/{folder_id}/children"
            params = {
                "$select": SELECT_FIELDS,
                "$top": str(page_size),
            }
            result = await self._graph_get(path, params=params)

        files = [_normalize_item(item) for item in result.get("value", [])]
        response: dict[str, Any] = {"files": files}

        next_link = result.get("@odata.nextLink")
        if next_link:
            response["nextPageToken"] = next_link

        return response

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_file(self, file_id: str) -> dict[str, Any]:
        if file_id == "root":
            path = "/me/drive/root"
        else:
            path = f"/me/drive/items/{file_id}"
        params = {"$select": SELECT_FIELDS}
        item = await self._graph_get(path, params=params)
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
        if is_folder:
            parent = "root" if parent_id == "root" else parent_id
            if parent == "root":
                path = "/me/drive/root/children"
            else:
                path = f"/me/drive/items/{parent}/children"
            body = {
                "name": name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            }
            item = await self._graph_post(path, json=body)
            return _normalize_item(item)
        else:
            if not content_path:
                # Create empty file via simple upload
                if parent_id == "root":
                    path = f"/me/drive/root:/{name}:/content"
                else:
                    path = f"/me/drive/items/{parent_id}:/{name}:/content"
                item = await self._request(
                    "PUT",
                    f"{GRAPH_BASE}{path}",
                    data=b"",
                    headers={"Content-Type": mime_type or "application/octet-stream"},
                )
                return _normalize_item(item)

            return await self._upload_content(
                content_path, name=name, parent_id=parent_id, mime_type=mime_type
            )

    async def _upload_content(
        self,
        content_path: str,
        *,
        name: str,
        parent_id: str | None = None,
        file_id: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Upload file content, using simple upload for small files and upload sessions for large."""
        import os

        file_size = os.path.getsize(content_path)

        if file_size <= UPLOAD_SESSION_THRESHOLD:
            return await self._simple_upload(
                content_path, name=name, parent_id=parent_id, file_id=file_id, mime_type=mime_type
            )
        else:
            return await self._session_upload(
                content_path,
                file_size=file_size,
                name=name,
                parent_id=parent_id,
                file_id=file_id,
            )

    async def _simple_upload(
        self,
        content_path: str,
        *,
        name: str,
        parent_id: str | None = None,
        file_id: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Upload a small file (< 4MB) via PUT."""

        def _read():
            with open(content_path, "rb") as f:
                return f.read()

        data = await asyncio.to_thread(_read)

        if file_id:
            path = f"/me/drive/items/{file_id}/content"
        elif parent_id == "root":
            path = f"/me/drive/root:/{name}:/content"
        else:
            path = f"/me/drive/items/{parent_id}:/{name}:/content"

        item = await self._request(
            "PUT",
            f"{GRAPH_BASE}{path}",
            data=data,
            headers={"Content-Type": mime_type or "application/octet-stream"},
        )
        return _normalize_item(item)

    async def _session_upload(
        self,
        content_path: str,
        *,
        file_size: int,
        name: str,
        parent_id: str | None = None,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        """Upload a large file (> 4MB) via an upload session."""
        import httpx

        # Create upload session
        if file_id:
            path = f"/me/drive/items/{file_id}/createUploadSession"
        elif parent_id == "root":
            path = f"/me/drive/root:/{name}:/createUploadSession"
        else:
            path = f"/me/drive/items/{parent_id}:/{name}:/createUploadSession"

        session_body = {
            "item": {
                "@microsoft.graph.conflictBehavior": "replace",
                "name": name,
            }
        }
        session = await self._graph_post(path, json=session_body)
        upload_url = session["uploadUrl"]

        # Upload in 10MB chunks
        chunk_size = 10 * 1024 * 1024
        token = await self._get_token()

        async with httpx.AsyncClient(timeout=300) as client:
            offset = 0
            with open(content_path, "rb") as f:
                while offset < file_size:
                    chunk = f.read(chunk_size)
                    end = offset + len(chunk) - 1
                    headers = {
                        "Content-Range": f"bytes {offset}-{end}/{file_size}",
                        "Content-Length": str(len(chunk)),
                        "Authorization": f"Bearer {token}",
                    }
                    resp = await client.put(upload_url, content=chunk, headers=headers)
                    resp.raise_for_status()
                    offset += len(chunk)

                    if resp.status_code in (200, 201):
                        # Upload complete
                        return _normalize_item(resp.json())

        # Should not reach here, but handle gracefully
        raise RuntimeError("Upload session completed without a final response")

    @async_retry(max_retries=3, base_delay=1.0)
    async def update_file(
        self,
        file_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        if new_name:
            await self._graph_patch(f"/me/drive/items/{file_id}", json={"name": new_name})

        if content_path:
            return await self._upload_content(
                content_path, name=new_name or "", file_id=file_id, mime_type=mime_type
            )

        # If only rename, re-fetch
        return await self.get_file(file_id)

    @async_retry(max_retries=3, base_delay=1.0)
    async def delete_file(self, file_id: str) -> None:
        await self._graph_delete(f"/me/drive/items/{file_id}")

    @async_retry(max_retries=3, base_delay=1.0)
    async def trash_file(self, file_id: str) -> dict[str, Any]:
        # OneDrive: PATCH the item to set it as deleted (move to recycle bin)
        # The Graph API DELETE on an item actually moves to recycle bin by default
        await self._graph_delete(f"/me/drive/items/{file_id}")
        # Return a minimal representation since the item is now in the recycle bin
        return {"id": file_id, "trashed": True}

    @async_retry(max_retries=3, base_delay=1.0)
    async def get_about(self) -> dict[str, Any]:
        drive = await self._graph_get("/me/drive")
        user = await self._graph_get("/me")
        return {
            "user": {
                "emailAddress": user.get("mail") or user.get("userPrincipalName", "unknown"),
                "displayName": user.get("displayName", ""),
            },
            "storageQuota": {
                "limit": drive.get("quota", {}).get("total", 0),
                "usage": drive.get("quota", {}).get("used", 0),
            },
        }

    async def find_child_folder(self, parent_id: str, name: str) -> str | None:
        if parent_id == "root":
            path = "/me/drive/root/children"
        else:
            path = f"/me/drive/items/{parent_id}/children"

        # Use $filter to find by name, then check for folder facet
        safe_name = name.replace("'", "''")
        params = {
            "$filter": f"name eq '{safe_name}'",
            "$select": "id,name,folder",
        }
        result = await self._graph_get(path, params=params)
        for item in result.get("value", []):
            if "folder" in item:
                return item["id"]
        return None

    async def list_all_recursive(
        self, folder_id: str = "root", prefix: str = ""
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
