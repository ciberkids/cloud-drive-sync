"""OneDrive AuthProvider implementation using Azure AD OAuth2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cloud_drive_sync.providers.base import AuthProvider, CloudClient
from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.onedrive.auth")

# Microsoft Graph scopes for OneDrive access
SCOPES = [
    "Files.ReadWrite.All",
    "User.Read",
    "offline_access",
]

# Default Azure AD app registration client ID
# Users should override this with their own app registration
DEFAULT_CLIENT_ID = ""

# Credentials directory
CREDS_DIR = Path.home() / ".config" / "cloud-drive-sync" / "accounts"


class OneDriveAuth(AuthProvider):
    """Handles OneDrive authentication via Azure AD OAuth2."""

    def __init__(self, client_id: str = "") -> None:
        self._client_id = client_id or DEFAULT_CLIENT_ID

    def run_auth_flow(self, headless: bool = False) -> Any:
        """Run Azure AD OAuth2 flow.

        Uses device code flow for headless environments, authorization code flow otherwise.
        """
        try:
            from azure.identity import DeviceCodeCredential  # noqa: F401
        except ImportError:
            raise ImportError(
                "azure-identity is required for OneDrive support. "
                "Install it with: pip install azure-identity"
            )

        if headless:
            return self._run_device_code_flow()
        return self._run_auth_code_flow()

    def _run_device_code_flow(self) -> Any:
        """Run device code flow for headless/CLI environments."""
        from azure.identity import DeviceCodeCredential

        log.info("Starting Azure AD device code flow...")

        def _prompt_callback(verification_uri: str, user_code: str, expires_on: Any) -> None:
            log.info(
                "To sign in, visit %s and enter code: %s",
                verification_uri,
                user_code,
            )
            print(f"\nTo sign in, visit: {verification_uri}")
            print(f"Enter code: {user_code}\n")

        credential = DeviceCodeCredential(
            client_id=self._client_id,
            prompt_callback=_prompt_callback,
        )
        # Force a token acquisition to complete the flow
        token = credential.get_token(*SCOPES)
        log.info("Azure AD device code authorization successful")

        # Return serializable credential info
        return {
            "type": "device_code",
            "client_id": self._client_id,
            "token": token.token,
            "expires_on": token.expires_on,
        }

    def _run_auth_code_flow(self) -> Any:
        """Run authorization code flow for desktop environments."""
        from azure.identity import InteractiveBrowserCredential

        log.info("Starting Azure AD interactive browser flow...")

        credential = InteractiveBrowserCredential(
            client_id=self._client_id,
            redirect_uri="http://localhost:8400",
        )
        # Force a token acquisition
        token = credential.get_token(*SCOPES)
        log.info("Azure AD browser authorization successful")

        return {
            "type": "interactive",
            "client_id": self._client_id,
            "token": token.token,
            "expires_on": token.expires_on,
        }

    def save_credentials(self, creds: Any, account_id: str) -> None:
        """Save OneDrive credentials to disk."""
        creds_path = CREDS_DIR / f"onedrive_{account_id}.json"
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        creds_path.write_text(json.dumps(creds, indent=2))
        creds_path.chmod(0o600)
        log.info("Saved OneDrive credentials for %s", account_id)

    def load_credentials(self, account_id: str) -> Any | None:
        """Load OneDrive credentials from disk."""
        creds_path = CREDS_DIR / f"onedrive_{account_id}.json"
        if not creds_path.exists():
            return None
        try:
            data = json.loads(creds_path.read_text())
            log.debug("Loaded OneDrive credentials for %s", account_id)
            return data
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load credentials for %s: %s", account_id, e)
            return None

    async def create_client(self, creds: Any) -> CloudClient:
        """Create an OneDriveClient from saved credentials."""
        from cloud_drive_sync.providers.onedrive.client import OneDriveClient

        credential = self._build_credential(creds)
        return OneDriveClient(credential)

    async def get_account_email(self, client: CloudClient) -> str:
        """Get the account email from the connected client."""
        about = await client.get_about()
        return about.get("user", {}).get("emailAddress", "unknown")

    def _build_credential(self, creds: dict[str, Any]) -> Any:
        """Build an azure-identity credential from saved credential data."""
        try:
            from azure.identity import (
                DeviceCodeCredential,
                InteractiveBrowserCredential,
            )
        except ImportError:
            raise ImportError(
                "azure-identity is required for OneDrive support. "
                "Install it with: pip install azure-identity"
            )

        cred_type = creds.get("type", "device_code")
        client_id = creds.get("client_id", self._client_id)

        if cred_type == "device_code":
            return DeviceCodeCredential(client_id=client_id)
        elif cred_type == "interactive":
            return InteractiveBrowserCredential(
                client_id=client_id,
                redirect_uri="http://localhost:8400",
            )
        else:
            raise ValueError(f"Unknown credential type: {cred_type}")
