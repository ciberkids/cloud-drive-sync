"""Proton Drive CloudFileOps stub (not yet implemented)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import CloudFileOps


class ProtonDriveFileOps(CloudFileOps):
    """Upload, download, and delete files on Proton Drive.

    This is a stub for future implementation. All methods raise
    NotImplementedError until Proton Drive support is completed.
    """

    async def upload_file(
        self,
        local_path: Path,
        remote_parent: str,
        remote_name: str | None = None,
        existing_id: str | None = None,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def download_file(
        self,
        remote_id: str,
        local_path: Path,
        progress_callback: Any = None,
    ) -> tuple[Path, float, int, float]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def delete_remote(self, remote_id: str, trash: bool = True) -> None:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")
