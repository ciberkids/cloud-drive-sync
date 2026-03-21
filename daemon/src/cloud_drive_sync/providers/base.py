"""Abstract base classes for cloud storage providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class CloudClient(ABC):
    """Abstract interface for cloud storage API operations."""

    # ── Provider capability properties ──────────────────────────────

    @property
    @abstractmethod
    def supports_trash(self) -> bool:
        """Whether the provider supports a trash/recycle bin."""

    @property
    @abstractmethod
    def supports_export(self) -> bool:
        """Whether the provider supports exporting native docs (e.g. Google Docs)."""

    @property
    @abstractmethod
    def hash_field(self) -> str:
        """The metadata field name containing the file hash.

        Examples: "md5Checksum", "content_hash", "sha1Hash", "quickXorHash"
        """

    @property
    @abstractmethod
    def hash_algorithm(self) -> str:
        """The hash algorithm identifier.

        Examples: "md5", "sha1", "content_hash", "quickxor"
        """

    @property
    @abstractmethod
    def folder_mime_type(self) -> str | None:
        """The MIME type that indicates a folder, or None for path-based providers."""

    @property
    @abstractmethod
    def native_doc_mimes(self) -> frozenset[str]:
        """Set of MIME types that are native docs (can't be downloaded as binary)."""

    # ── Core file operations ────────────────────────────────────────

    @abstractmethod
    async def list_files(
        self,
        folder_id: str = "root",
        page_token: str | None = None,
        page_size: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        """List files in a folder.

        Returns a dict with 'files' list and optional 'nextPageToken'.
        """

    @abstractmethod
    async def get_file(self, file_id: str) -> dict[str, Any]:
        """Get metadata for a single file."""

    @abstractmethod
    async def create_file(
        self,
        name: str,
        parent_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        is_folder: bool = False,
    ) -> dict[str, Any]:
        """Create a file or folder."""

    @abstractmethod
    async def update_file(
        self,
        file_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        """Update a file's content and/or metadata."""

    @abstractmethod
    async def delete_file(self, file_id: str) -> None:
        """Permanently delete a file."""

    @abstractmethod
    async def trash_file(self, file_id: str) -> dict[str, Any]:
        """Move a file to trash."""

    @abstractmethod
    async def list_all_recursive(
        self, folder_id: str = "root", prefix: str = ""
    ) -> list[dict[str, Any]]:
        """Recursively list all files and folders, adding a 'relativePath' field."""

    @abstractmethod
    async def get_about(self) -> dict[str, Any]:
        """Get storage quota and user info."""

    @abstractmethod
    async def find_child_folder(self, parent_id: str, name: str) -> str | None:
        """Find a child folder by name within a parent.

        Returns the folder ID if found, None otherwise.
        """

    # ── Optional methods with default implementations ───────────────

    async def export_file(self, file_id: str, mime_type: str) -> bytes:
        """Export a native doc to a specific format. Only for providers with supports_export."""
        raise NotImplementedError(f"{type(self).__name__} does not support export")

    async def list_shared_drives(self) -> list[dict[str, Any]]:
        """List shared/team drives. Not all providers support this."""
        return []

    async def list_all_files(self, folder_id: str = "root") -> list[dict[str, Any]]:
        """List all files in a folder, handling pagination."""
        all_files: list[dict[str, Any]] = []
        page_token = None
        while True:
            result = await self.list_files(folder_id=folder_id, page_token=page_token)
            all_files.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return all_files


class CloudFileOps(ABC):
    """Abstract interface for high-level file transfer operations."""

    @abstractmethod
    async def upload_file(
        self,
        local_path: Path,
        remote_parent: str,
        remote_name: str | None = None,
        existing_id: str | None = None,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        """Upload a local file to the cloud.

        Returns metadata dict including at minimum 'id' and the hash field.
        Transfer stats should be included as '_transfer_speed', '_transfer_size',
        '_transfer_elapsed' keys.
        """

    @abstractmethod
    async def download_file(
        self,
        remote_id: str,
        local_path: Path,
        progress_callback: Any = None,
    ) -> tuple[Path, float, int, float]:
        """Download a file from the cloud.

        Returns (local_path, avg_speed, size, elapsed).
        """

    @abstractmethod
    async def delete_remote(self, remote_id: str, trash: bool = True) -> None:
        """Delete or trash a remote file."""


class CloudChangePoller(ABC):
    """Abstract interface for remote change detection."""

    @abstractmethod
    async def get_start_page_token(self) -> str:
        """Get the initial token for change polling."""

    @abstractmethod
    async def poll_changes(self, page_token: str) -> tuple[list, str]:
        """Poll for changes since the given token.

        Returns (list_of_changes, new_token). Changes should be RemoteChange-compatible.
        """

    async def poll_loop(
        self, page_token: str, interval: float, callback, stop_event
    ) -> str:
        """Continuously poll at the given interval. Default implementation."""
        import asyncio

        current_token = page_token
        while not stop_event.is_set():
            try:
                changes, current_token = await self.poll_changes(current_token)
                if changes:
                    await callback(changes, current_token)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Error during change polling")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

        return current_token


class AuthProvider(ABC):
    """Abstract interface for provider authentication."""

    @abstractmethod
    def run_auth_flow(self, headless: bool = False) -> Any:
        """Run the authentication flow. Returns provider-specific credentials."""

    @abstractmethod
    def save_credentials(self, creds: Any, account_id: str) -> None:
        """Save credentials for a specific account."""

    @abstractmethod
    def load_credentials(self, account_id: str) -> Any | None:
        """Load credentials for a specific account."""

    @abstractmethod
    async def create_client(self, creds: Any) -> CloudClient:
        """Create a CloudClient from credentials."""

    @abstractmethod
    async def get_account_email(self, client: CloudClient) -> str:
        """Get the account email/identifier from a connected client."""
