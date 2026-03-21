"""Proton Drive CloudChangePoller stub (not yet implemented)."""

from __future__ import annotations

from cloud_drive_sync.providers.base import CloudChangePoller


class ProtonDriveChangePoller(CloudChangePoller):
    """Polls Proton Drive for remote modifications.

    This is a stub for future implementation. All methods raise
    NotImplementedError until Proton Drive support is completed.
    """

    async def get_start_page_token(self) -> str:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def poll_changes(self, page_token: str) -> tuple[list, str]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")
