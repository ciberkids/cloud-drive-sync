"""High-level file operations: upload, download, delete."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from gdrive_sync.drive.client import DriveClient
from gdrive_sync.util.logging import get_logger
from gdrive_sync.util.retry import async_retry

log = get_logger("drive.operations")


def _format_speed(bytes_per_sec: float) -> str:
    """Format bytes/sec as a human-readable string."""
    if bytes_per_sec >= 1_000_000:
        return f"{bytes_per_sec / 1_000_000:.1f} MB/s"
    elif bytes_per_sec >= 1_000:
        return f"{bytes_per_sec / 1_000:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def _format_size(size_bytes: int) -> str:
    """Format bytes as a human-readable string."""
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / 1_000_000_000:.1f} GB"
    elif size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.1f} MB"
    elif size_bytes >= 1_000:
        return f"{size_bytes / 1_000:.1f} KB"
    return f"{size_bytes} B"


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

        start_time = time.monotonic()
        loop = asyncio.get_running_loop()

        def _do_upload():
            with self._client._api_lock:
                response = None
                while response is None:
                    status, response = request.next_chunk()
                    if status and progress_callback:
                        elapsed = time.monotonic() - start_time
                        bytes_sent = int(status.resumable_progress)
                        speed = bytes_sent / elapsed if elapsed > 0 else 0
                        loop.call_soon_threadsafe(
                            progress_callback, bytes_sent, file_size, speed
                        )
                return response

        result = await asyncio.to_thread(_do_upload)
        elapsed = time.monotonic() - start_time
        avg_speed = file_size / elapsed if elapsed > 0 else 0
        log.info(
            "Upload complete: %s -> %s (%s at %s)",
            name, result.get("id"), _format_size(file_size), _format_speed(avg_speed),
        )
        return {**result, "_transfer_speed": avg_speed, "_transfer_size": file_size, "_transfer_elapsed": elapsed}

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

        start_time = time.monotonic()
        loop = asyncio.get_running_loop()

        def _do_download():
            """Stream download to a temp file, then atomically rename."""
            with self._client._api_lock:
                # Write to a temp file in the same directory for atomic rename
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(local_path.parent),
                    prefix=f".{local_path.name}.",
                    suffix=".tmp",
                )
                try:
                    with os.fdopen(fd, "wb") as tmp_file:
                        downloader = MediaIoBaseDownload(tmp_file, request)
                        done = False
                        while not done:
                            status, done = downloader.next_chunk()
                            if status and progress_callback:
                                elapsed = time.monotonic() - start_time
                                bytes_received = int(status.resumable_progress)
                                speed = bytes_received / elapsed if elapsed > 0 else 0
                                loop.call_soon_threadsafe(
                                    progress_callback, bytes_received, 0, speed
                                )
                    # Atomic rename to final destination
                    os.replace(tmp_path, str(local_path))
                    return os.path.getsize(str(local_path))
                except BaseException:
                    # Clean up temp file on any failure
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise

        size = await asyncio.to_thread(_do_download)
        elapsed = time.monotonic() - start_time
        avg_speed = size / elapsed if elapsed > 0 else 0
        log.info(
            "Download complete: %s (%s at %s)",
            local_path, _format_size(size), _format_speed(avg_speed),
        )
        return local_path, avg_speed, size, elapsed

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
