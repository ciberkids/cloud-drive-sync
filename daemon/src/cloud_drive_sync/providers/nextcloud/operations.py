"""Nextcloud CloudFileOps implementation."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote as _urlencode

from cloud_drive_sync.providers.base import CloudFileOps
from cloud_drive_sync.providers.nextcloud.client import NextcloudClient
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.retry import async_retry

log = get_logger("providers.nextcloud.operations")

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitise_name(name: str) -> str:
    """Strip ASCII control characters (including \\r, \\n) from a filename."""
    cleaned = _CONTROL_RE.sub("", name)
    if cleaned != name:
        log.warning("Stripped control characters from filename %r → %r", name, cleaned)
    return cleaned


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

    _UPLOAD_CHUNK_SIZE = 2 * 1024 * 1024  # 2 MB chunks

    @async_retry(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def upload_file(
        self,
        local_path: Path,
        remote_parent: str,
        remote_name: str | None = None,
        existing_id: str | None = None,
        progress_callback: Any = None,
        resume_uri: str | None = None,
    ) -> dict[str, Any]:
        # Normalise to NFC and strip control chars (issues #30/#31)
        name = unicodedata.normalize("NFC", _sanitise_name(remote_name or local_path.name))
        file_size = local_path.stat().st_size
        log.info("Uploading %s (%d bytes) as '%s'", local_path, file_size, name)

        if existing_id:
            remote_path = self._client._resolve_path(existing_id)
        else:
            parent = "/" if remote_parent == "root" else self._client._resolve_path(remote_parent)
            remote_path = f"{parent}/{name}" if parent != "/" else f"/{name}"

        start_time = time.monotonic()
        loop = asyncio.get_running_loop()

        def _upload():
            # Use a streaming PUT via httpx so progress_callback fires per chunk.
            # nc_py_api already requires httpx, so no extra dependency.
            import httpx

            # Percent-encode path components so special chars (%, spaces, etc.)
            # don't get double-encoded or rejected by httpx (issues #30/#31).
            encoded_path = _urlencode(remote_path, safe="/")
            dav_url = (
                f"{self._client._server_url}/remote.php/dav/files"
                f"/{_urlencode(self._client._username, safe='')}{encoded_path}"
            )
            auth = (self._client._username, self._client._app_password)

            uploaded = 0
            t0 = time.monotonic()

            def _gen():
                nonlocal uploaded
                with open(str(local_path), "rb") as f:
                    while True:
                        chunk = f.read(NextcloudFileOps._UPLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        uploaded += len(chunk)
                        elapsed = time.monotonic() - t0
                        speed = uploaded / elapsed if elapsed > 0 else 0
                        if progress_callback:
                            loop.call_soon_threadsafe(progress_callback, uploaded, file_size, speed)
                        yield chunk

            with httpx.Client(timeout=300) as client:
                resp = client.put(dav_url, content=_gen(), auth=auth)
                resp.raise_for_status()

            # Re-list to get uploaded file metadata
            parent_dir = remote_path.rsplit("/", 1)[0] or "/"
            nodes = self._client._nc.files.listdir(parent_dir)
            target_name = remote_path.rsplit("/", 1)[-1]
            for node in nodes:
                if unicodedata.normalize("NFC", node.name) == unicodedata.normalize("NFC", target_name):
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

        loop = asyncio.get_running_loop()

        def _download():
            remote_path = self._client._resolve_path(remote_id)

            fd, tmp_path = tempfile.mkstemp(
                dir=str(local_path.parent),
                prefix=f".{local_path.name}.",
                suffix=".tmp",
            )
            try:
                data = self._client._nc.files.download(remote_path)
                received = 0
                t0 = time.monotonic()
                with os.fdopen(fd, "wb") as tmp_file:
                    if isinstance(data, bytes):
                        tmp_file.write(data)
                        received = len(data)
                        elapsed = time.monotonic() - t0
                        speed = received / elapsed if elapsed > 0 else 0
                        if progress_callback:
                            loop.call_soon_threadsafe(progress_callback, received, 0, speed)
                    else:
                        for chunk in data:
                            tmp_file.write(chunk)
                            received += len(chunk)
                            elapsed = time.monotonic() - t0
                            speed = received / elapsed if elapsed > 0 else 0
                            if progress_callback:
                                loop.call_soon_threadsafe(progress_callback, received, 0, speed)
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
