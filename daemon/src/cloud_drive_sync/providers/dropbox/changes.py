"""Dropbox CloudChangePoller implementation."""

from __future__ import annotations

from typing import Any

from cloud_drive_sync.providers.base import CloudChangePoller
from cloud_drive_sync.providers.dropbox.client import DropboxClient
from cloud_drive_sync.providers.gdrive.changes import RemoteChange
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.dropbox.changes")


class DropboxChangePoller(CloudChangePoller):
    """Polls Dropbox for remote changes using the cursor-based listing API.

    Dropbox doesn't have a dedicated "changes" API like Google Drive.
    Instead, it uses the files_list_folder/continue pattern with cursors
    to detect changes since the last poll.
    """

    def __init__(self, client: DropboxClient, root_path: str = "") -> None:
        self._client = client
        self._root_path = root_path

    @async_retry(max_retries=3, base_delay=2.0)
    async def get_start_page_token(self) -> str:
        """Get the initial cursor by doing a full folder listing.

        We list the entire folder tree to establish a baseline cursor,
        then use that cursor to detect future changes.
        """
        result = await self._client._run(
            self._client.dbx.files_list_folder,
            self._root_path,
            recursive=True,
            limit=2000,
        )

        # Consume all pages to get the final cursor
        while result.has_more:
            result = await self._client._run(
                self._client.dbx.files_list_folder_continue,
                result.cursor,
            )

        cursor = result.cursor
        log.debug("Got initial Dropbox cursor: %s...", cursor[:40])
        return cursor

    @async_retry(max_retries=3, base_delay=2.0)
    async def poll_changes(self, page_token: str) -> tuple[list[RemoteChange], str]:
        """Poll for changes since the given cursor.

        First checks if there are changes using longpoll, then fetches them.
        """

        # Use list_folder_continue to get changes since cursor
        changes: list[RemoteChange] = []
        cursor = page_token

        result = await self._client._run(
            self._client.dbx.files_list_folder_continue, cursor
        )

        while True:
            for entry in result.entries:
                change = self._parse_entry(entry)
                if change is not None:
                    changes.append(change)

            cursor = result.cursor
            if not result.has_more:
                break

            result = await self._client._run(
                self._client.dbx.files_list_folder_continue, cursor
            )

        log.debug("Polled %d changes from Dropbox, new cursor: %s...", len(changes), cursor[:40])
        return changes, cursor

    @staticmethod
    def _parse_entry(entry: Any) -> RemoteChange | None:
        """Convert a Dropbox metadata entry to a RemoteChange."""
        import dropbox

        if isinstance(entry, dropbox.files.DeletedMetadata):
            path = entry.path_lower or entry.path_display
            return RemoteChange(
                file_id=path,
                file_name=entry.name,
                removed=True,
                trashed=False,
            )

        if isinstance(entry, dropbox.files.FileMetadata):
            path = entry.path_lower or entry.path_display
            # Derive parent path
            parent = "/".join(path.rsplit("/", 1)[:-1]) or ""
            return RemoteChange(
                file_id=path,
                file_name=entry.name,
                mime_type="application/octet-stream",
                md5=entry.content_hash,
                modified_time=entry.server_modified.isoformat() + "Z",
                removed=False,
                trashed=False,
                parents=[parent] if parent else [],
            )

        if isinstance(entry, dropbox.files.FolderMetadata):
            path = entry.path_lower or entry.path_display
            parent = "/".join(path.rsplit("/", 1)[:-1]) or ""
            return RemoteChange(
                file_id=path,
                file_name=entry.name,
                mime_type="folder",
                removed=False,
                trashed=False,
                parents=[parent] if parent else [],
            )

        return None
