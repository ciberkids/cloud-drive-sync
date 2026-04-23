"""Box AuthProvider implementation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import AuthProvider, CloudClient
from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.box.auth")

_CONFIG_DIR = Path.home() / ".config" / "cloud-drive-sync"
_BOX_CREDENTIALS_DIR = _CONFIG_DIR / "box"


class _AuthUrlReady(Exception):
    """Raised when auth URL is ready but code input is needed via HTTP."""
    def __init__(self, url: str):
        self.url = url
        super().__init__(url)


class BoxAuth(AuthProvider):
    """Handles Box OAuth2 authentication using box-sdk-gen."""

    # Pending auth flow for two-step HTTP auth: stores (oauth, client_id, client_secret)
    _pending_flow = None

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret

    def _get_client_config(self) -> tuple[str, str]:
        """Get client ID and secret from instance, env, or config file."""
        import os

        client_id = self._client_id or os.environ.get("BOX_CLIENT_ID")
        client_secret = self._client_secret or os.environ.get("BOX_CLIENT_SECRET")

        if not client_id or not client_secret:
            config_path = _CONFIG_DIR / "box_client_config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text())
                client_id = client_id or config.get("client_id")
                client_secret = client_secret or config.get("client_secret")

        if not client_id or not client_secret:
            raise ValueError(
                "Box client_id and client_secret are required. "
                "Set BOX_CLIENT_ID and BOX_CLIENT_SECRET environment variables, "
                f"or create {_CONFIG_DIR / 'box_client_config.json'} with "
                '{"client_id": "...", "client_secret": "..."}.'
            )
        return client_id, client_secret

    def run_auth_flow(self, headless: bool = False, extra: dict | None = None) -> Any:
        from box_sdk_gen import BoxOAuth, OAuthConfig

        client_id, client_secret = self._get_client_config()

        oauth = BoxOAuth(OAuthConfig(client_id=client_id, client_secret=client_secret))
        auth_url = oauth.get_authorize_url()

        if headless:
            print(f"Visit this URL to authorize:\n{auth_url}")
        else:
            import webbrowser
            webbrowser.open(auth_url)
            print(f"Opened browser for authorization. URL: {auth_url}")

        # No TTY — HTTP API context; return URL for two-step exchange
        if not sys.stdin.isatty():
            log.info("No TTY detected, returning auth URL for two-step flow")
            BoxAuth._pending_flow = (oauth, client_id, client_secret)
            raise _AuthUrlReady(auth_url)

        auth_code = input("Enter the authorization code: ").strip()
        return self._finish(oauth, auth_code, client_id, client_secret)

    @classmethod
    def exchange_code(cls, code: str) -> Any:
        """Complete a pending two-step auth flow by exchanging the code."""
        if cls._pending_flow is None:
            raise ValueError("No pending Box auth flow. Call add_account first.")
        oauth, client_id, client_secret = cls._pending_flow
        cls._pending_flow = None
        return cls._finish(oauth, code.strip(), client_id, client_secret)

    @staticmethod
    def _finish(oauth: Any, auth_code: str, client_id: str, client_secret: str) -> dict:
        token = oauth.get_tokens_authorization_code_grant(auth_code)
        log.info("Box OAuth2 authorization successful")
        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }

    def save_credentials(self, creds: Any, account_id: str) -> None:
        _BOX_CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        creds_path = _BOX_CREDENTIALS_DIR / f"{account_id}.json"
        creds_path.write_text(json.dumps(creds, indent=2))
        creds_path.chmod(0o600)
        log.info("Saved Box credentials for account %s", account_id)

    def load_credentials(self, account_id: str) -> Any | None:
        creds_path = _BOX_CREDENTIALS_DIR / f"{account_id}.json"
        if not creds_path.exists():
            return None
        return json.loads(creds_path.read_text())

    async def create_client(self, creds: Any) -> CloudClient:
        from box_sdk_gen import BoxClient as SdkBoxClient
        from box_sdk_gen import BoxOAuth, OAuthConfig
        from box_sdk_gen.schemas import AccessToken

        oauth = BoxOAuth(
            OAuthConfig(
                client_id=creds["client_id"],
                client_secret=creds["client_secret"],
            )
        )
        token = AccessToken(
            access_token=creds["access_token"],
            refresh_token=creds.get("refresh_token"),
        )
        sdk_client = SdkBoxClient(auth=oauth).with_extra_headers({})
        # Authenticate with existing tokens
        oauth._token_storage.store(token)

        from cloud_drive_sync.providers.box.client import BoxClient

        return BoxClient(sdk_client)

    async def get_account_email(self, client: CloudClient) -> str:
        about = await client.get_about()
        return about.get("user", {}).get("emailAddress", "unknown")
