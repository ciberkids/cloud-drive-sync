"""OAuth2 browser flow for Google Drive API authorization."""

from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.paths import config_dir

log = get_logger("auth.oauth")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

DEFAULT_CLIENT_SECRETS = config_dir() / "client_secret.json"


def run_oauth_flow(client_secrets: Path | None = None) -> "google.oauth2.credentials.Credentials":  # noqa: F821
    """Run the OAuth2 browser-based consent flow.

    Opens the user's default browser for Google account authorization.
    Returns credentials on success.

    Args:
        client_secrets: Path to the OAuth client secrets JSON file.
            Defaults to ~/.config/cloud-drive-sync/client_secret.json.

    Returns:
        Google OAuth2 credentials with the requested scopes.
    """
    secrets_path = client_secrets or DEFAULT_CLIENT_SECRETS
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"OAuth client secrets not found at {secrets_path}. "
            "Download it from the Google Cloud Console and place it there."
        )

    log.info("Starting OAuth2 browser flow...")
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes=SCOPES)
    credentials = flow.run_local_server(
        port=0,
        prompt="consent",
        success_message="Authorization complete. You may close this tab.",
    )
    log.info("OAuth2 authorization successful")
    return credentials
