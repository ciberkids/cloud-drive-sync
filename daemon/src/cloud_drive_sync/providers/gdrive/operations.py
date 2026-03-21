"""Google Drive CloudFileOps implementation."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from cloud_drive_sync.providers.base import CloudFileOps
from cloud_drive_sync.providers.gdrive.client import GoogleDriveClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.gdrive.operations")


def _format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec >= 1_000_000:
        return f"{bytes_per_sec / 1_000_000:.1f} MB/s"
    elif bytes_per_sec >= 1_000:
        return f"{bytes_per_sec / 1_000:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / 1_000_000_000:.1f} GB"
    elif size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.1f} MB"
    elif size_bytes >= 1_000:
        return f"{size_bytes / 1_000:.1f} KB"
    return f"{size_bytes} B"


class GoogleDriveFileOps(CloudFileOps):
    """Upload, download, and delete files on Google Drive."""

    def __init__(
        self,
        client: GoogleDriveClient,
        upload_throttle=None,
        download_throttle=None,
    ) -> None:
        self._client = client
        self._upload_throttle = upload_throttle
        self._download_throttle = download_throttle

    @async_retry(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def upload_file(
        self,
        local_path: Path,
        remote_parent: str,
        remote_name: str | None = None,
        existing_id: str | None = None,
        progress_callback=None,
        resume_uri: str | None = None,
    ) -> dict[str, Any]:
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
                supportsAllDrives=True,
            )
        else:
            metadata = {"name": name, "parents": [remote_parent]}
            request = self._client.service.files().create(
                body=metadata,
                media_body=media,
                fields="id, name, md5Checksum, modifiedTime",
                supportsAllDrives=True,
            )

        # If a resume URI was provided, set it on the request so the Google
        # API client picks up where it left off.
        if resume_uri:
            request.resumable_uri = resume_uri
            log.info("Resuming upload from URI: %s", resume_uri)

        start_time = time.monotonic()
        loop = asyncio.get_running_loop()

        upload_throttle = self._upload_throttle
        # Mutable container so the inner function can expose the resumable URI
        _resume_state: dict[str, str | None] = {"uri": resume_uri}

        def _do_upload():
            with self._client._api_lock:
                response = None
                while response is None:
                    status, response = request.next_chunk()
                    # Capture the resumable URI after the first chunk for
                    # potential future resume.
                    if request.resumable_uri and not _resume_state["uri"]:
                        _resume_state["uri"] = request.resumable_uri
                    chunk_bytes = 256 * 1024  # chunksize used above
                    if upload_throttle:
                        delay = upload_throttle.sleep_duration(chunk_bytes)
                        if delay > 0:
                            time.sleep(delay)
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
        resume_from: int = 0,
        temp_path: str | None = None,
    ) -> tuple[Path, float, int, float]:
        log.info("Downloading %s -> %s", remote_id, local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        request = self._client.service.files().get_media(fileId=remote_id, supportsAllDrives=True)

        # If resuming, add Range header so we only fetch remaining bytes
        if resume_from > 0:
            request.headers["Range"] = f"bytes={resume_from}-"
            log.info("Resuming download from byte %d", resume_from)

        start_time = time.monotonic()
        loop = asyncio.get_running_loop()

        download_throttle = self._download_throttle

        def _do_download():
            with self._client._api_lock:
                # If resuming and we have a temp_path, append to it;
                # otherwise create a new temp file.
                if resume_from > 0 and temp_path and os.path.exists(temp_path):
                    fd = os.open(temp_path, os.O_WRONLY | os.O_APPEND)
                    used_tmp_path = temp_path
                else:
                    fd, used_tmp_path = tempfile.mkstemp(
                        dir=str(local_path.parent),
                        prefix=f".{local_path.name}.",
                        suffix=".tmp",
                    )
                try:
                    with os.fdopen(fd, "ab" if (resume_from > 0 and temp_path) else "wb") as tmp_file:
                        downloader = MediaIoBaseDownload(tmp_file, request)
                        done = False
                        while not done:
                            status, done = downloader.next_chunk()
                            chunk_bytes = int(status.resumable_progress) if status else 0
                            if download_throttle and chunk_bytes > 0:
                                delay = download_throttle.sleep_duration(chunk_bytes)
                                if delay > 0:
                                    time.sleep(delay)
                            if status and progress_callback:
                                elapsed = time.monotonic() - start_time
                                bytes_received = resume_from + int(status.resumable_progress)
                                speed = bytes_received / elapsed if elapsed > 0 else 0
                                loop.call_soon_threadsafe(
                                    progress_callback, bytes_received, 0, speed
                                )
                    os.replace(used_tmp_path, str(local_path))
                    return os.path.getsize(str(local_path))
                except BaseException:
                    # Don't delete the temp file if resuming — it may be
                    # reused on the next attempt.
                    if not (resume_from > 0 and temp_path):
                        try:
                            os.unlink(used_tmp_path)
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
        if trash:
            await self._client.trash_file(remote_id)
            log.info("Trashed remote file %s", remote_id)
        else:
            await self._client.delete_file(remote_id)
            log.info("Permanently deleted remote file %s", remote_id)
