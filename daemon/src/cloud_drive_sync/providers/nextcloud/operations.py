"""Nextcloud CloudFileOps implementation."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import CloudFileOps
from cloud_drive_sync.providers.nextcloud.client import NextcloudClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.nextcloud.operations")


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


class NextcloudFileOps(CloudFileOps):
    """Upload, download, and delete files on Nextcloud via WebDAV."""

    def __init__(self, client: NextcloudClient) -> None:
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
        name = remote_name or local_path.name
        file_size = local_path.stat().st_size
        log.info("Uploading %s (%d bytes) as '%s'", local_path, file_size, name)

        if existing_id:
            # Update existing file — resolve its current path from fileid
            def _get_path():
                node = self._client._nc.files.by_id(int(existing_id))
                if node is None:
                    raise FileNotFoundError(f"Nextcloud file not found: fileid={existing_id}")
                return node.user_path

            remote_path = await asyncio.to_thread(_get_path)
        else:
            parent = "/" if remote_parent == "root" else self._client._normalise_path(remote_parent)
            remote_path = f"{parent}/{name}" if parent != "/" else f"/{name}"

        start_time = time.monotonic()

        def _upload():
            self._client._nc.files.upload(remote_path, str(local_path))
            # Re-list to get the uploaded file's metadata
            parent_dir = remote_path.rsplit("/", 1)[0] or "/"
            nodes = self._client._nc.files.listdir(parent_dir)
            target_name = remote_path.rsplit("/", 1)[-1]
            for node in nodes:
                if node.name == target_name:
                    return node
            return None

        result_node = await asyncio.to_thread(_upload)
        elapsed = time.monotonic() - start_time
        avg_speed = file_size / elapsed if elapsed > 0 else 0

        if result_node is None:
            raise RuntimeError(f"Upload succeeded but could not find file: {remote_path}")

        metadata = self._client._file_to_dict(result_node)
        log.info(
            "Upload complete: %s -> %s (%s at %s)",
            name,
            metadata.get("id"),
            _format_size(file_size),
            _format_speed(avg_speed),
        )
        return {
            **metadata,
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
        log.info("Downloading fileid=%s -> %s", remote_id, local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        start_time = time.monotonic()

        def _download():
            node = self._client._nc.files.by_id(int(remote_id))
            if node is None:
                raise FileNotFoundError(f"Nextcloud file not found: fileid={remote_id}")

            remote_path = node.user_path

            fd, tmp_path = tempfile.mkstemp(
                dir=str(local_path.parent),
                prefix=f".{local_path.name}.",
                suffix=".tmp",
            )
            try:
                data = self._client._nc.files.download(remote_path)
                with os.fdopen(fd, "wb") as tmp_file:
                    if isinstance(data, bytes):
                        tmp_file.write(data)
                    else:
                        for chunk in data:
                            tmp_file.write(chunk)
                os.replace(tmp_path, str(local_path))
                return os.path.getsize(str(local_path))
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        size = await asyncio.to_thread(_download)
        elapsed = time.monotonic() - start_time
        avg_speed = size / elapsed if elapsed > 0 else 0

        log.info(
            "Download complete: %s (%s at %s)",
            local_path,
            _format_size(size),
            _format_speed(avg_speed),
        )
        return local_path, avg_speed, size, elapsed

    async def delete_remote(self, remote_id: str, trash: bool = True) -> None:
        if trash:
            await self._client.trash_file(remote_id)
            log.info("Trashed remote file fileid=%s", remote_id)
        else:
            await self._client.delete_file(remote_id)
            log.info("Permanently deleted remote file fileid=%s", remote_id)
