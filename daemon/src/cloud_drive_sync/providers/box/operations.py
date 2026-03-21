"""Box CloudFileOps implementation."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import CloudFileOps
from cloud_drive_sync.providers.box.client import BoxClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.box.operations")

_CHUNKED_THRESHOLD = 50 * 1024 * 1024  # 50 MB


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


class BoxFileOps(CloudFileOps):
    """Upload, download, and delete files on Box."""

    def __init__(self, client: BoxClient) -> None:
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
        from box_sdk_gen.managers.uploads import (
            UploadFileAttributes,
            UploadFileAttributesParentField,
        )

        name = remote_name or local_path.name
        file_size = local_path.stat().st_size
        log.info("Uploading %s (%d bytes) as '%s'", local_path, file_size, name)

        start_time = time.monotonic()

        if existing_id:
            # Upload new version
            if file_size > _CHUNKED_THRESHOLD:
                from box_sdk_gen.managers.uploads import UploadFileVersionAttributes

                attrs = UploadFileVersionAttributes(name=name)
                result_item = await self._client._chunked_upload_version(
                    str(local_path), existing_id, attrs, file_size
                )
            else:
                with open(local_path, "rb") as f:
                    files_obj = await self._client._run(
                        self._client.client.uploads.upload_file_version,
                        existing_id,
                        f,
                    )
                result_item = files_obj.entries[0]
        else:
            # New file upload
            attrs = UploadFileAttributes(
                name=name,
                parent=UploadFileAttributesParentField(id=remote_parent),
            )
            if file_size > _CHUNKED_THRESHOLD:
                result_item = await self._client._chunked_upload(
                    str(local_path), attrs, file_size
                )
            else:
                with open(local_path, "rb") as f:
                    files_obj = await self._client._run(
                        self._client.client.uploads.upload_file,
                        attrs,
                        f,
                    )
                result_item = files_obj.entries[0]

        elapsed = time.monotonic() - start_time
        avg_speed = file_size / elapsed if elapsed > 0 else 0

        from cloud_drive_sync.providers.box.client import _normalize_item

        result = _normalize_item(result_item)
        log.info(
            "Upload complete: %s -> %s (%s at %s)",
            name, result.get("id"), _format_size(file_size), _format_speed(avg_speed),
        )
        return {
            **result,
            "_transfer_speed": avg_speed,
            "_transfer_size": file_size,
            "_transfer_elapsed": elapsed,
        }

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
            content = self._client.client.downloads.download_file(remote_id)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(local_path.parent),
                prefix=f".{local_path.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "wb") as tmp_file:
                    for chunk in content:
                        tmp_file.write(chunk)
                os.replace(tmp_path, str(local_path))
                return os.path.getsize(str(local_path))
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
