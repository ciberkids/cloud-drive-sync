"""OneDrive CloudFileOps implementation."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import CloudFileOps
from cloud_drive_sync.providers.onedrive.client import OneDriveClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.onedrive.operations")


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


class OneDriveFileOps(CloudFileOps):
    """Upload, download, and delete files on OneDrive."""

    def __init__(self, client: OneDriveClient) -> None:
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

        start_time = time.monotonic()

        if existing_id:
            result = await self._client._upload_content(
                str(local_path), name=name, file_id=existing_id
            )
        else:
            result = await self._client._upload_content(
                str(local_path), name=name, parent_id=remote_parent
            )

        elapsed = time.monotonic() - start_time
        avg_speed = file_size / elapsed if elapsed > 0 else 0
        log.info(
            "Upload complete: %s -> %s (%s at %s)",
            name,
            result.get("id"),
            _format_size(file_size),
            _format_speed(avg_speed),
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

        # Get the download URL from the item metadata
        import httpx

        token = await self._client._get_token()
        download_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{remote_id}/content"

        fd, tmp_path = tempfile.mkstemp(
            dir=str(local_path.parent),
            prefix=f".{local_path.name}.",
            suffix=".tmp",
        )
        try:
            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                async with client.stream(
                    "GET", download_url, headers={"Authorization": f"Bearer {token}"}
                ) as resp:
                    resp.raise_for_status()
                    with os.fdopen(fd, "wb") as tmp_file:
                        async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                            tmp_file.write(chunk)
                            if progress_callback:
                                elapsed = time.monotonic() - start_time
                                bytes_received = tmp_file.tell()
                                speed = bytes_received / elapsed if elapsed > 0 else 0
                                progress_callback(bytes_received, 0, speed)
                    fd = -1  # Prevent double close
            os.replace(tmp_path, str(local_path))
        except BaseException:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        size = os.path.getsize(str(local_path))
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
            log.info("Trashed remote file %s", remote_id)
        else:
            await self._client.delete_file(remote_id)
            log.info("Permanently deleted remote file %s", remote_id)
