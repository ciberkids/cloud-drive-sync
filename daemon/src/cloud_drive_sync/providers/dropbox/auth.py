"""Dropbox AuthProvider implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import AuthProvider, CloudClient
from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.dropbox.auth")

# Default Dropbox app key — users can override via config
_DEFAULT_APP_KEY = ""


class DropboxAuth(AuthProvider):
    """Handles Dropbox OAuth2 PKCE authentication."""

    def __init__(self, app_key: str = "") -> None:
        self._app_key = app_key or _DEFAULT_APP_KEY

    def run_auth_flow(self, headless: bool = False) -> Any:
        """Run Dropbox OAuth2 PKCE flow.

        Returns a dict with access_token, refresh_token, app_key, and expiry.
        """
        import dropbox

        auth_flow = dropbox.DropboxOAuth2FlowNoRedirect(
            self._app_key,
            use_pkce=True,
            token_access_type="offline",
        )

        authorize_url = auth_flow.start()
        print(f"\n1. Go to: {authorize_url}")
        print("2. Click 'Allow' (you might have to log in first)")
        print("3. Copy the authorization code.\n")

        auth_code = input("Enter the authorization code: ").strip()

        oauth_result = auth_flow.finish(auth_code)
        log.info("Dropbox OAuth2 authorization successful")

        return {
            "access_token": oauth_result.access_token,
            "refresh_token": oauth_result.refresh_token,
            "app_key": self._app_key,
            "expires_at": oauth_result.expires_at.isoformat() if oauth_result.expires_at else None,
        }

    def save_credentials(self, creds: Any, account_id: str) -> None:
        """Encrypt and persist Dropbox credentials to disk."""
        path = self._credentials_path(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        from cloud_drive_sync.auth.credentials import _ensure_salt, _get_fernet

        salt = _ensure_salt()
        fernet = _get_fernet(salt)
        encrypted = fernet.encrypt(json.dumps(creds).encode())
        path.write_bytes(encrypted)
        log.info("Dropbox credentials saved for account %s", account_id)

    def load_credentials(self, account_id: str) -> Any | None:
        """Load and decrypt Dropbox credentials from disk."""
        path = self._credentials_path(account_id)
        if not path.exists():
            log.debug("No stored Dropbox credentials for %s", account_id)
            return None

        from cloud_drive_sync.auth.credentials import _get_fernet, _salt_path

        salt_p = _salt_path()
        if not salt_p.exists():
            log.warning("Salt file missing, cannot decrypt credentials")
            return None

        salt = salt_p.read_bytes()
        fernet = _get_fernet(salt)

        try:
            data = json.loads(fernet.decrypt(path.read_bytes()))
        except Exception:
            log.error("Failed to decrypt Dropbox credentials for %s", account_id)
            return None

        return data

    async def create_client(self, creds: Any) -> CloudClient:
        """Create a DropboxClient from stored credentials."""
        import dropbox

        from cloud_drive_sync.providers.dropbox.client import DropboxClient

        dbx = dropbox.Dropbox(
            oauth2_access_token=creds.get("access_token"),
            oauth2_refresh_token=creds.get("refresh_token"),
            app_key=creds.get("app_key", self._app_key),
        )
        return DropboxClient(dbx)

    async def get_account_email(self, client: CloudClient) -> str:
        about = await client.get_about()
        return about.get("user", {}).get("emailAddress", "unknown")

    @staticmethod
    def _credentials_path(account_id: str) -> Path:
        """Get the path for storing Dropbox credentials for an account."""
        from cloud_drive_sync.util.paths import data_dir

        safe_id = account_id.replace("/", "_").replace("\\", "_")
        return data_dir() / f"dropbox-credentials-{safe_id}.enc"
