"""Box CloudChangePoller implementation using the Events API."""

from __future__ import annotations

from typing import Any

from cloud_drive_sync.drive.changes import RemoteChange
from cloud_drive_sync.providers.base import CloudChangePoller
from cloud_drive_sync.providers.box.client import BoxClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.box.changes")

# Box event types that indicate file/folder mutations
_FILE_CHANGE_EVENTS = frozenset({
    "ITEM_CREATE",
    "ITEM_UPLOAD",
    "ITEM_MOVE",
    "ITEM_COPY",
    "ITEM_RENAME",
    "ITEM_TRASH",
    "ITEM_UNDELETE_VIA_TRASH",
})


class BoxChangePoller(CloudChangePoller):
    """Polls the Box Events API for remote modifications."""

    def __init__(self, client: BoxClient) -> None:
        self._client = client

    @async_retry(max_retries=3, base_delay=2.0)
    async def get_start_page_token(self) -> str:
        events = await self._client._run(
            self._client.client.events.get_events,
            stream_type="changes",
            limit=0,
        )
        token = str(events.next_stream_position)
        log.debug("Got start stream position: %s", token)
        return token

    @async_retry(max_retries=3, base_delay=2.0)
    async def poll_changes(self, page_token: str) -> tuple[list[RemoteChange], str]:
        changes: list[RemoteChange] = []

        events = await self._client._run(
            self._client.client.events.get_events,
            stream_type="changes",
            stream_position=page_token,
            limit=500,
        )

        for event in events.entries:
            event_type = getattr(event, "event_type", None)
            if event_type not in _FILE_CHANGE_EVENTS:
                continue

            source = getattr(event, "source", None)
            if source is None:
                continue

            change = self._parse_event(event_type, source)
            if change:
                changes.append(change)

        new_token = str(events.next_stream_position)
        log.debug("Polled %d changes, new stream position: %s", len(changes), new_token)
        return changes, new_token

    @staticmethod
    def _parse_event(event_type: str, source: Any) -> RemoteChange | None:
        source_type = getattr(source, "type", None)
        if source_type not in ("file", "folder"):
            return None

        is_trashed = event_type == "ITEM_TRASH"
        is_removed = False  # Box uses trash, not permanent removal via events

        modified = getattr(source, "content_modified_at", None) or getattr(source, "modified_at", None)
        if modified and hasattr(modified, "isoformat"):
            modified = modified.isoformat()

        mime_type = "folder" if source_type == "folder" else None
        parents = [str(source.parent.id)] if getattr(source, "parent", None) else []

        return RemoteChange(
            file_id=str(source.id),
            file_name=getattr(source, "name", None),
            mime_type=mime_type,
            md5=getattr(source, "sha1", None),
            modified_time=modified,
            removed=is_removed,
            trashed=is_trashed,
            parents=parents,
        )
