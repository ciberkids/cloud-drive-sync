"""OneDrive CloudChangePoller implementation using the Delta API."""

from __future__ import annotations

from typing import Any

from cloud_drive_sync.providers.base import CloudChangePoller
from cloud_drive_sync.providers.gdrive.changes import RemoteChange
from cloud_drive_sync.providers.onedrive.client import OneDriveClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.onedrive.changes")


class OneDriveChangePoller(CloudChangePoller):
    """Polls the OneDrive Delta API for remote modifications."""

    def __init__(self, client: OneDriveClient) -> None:
        self._client = client

    @async_retry(max_retries=3, base_delay=2.0)
    async def get_start_page_token(self) -> str:
        """Get the initial delta link by doing a full delta enumeration.

        We iterate through all pages until we get the @odata.deltaLink,
        which serves as our "start page token" for future change polling.
        """
        url = None
        path = "/me/drive/root/delta"
        params = {"$select": "id,name,file,folder,parentReference,lastModifiedDateTime,size,deleted"}

        while True:
            if url:
                result = await self._client._request("GET", url)
            else:
                result = await self._client._graph_get(path, params=params)

            next_link = result.get("@odata.nextLink")
            delta_link = result.get("@odata.deltaLink")

            if delta_link:
                log.debug("Got initial delta link")
                return delta_link
            elif next_link:
                url = next_link
            else:
                raise RuntimeError("Delta response contained neither nextLink nor deltaLink")

    @async_retry(max_retries=3, base_delay=2.0)
    async def poll_changes(self, page_token: str) -> tuple[list[RemoteChange], str]:
        """Poll for changes since the given delta link.

        Args:
            page_token: The @odata.deltaLink from the previous poll.

        Returns:
            (list_of_changes, new_delta_link)
        """
        changes: list[RemoteChange] = []
        url = page_token

        while True:
            result = await self._client._request("GET", url)

            for item in result.get("value", []):
                change = self._parse_change(item)
                changes.append(change)

            next_link = result.get("@odata.nextLink")
            delta_link = result.get("@odata.deltaLink")

            if delta_link:
                log.debug("Polled %d changes, got new delta link", len(changes))
                return changes, delta_link
            elif next_link:
                url = next_link
            else:
                raise RuntimeError("Delta response contained neither nextLink nor deltaLink")

    @staticmethod
    def _parse_change(item: dict[str, Any]) -> RemoteChange:
        """Parse a delta DriveItem into a RemoteChange."""
        # Detect removal: item has a "deleted" facet
        is_removed = "deleted" in item

        # Determine MIME type
        if "folder" in item:
            mime_type = "folder"
        elif item.get("file", {}).get("mimeType"):
            mime_type = item["file"]["mimeType"]
        else:
            mime_type = "application/octet-stream" if not is_removed else None

        # Extract quickXorHash
        quick_xor = None
        if "file" in item and "hashes" in item.get("file", {}):
            quick_xor = item["file"]["hashes"].get("quickXorHash")

        # Extract parent
        parents = []
        if "parentReference" in item and "id" in item.get("parentReference", {}):
            parents = [item["parentReference"]["id"]]

        return RemoteChange(
            file_id=item["id"],
            file_name=item.get("name"),
            mime_type=mime_type,
            md5=quick_xor,  # RemoteChange uses md5 field; we store quickXorHash here
            modified_time=item.get("lastModifiedDateTime"),
            removed=is_removed,
            trashed=is_removed,  # OneDrive DELETE = recycle bin
            parents=parents,
        )
