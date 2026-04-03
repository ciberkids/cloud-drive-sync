"""Google Drive AuthProvider implementation."""

from __future__ import annotations

from typing import Any

from cloud_drive_sync.providers.base import AuthProvider, CloudClient
from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.gdrive.auth")


class GoogleDriveAuth(AuthProvider):
    """Handles Google Drive OAuth2 authentication."""

    def run_auth_flow(self, headless: bool = False) -> Any:
        from cloud_drive_sync.auth.oauth import run_oauth_flow

        if headless:
            return self._run_console_flow()
        return run_oauth_flow()

    def _run_console_flow(self) -> Any:
        """Run OAuth flow in headless/console mode.

        Uses run_local_server with open_browser=False so the user can
        copy the URL manually. Falls back to manual code entry if the
        local server can't bind (e.g., Docker without port forwarding).
        """
        from cloud_drive_sync.auth.oauth import _create_oauth_flow

        log.info("Starting OAuth2 headless flow...")
        flow = _create_oauth_flow()

        try:
            # Try local server without auto-opening browser
            credentials = flow.run_local_server(
                port=0,
                open_browser=False,
                prompt="consent",
                success_message="Authorization complete. You may close this tab.",
            )
        except OSError:
            # Fallback: manual OOB-style flow for environments where
            # a local HTTP server can't bind (rare)
            auth_uri, _ = flow.authorization_url(prompt="consent")
            print(f"\nVisit this URL to authorize:\n{auth_uri}\n")
            code = input("Enter the authorization code: ").strip()
            flow.fetch_token(code=code)
            credentials = flow.credentials

        log.info("OAuth2 headless authorization successful")
        return credentials

    def save_credentials(self, creds: Any, account_id: str) -> None:
        from cloud_drive_sync.auth.credentials import save_account_credentials

        save_account_credentials(creds, account_id)

    def load_credentials(self, account_id: str) -> Any | None:
        from cloud_drive_sync.auth.credentials import load_account_credentials

        return load_account_credentials(account_id)

    async def create_client(self, creds: Any) -> CloudClient:
        from cloud_drive_sync.providers.gdrive.client import GoogleDriveClient

        return GoogleDriveClient(creds)

    async def get_account_email(self, client: CloudClient) -> str:
        about = await client.get_about()
        return about.get("user", {}).get("emailAddress", "unknown")
