"""Dropbox AuthProvider implementation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import AuthProvider, CloudClient
from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.dropbox.auth")

# Embedded Dropbox app credentials (PKCE flow — app secret not used for token exchange)
_DEFAULT_APP_KEY = "ch4h2lb0g6k9g42"
_DEFAULT_APP_SECRET = "[REDACTED]"


class _AuthUrlReady(Exception):
    """Raised when auth URL is ready but code input is needed via HTTP."""
    def __init__(self, url: str):
        self.url = url
        super().__init__(url)


class DropboxAuth(AuthProvider):
    """Handles Dropbox OAuth2 PKCE authentication."""

    # Pending auth flow for two-step HTTP auth
    _pending_flow = None

    def __init__(self, app_key: str = "") -> None:
        self._app_key = app_key or _DEFAULT_APP_KEY

    def run_auth_flow(self, headless: bool = False, extra: dict | None = None) -> Any:
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

        if not headless:
            import webbrowser
            webbrowser.open(authorize_url)
            print(f"Opened browser for authorization. URL: {authorize_url}")
        else:
            print(f"\n1. Go to: {authorize_url}")

        print("2. Click 'Allow' (you might have to log in first)")
        print("3. Copy the authorization code.\n")

        # No TTY — HTTP API context; return URL for two-step exchange
        if not sys.stdin.isatty():
            log.info("No TTY detected, returning auth URL for two-step flow")
            DropboxAuth._pending_flow = (auth_flow, self._app_key)
            raise _AuthUrlReady(authorize_url)

        auth_code = input("Enter the authorization code: ").strip()
        return self._finish(auth_flow, auth_code, self._app_key)

    @classmethod
    def exchange_code(cls, code: str) -> Any:
        """Complete a pending two-step auth flow by exchanging the code."""
        if cls._pending_flow is None:
            raise ValueError("No pending Dropbox auth flow. Call add_account first.")
        auth_flow, app_key = cls._pending_flow
        cls._pending_flow = None
        return cls._finish(auth_flow, code, app_key)

    @staticmethod
    def _finish(auth_flow: Any, auth_code: str, app_key: str) -> dict:
        oauth_result = auth_flow.finish(auth_code.strip())
        log.info("Dropbox OAuth2 authorization successful")
        return {
            "access_token": oauth_result.access_token,
            "refresh_token": oauth_result.refresh_token,
            "app_key": app_key,
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
