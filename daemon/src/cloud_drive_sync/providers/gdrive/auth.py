"""Google Drive AuthProvider implementation."""

from __future__ import annotations

from typing import Any

from cloud_drive_sync.providers.base import AuthProvider, CloudClient
from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.gdrive.auth")


class _AuthUrlReady(Exception):
    """Raised when auth URL is ready but code input is needed via HTTP."""
    def __init__(self, url: str):
        self.url = url
        super().__init__(url)


class GoogleDriveAuth(AuthProvider):
    """Handles Google Drive OAuth2 authentication."""

    # Pending auth flow for two-step HTTP auth
    _pending_flow = None
    # OAuth callback URL set by HttpServer when running (enables auto-redirect)
    _oauth_callback_url: str | None = None

    def run_auth_flow(self, headless: bool = False) -> Any:
        from cloud_drive_sync.auth.oauth import run_oauth_flow

        if headless:
            return self._run_console_flow()
        return run_oauth_flow()

    def _run_console_flow(self) -> Any:
        """Run OAuth flow in headless/console mode.

        Uses a manual code-entry flow: prints the authorization URL,
        the user visits it on any device, authorizes, and pastes back
        the code. This works in Docker, SSH, and any environment where
        a local HTTP redirect server would be unreachable.
        """
        import sys

        from cloud_drive_sync.auth.oauth import _create_oauth_flow

        log.info("Starting OAuth2 headless flow...")
        flow = _create_oauth_flow()

        # If the HTTP server is running, use its callback URL as redirect
        # so Google redirects directly to our server (no manual code pasting)
        if GoogleDriveAuth._oauth_callback_url:
            flow.redirect_uri = GoogleDriveAuth._oauth_callback_url
            log.info("Using HTTP callback redirect: %s", GoogleDriveAuth._oauth_callback_url)
        else:
            flow.redirect_uri = "http://localhost"

        auth_uri, _ = flow.authorization_url(
            prompt="consent",
            access_type="offline",
            state="gdrive",  # passed back in callback for provider identification
        )

        # If stdin is not a TTY (e.g., HTTP API, Docker without -it),
        # store the flow and return the URL for the caller to handle
        if not sys.stdin.isatty():
            log.info("No TTY detected, returning auth URL for two-step flow")
            GoogleDriveAuth._pending_flow = flow
            raise _AuthUrlReady(auth_uri)

        print(f"\nVisit this URL to authorize:\n\n  {auth_uri}\n")
        print("Sign in, click 'Allow'.")
        print("Your browser will redirect to a localhost URL that won't load.")
        print("Copy the FULL URL from your browser's address bar and paste it here.\n")
        sys.stdout.flush()
        response = input("Paste the redirect URL (or just the code): ").strip()

        # Accept either the full redirect URL or just the code
        code = self._extract_code(response)
        flow.fetch_token(code=code)
        log.info("OAuth2 headless authorization successful")
        return flow.credentials

    @staticmethod
    def _extract_code(response: str) -> str:
        """Extract the auth code from a redirect URL or raw code string."""
        from urllib.parse import parse_qs, urlparse

        if response.startswith("http"):
            parsed = urlparse(response)
            params = parse_qs(parsed.query)
            if "code" in params:
                return params["code"][0]
        return response

    @classmethod
    def exchange_code(cls, code: str) -> Any:
        """Complete a pending two-step auth flow by exchanging the code."""
        if cls._pending_flow is None:
            raise ValueError("No pending auth flow. Call add_account first.")
        flow = cls._pending_flow
        cls._pending_flow = None
        code = cls._extract_code(code)
        flow.fetch_token(code=code)
        log.info("OAuth2 code exchange successful")
        return flow.credentials

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
