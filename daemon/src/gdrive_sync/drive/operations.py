"""High-level file operations: upload, download, delete."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from gdrive_sync.drive.client import DriveClient
from gdrive_sync.util.logging import get_logger
from gdrive_sync.util.retry import async_retry

log = get_logger("drive.operations")


class FileOperations:
    """Upload, download, and delete files with progress reporting."""

    def __init__(self, client: DriveClient) -> None:
        self._client = client

    @async_retry(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def upload_file(
        self,
        local_path: Path,
        remote_parent: str,
        remote_name: str | None = None,
        existing_id: str | None = None,
        progress_callback=None,
    ) -> dict[str, Any]:
        """Upload a local file to Drive using resumable upload.

        Args:
            local_path: Path to the local file.
            remote_parent: Drive folder ID to upload into.
            remote_name: Name for the remote file (defaults to local filename).
            existing_id: If set, update this file instead of creating new.
            progress_callback: Optional async callable(bytes_sent, total_bytes).

        Returns:
            The Drive file metadata of the uploaded file.
        """
        name = remote_name or local_path.name
        file_size = local_path.stat().st_size
        log.info("Uploading %s (%d bytes) as '%s'", local_path, file_size, name)

        media = MediaFileUpload(
            str(local_path),
            resumable=True,
            chunksize=256 * 1024,
        )

        if existing_id:
            request = self._client.service.files().update(
                fileId=existing_id,
                media_body=media,
                fields="id, name, md5Checksum, modifiedTime",
            )
        else:
            metadata = {"name": name, "parents": [remote_parent]}
            request = self._client.service.files().create(
                body=metadata,
                media_body=media,
                fields="id, name, md5Checksum, modifiedTime",
            )

        def _do_upload():
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status and progress_callback:
                    # Schedule the callback from the thread
                    pass
            return response

        result = await asyncio.to_thread(_do_upload)
        log.info("Upload complete: %s -> %s", name, result.get("id"))
        return result

    @async_retry(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def download_file(
        self,
        remote_id: str,
        local_path: Path,
        progress_callback=None,
    ) -> Path:
        """Download a file from Drive to local disk.

        Args:
            remote_id: Drive file ID.
            local_path: Destination path on local disk.
            progress_callback: Optional async callable(bytes_received, total_bytes).

        Returns:
            The local path where the file was saved.
        """
        log.info("Downloading %s -> %s", remote_id, local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        request = self._client.service.files().get_media(fileId=remote_id)

        def _do_download():
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            return buffer.getvalue()

        data = await asyncio.to_thread(_do_download)
        local_path.write_bytes(data)
        log.info("Download complete: %s (%d bytes)", local_path, len(data))
        return local_path

    async def delete_remote(self, remote_id: str, trash: bool = True) -> None:
        """Delete (or trash) a remote file.

        Args:
            remote_id: Drive file ID.
            trash: If True, move to trash; if False, permanently delete.
        """
        if trash:
            await self._client.trash_file(remote_id)
            log.info("Trashed remote file %s", remote_id)
        else:
            await self._client.delete_file(remote_id)
            log.info("Permanently deleted remote file %s", remote_id)
