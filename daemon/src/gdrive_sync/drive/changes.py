"""Remote change polling via the Drive Changes API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from gdrive_sync.drive.client import DriveClient
from gdrive_sync.util.logging import get_logger
from gdrive_sync.util.retry import async_retry

log = get_logger("drive.changes")


@dataclass
class RemoteChange:
    """A single change detected from the Drive Changes API."""

    file_id: str
    file_name: str | None = None
    mime_type: str | None = None
    md5: str | None = None
    modified_time: str | None = None
    removed: bool = False
    trashed: bool = False
    parents: list[str] = field(default_factory=list)


class ChangePoller:
    """Polls the Drive Changes API for remote modifications."""

    def __init__(self, client: DriveClient) -> None:
        self._client = client

    @async_retry(max_retries=3, base_delay=2.0)
    async def get_start_page_token(self) -> str:
        """Get the initial change token to start polling from."""
        request = self._client.service.changes().getStartPageToken(
            supportsAllDrives=True,
        )
        result = await self._client._execute(request)
        token = result["startPageToken"]
        log.debug("Got start page token: %s", token)
        return token

    @async_retry(max_retries=3, base_delay=2.0)
    async def poll_changes(self, page_token: str) -> tuple[list[RemoteChange], str]:
        """Poll for changes since the given page token.

        Returns:
            Tuple of (list of changes, new page token).
        """
        changes: list[RemoteChange] = []
        current_token = page_token

        while True:
            request = self._client.service.changes().list(
                pageToken=current_token,
                spaces="drive",
                includeRemoved=True,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                fields="nextPageToken, newStartPageToken, changes("
                "fileId, removed, file(id, name, mimeType, md5Checksum, "
                "modifiedTime, parents, trashed))",
                pageSize=100,
            )
            result = await self._client._execute(request)

            for change_data in result.get("changes", []):
                change = self._parse_change(change_data)
                changes.append(change)

            if "newStartPageToken" in result:
                current_token = result["newStartPageToken"]
                break
            current_token = result["nextPageToken"]

        log.debug("Polled %d changes, new token: %s", len(changes), current_token)
        return changes, current_token

    @staticmethod
    def _parse_change(data: dict[str, Any]) -> RemoteChange:
        file_data = data.get("file", {})
        return RemoteChange(
            file_id=data["fileId"],
            file_name=file_data.get("name"),
            mime_type=file_data.get("mimeType"),
            md5=file_data.get("md5Checksum"),
            modified_time=file_data.get("modifiedTime"),
            removed=data.get("removed", False),
            trashed=file_data.get("trashed", False),
            parents=file_data.get("parents", []),
        )

    async def poll_loop(
        self,
        page_token: str,
        interval: float,
        callback,
        stop_event: asyncio.Event,
    ) -> str:
        """Continuously poll for changes at the given interval.

        Args:
            page_token: Starting page token.
            interval: Seconds between polls.
            callback: Async callable receiving (changes, new_token).
            stop_event: Event to signal stopping.

        Returns:
            The last page token when stopped.
        """
        current_token = page_token
        while not stop_event.is_set():
            try:
                changes, current_token = await self.poll_changes(current_token)
                if changes:
                    await callback(changes, current_token)
            except Exception:
                log.exception("Error during change polling")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

        return current_token
