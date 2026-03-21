"""Dropbox CloudFileOps implementation."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import CloudFileOps
from cloud_drive_sync.providers.dropbox.client import DropboxClient, _metadata_to_dict
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.dropbox.operations")

# Dropbox upload session threshold: files larger than 150MB use upload sessions
_SESSION_THRESHOLD = 150 * 1024 * 1024
_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB chunks for upload sessions


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


class DropboxFileOps(CloudFileOps):
    """Upload, download, and delete files on Dropbox."""

    def __init__(self, client: DropboxClient) -> None:
        self._client = client

    @async_retry(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def upload_file(
        self,
        local_path: Path,
        remote_parent: str,
        remote_name: str | None = None,
        existing_id: str | None = None,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        import dropbox

        name = remote_name or local_path.name
        file_size = local_path.stat().st_size
        log.info("Uploading %s (%d bytes) as '%s'", local_path, file_size, name)

        if existing_id:
            # Update existing file at its current path
            dest_path = existing_id
            mode = dropbox.files.WriteMode.overwrite
        else:
            parent = "" if remote_parent in ("root", "") else remote_parent
            dest_path = f"{parent}/{name}"
            mode = dropbox.files.WriteMode.add

        start_time = time.monotonic()

        if file_size <= _SESSION_THRESHOLD:
            result = await self._upload_simple(local_path, dest_path, mode)
        else:
            result = await self._upload_session(
                local_path, dest_path, mode, file_size, progress_callback, start_time
            )

        elapsed = time.monotonic() - start_time
        avg_speed = file_size / elapsed if elapsed > 0 else 0
        metadata = _metadata_to_dict(result)
        log.info(
            "Upload complete: %s -> %s (%s at %s)",
            name, metadata["id"], _format_size(file_size), _format_speed(avg_speed),
        )
        return {
            **metadata,
            "_transfer_speed": avg_speed,
            "_transfer_size": file_size,
            "_transfer_elapsed": elapsed,
        }

    async def _upload_simple(self, local_path: Path, dest_path: str, mode: Any) -> Any:
        """Upload a small file in a single request."""
        with open(local_path, "rb") as f:
            content = f.read()
        return await self._client._run(
            self._client.dbx.files_upload, content, dest_path, mode=mode
        )

    async def _upload_session(
        self,
        local_path: Path,
        dest_path: str,
        mode: Any,
        file_size: int,
        progress_callback: Any,
        start_time: float,
    ) -> Any:
        """Upload a large file using upload sessions."""
        import dropbox

        dbx = self._client.dbx

        def _do_session_upload():
            with open(local_path, "rb") as f:
                chunk = f.read(_UPLOAD_CHUNK_SIZE)
                session = dbx.files_upload_session_start(chunk)
                cursor = dropbox.files.UploadSessionCursor(
                    session_id=session.session_id, offset=len(chunk)
                )
                uploaded = len(chunk)

                while True:
                    chunk = f.read(_UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break

                    if len(chunk) + cursor.offset >= file_size:
                        # Last chunk
                        break

                    dbx.files_upload_session_append_v2(chunk, cursor)
                    cursor.offset += len(chunk)
                    uploaded += len(chunk)

                    if progress_callback:
                        elapsed = time.monotonic() - start_time
                        speed = uploaded / elapsed if elapsed > 0 else 0
                        progress_callback(uploaded, file_size, speed)

                commit = dropbox.files.CommitInfo(path=dest_path, mode=mode)
                return dbx.files_upload_session_finish(chunk, cursor, commit)

        return await asyncio.to_thread(_do_session_upload)

    @async_retry(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def download_file(
        self,
        remote_id: str,
        local_path: Path,
        progress_callback: Any = None,
    ) -> tuple[Path, float, int, float]:
        log.info("Downloading %s -> %s", remote_id, local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        start_time = time.monotonic()

        def _do_download():
            fd, tmp_path = tempfile.mkstemp(
                dir=str(local_path.parent),
                prefix=f".{local_path.name}.",
                suffix=".tmp",
            )
            try:
                metadata, response = self._client.dbx.files_download(remote_id)
                with os.fdopen(fd, "wb") as tmp_file:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            tmp_file.write(chunk)
                os.replace(tmp_path, str(local_path))
                return metadata.size
            except BaseException:
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
        if trash:
            await self._client.trash_file(remote_id)
            log.info("Trashed remote file %s", remote_id)
        else:
            await self._client.delete_file(remote_id)
            log.info("Permanently deleted remote file %s", remote_id)
