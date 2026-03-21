"""Proton Drive AuthProvider stub (not yet implemented)."""

from __future__ import annotations

from typing import Any

from cloud_drive_sync.providers.base import AuthProvider, CloudClient


class ProtonDriveAuth(AuthProvider):
    """Handles Proton Drive authentication.

    This is a stub for future implementation. All methods raise
    NotImplementedError until Proton Drive support is completed.
    """

    def run_auth_flow(self, headless: bool = False) -> Any:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    def save_credentials(self, creds: Any, account_id: str) -> None:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    def load_credentials(self, account_id: str) -> Any | None:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def create_client(self, creds: Any) -> CloudClient:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def get_account_email(self, client: CloudClient) -> str:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")
