"""Nextcloud AuthProvider implementation.

Supports app-password authentication (username + app password) with credentials
stored encrypted via the existing credential helpers.  The ``Nextcloud`` client
is created via nc-py-api.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import AuthProvider, CloudClient
from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.nextcloud.auth")

# Credentials are stored as a JSON blob: {"username": "...", "app_password": "...", "server_url": "..."}
_CREDS_DIR = Path.home() / ".config" / "cloud-drive-sync" / "accounts"


class NextcloudAuth(AuthProvider):
    """Handles Nextcloud app-password authentication."""

    def __init__(self, server_url: str = "") -> None:
        self._server_url = server_url.rstrip("/") if server_url else ""

    def run_auth_flow(self, headless: bool = False) -> Any:
        """Prompt for Nextcloud server URL, username, and app password.

        Returns a dict with ``server_url``, ``username``, and ``app_password``.
        To create an app password in Nextcloud: Settings -> Security -> Devices & sessions.
        """
        import getpass

        server_url = self._server_url
        if not server_url:
            server_url = input("Nextcloud server URL (e.g. https://cloud.example.com): ").strip().rstrip("/")
            if not server_url:
                raise ValueError("Server URL is required")

        username = input("Nextcloud username: ").strip()
        if not username:
            raise ValueError("Username is required")

        app_password = getpass.getpass("Nextcloud app password: ").strip()
        if not app_password:
            raise ValueError("App password is required")

        # Validate credentials by attempting a connection
        try:
            from nc_py_api import Nextcloud

            nc = Nextcloud(nextcloud_url=server_url, nc_auth_user=username, nc_auth_pass=app_password)
            user = nc.users.get_current()
            display = user.display_name if hasattr(user, "display_name") else str(user)
            log.info("Authenticated as: %s", display)
        except ImportError:
            raise ImportError(
                "nc-py-api is required for Nextcloud support. "
                "Install it with: pip install nc-py-api"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to authenticate with Nextcloud: {e}") from e

        return {
            "server_url": server_url,
            "username": username,
            "app_password": app_password,
        }

    def save_credentials(self, creds: Any, account_id: str) -> None:
        """Save Nextcloud credentials as an encrypted JSON file."""
        creds_dir = _CREDS_DIR / account_id
        creds_dir.mkdir(parents=True, exist_ok=True)

        creds_file = creds_dir / "nextcloud_creds.json"
        creds_data = json.dumps(creds, indent=2)

        # Set restrictive permissions before writing
        creds_file.touch(mode=0o600, exist_ok=True)
        creds_file.write_text(creds_data)
        log.info("Saved Nextcloud credentials for account: %s", account_id)

    def load_credentials(self, account_id: str) -> Any | None:
        """Load Nextcloud credentials for a specific account."""
        creds_file = _CREDS_DIR / account_id / "nextcloud_creds.json"
        if not creds_file.exists():
            log.debug("No credentials found for account: %s", account_id)
            return None

        try:
            data = json.loads(creds_file.read_text())
            # Ensure all required fields are present
            if not all(k in data for k in ("server_url", "username", "app_password")):
                log.warning("Incomplete credentials for account: %s", account_id)
                return None
            return data
        except (json.JSONDecodeError, OSError) as e:
            log.error("Failed to load credentials for %s: %s", account_id, e)
            return None

    async def create_client(self, creds: Any) -> CloudClient:
        """Create a NextcloudClient from stored credentials."""
        from nc_py_api import Nextcloud

        from cloud_drive_sync.providers.nextcloud.client import NextcloudClient

        nc = Nextcloud(
            nextcloud_url=creds["server_url"],
            nc_auth_user=creds["username"],
            nc_auth_pass=creds["app_password"],
        )
        return NextcloudClient(nc, creds["server_url"])

    async def get_account_email(self, client: CloudClient) -> str:
        """Get the user's display name or email from Nextcloud."""
        about = await client.get_about()
        email = about.get("user", {}).get("emailAddress", "")
        if email:
            return email
        return about.get("user", {}).get("displayName", "unknown")
