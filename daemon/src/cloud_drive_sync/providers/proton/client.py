"""Proton Drive CloudClient stub (not yet implemented)."""

from __future__ import annotations

from typing import Any

from cloud_drive_sync.providers.base import CloudClient


class ProtonDriveClient(CloudClient):
    """Proton Drive API wrapper implementing CloudClient.

    This is a stub for future implementation. All methods raise
    NotImplementedError until Proton Drive support is completed.
    """

    # ── CloudClient capability properties ────────────────────────────

    @property
    def supports_trash(self) -> bool:
        return True

    @property
    def supports_export(self) -> bool:
        return False

    @property
    def hash_field(self) -> str:
        return "sha256"

    @property
    def hash_algorithm(self) -> str:
        return "sha256"

    @property
    def folder_mime_type(self) -> str | None:
        return None

    @property
    def native_doc_mimes(self) -> frozenset[str]:
        return frozenset()

    # ── CloudClient methods ──────────────────────────────────────────

    async def list_files(
        self,
        folder_id: str = "root",
        page_token: str | None = None,
        page_size: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def get_file(self, file_id: str) -> dict[str, Any]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def create_file(
        self,
        name: str,
        parent_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        is_folder: bool = False,
    ) -> dict[str, Any]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def update_file(
        self,
        file_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def delete_file(self, file_id: str) -> None:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def trash_file(self, file_id: str) -> dict[str, Any]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def list_all_recursive(
        self, folder_id: str = "root", prefix: str = ""
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def get_about(self) -> dict[str, Any]:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")

    async def find_child_folder(self, parent_id: str, name: str) -> str | None:
        raise NotImplementedError("Proton Drive support planned for Q2 2026+")
