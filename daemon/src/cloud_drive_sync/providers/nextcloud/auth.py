"""Nextcloud AuthProvider implementation.

Supports app-password authentication (username + app password) with credentials
stored encrypted via the existing credential helpers.  The ``Nextcloud`` client
is created via nc-py-api.
"""

from __future__ import annotations

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

    def run_auth_flow(self, headless: bool = False, extra: dict | None = None) -> Any:
        """Authenticate with a Nextcloud server using an app password.

        Credentials can be supplied via *extra* (from the HTTP API / UI form) or,
        as a fallback, interactively via stdin when a TTY is present.

        Returns a dict with ``server_url``, ``username``, and ``app_password``.
        To create an app password in Nextcloud: Settings -> Security -> Devices & sessions.
        """
        creds = extra or {}

        server_url = (creds.get("server_url") or self._server_url).rstrip("/")
        username = creds.get("username", "")
        app_password = creds.get("app_password", "")

        # Fall back to interactive prompts only when we have a real TTY
        if not server_url or not username or not app_password:
            import sys
            if not sys.stdin.isatty():
                missing = [k for k, v in [("server_url", server_url), ("username", username), ("app_password", app_password)] if not v]
                raise ValueError(
                    f"Nextcloud credentials required: {', '.join(missing)}. "
                    "Provide them via the account setup form."
                )
            import getpass
            if not server_url:
                server_url = input("Nextcloud server URL (e.g. https://cloud.example.com): ").strip().rstrip("/")
                if not server_url:
                    raise ValueError("Server URL is required")
            if not username:
                username = input("Nextcloud username: ").strip()
                if not username:
                    raise ValueError("Username is required")
            if not app_password:
                app_password = getpass.getpass("Nextcloud app password: ").strip()
                if not app_password:
                    raise ValueError("App password is required")

        # Validate credentials by attempting a connection
        try:
            from nc_py_api import Nextcloud

            nc = Nextcloud(nextcloud_url=server_url, nc_auth_user=username, nc_auth_pass=app_password)
            user_info = nc.users.get_user()  # no arg = current user
            display = getattr(user_info, "display_name", None) or nc.user
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

        # Encrypted and owner-only. This stores the app password itself rather than a
        # refresh token, so it is the credential, not a handle to one. The salt goes
        # beside it rather than in the shared data directory, which is a separate
        # volume in a container — the ciphertext and its only key should not be
        # restorable apart from each other.
        from cloud_drive_sync.auth.credentials import write_encrypted_json

        write_encrypted_json(creds_file, creds, creds_file.with_suffix(".salt"))
        log.info("Saved Nextcloud credentials for account: %s", account_id)

    def load_credentials(self, account_id: str) -> Any | None:
        """Load Nextcloud credentials for a specific account."""
        creds_file = _CREDS_DIR / account_id / "nextcloud_creds.json"
        # Decrypts, and upgrades a pre-encryption plaintext file on read.
        from cloud_drive_sync.auth.credentials import read_encrypted_json

        data = read_encrypted_json(
            creds_file,
            creds_file.with_suffix(".salt"),
            label=f"Nextcloud credentials for {account_id}",
        )
        if data is None:
            return None

        # Kept from before the shared reader: an incomplete set must come back as
        # None, or the missing key surfaces as a KeyError inside create_client.
        if not all(k in data for k in ("server_url", "username", "app_password")):
            log.warning("Incomplete credentials for account: %s", account_id)
            return None
        return data

    def delete_credentials(self, account_id: str) -> None:
        creds_file = _CREDS_DIR / account_id / "nextcloud_creds.json"
        creds_file.unlink(missing_ok=True)
        creds_file.with_suffix(".salt").unlink(missing_ok=True)
        # This provider gets a directory per account, so clear it up too — but only
        # if empty, since another provider stores its files in the same tree.
        try:
            creds_file.parent.rmdir()
        except OSError:
            pass

    async def create_client(self, creds: Any) -> CloudClient:
        """Create a NextcloudClient from stored credentials."""
        from nc_py_api import Nextcloud

        from cloud_drive_sync.providers.nextcloud.client import NextcloudClient

        nc = Nextcloud(
            nextcloud_url=creds["server_url"],
            nc_auth_user=creds["username"],
            nc_auth_pass=creds["app_password"],
        )
        return NextcloudClient(nc, creds["server_url"], username=creds["username"], app_password=creds["app_password"])

    async def get_account_email(self, client: CloudClient) -> str:
        """Get the user's display name or email from Nextcloud."""
        about = await client.get_about()
        email = about.get("user", {}).get("emailAddress", "")
        if email:
            return email
        return about.get("user", {}).get("displayName", "unknown")
