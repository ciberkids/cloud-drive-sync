"""OAuth2 browser flow for Google Drive API authorization."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.paths import config_dir

log = get_logger("auth.oauth")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

DEFAULT_CLIENT_SECRETS = config_dir() / "client_secret.json"

# Embedded OAuth client credentials (Desktop app type).
# Users can override by placing a client_secret.json in the config dir
# or setting CDS_GOOGLE_CLIENT_ID / CDS_GOOGLE_CLIENT_SECRET env vars.
_EMBEDDED_CLIENT_ID = os.environ.get(
    "CDS_GOOGLE_CLIENT_ID",
    "613983213830-g58svo7c1m2vhtta0r0snkb8rdjekq87.apps.googleusercontent.com",
)
_EMBEDDED_CLIENT_SECRET = os.environ.get(
    "CDS_GOOGLE_CLIENT_SECRET",
    "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",
)

# Timeout for the OAuth browser flow (seconds).
# If the user doesn't complete auth within this time, the flow is cancelled.
AUTH_TIMEOUT = 120


def _create_oauth_flow() -> InstalledAppFlow:
    """Create an OAuth flow using either a local secrets file or embedded credentials."""
    # Prefer local client_secret.json if it exists (power user override)
    if DEFAULT_CLIENT_SECRETS.exists():
        log.info("Using OAuth client secrets from %s", DEFAULT_CLIENT_SECRETS)
        return InstalledAppFlow.from_client_secrets_file(str(DEFAULT_CLIENT_SECRETS), scopes=SCOPES)

    # Use embedded credentials
    log.info("Using embedded OAuth client credentials")
    client_config = {
        "installed": {
            "client_id": _EMBEDDED_CLIENT_ID,
            "client_secret": _EMBEDDED_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    return InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)


def run_oauth_flow(
    client_secrets: Path | None = None,
    timeout: int = AUTH_TIMEOUT,
) -> "google.oauth2.credentials.Credentials":  # noqa: F821
    """Run the OAuth2 browser-based consent flow.

    Opens the user's default browser for Google account authorization.
    Returns credentials on success. Uses embedded client credentials
    by default — no client_secret.json required.

    Args:
        client_secrets: Path to the OAuth client secrets JSON file.
            If provided and exists, overrides embedded credentials.
        timeout: Maximum seconds to wait for the user to complete auth.

    Returns:
        Google OAuth2 credentials with the requested scopes.

    Raises:
        TimeoutError: If the user doesn't complete auth within the timeout.
    """
    if client_secrets and client_secrets.exists():
        log.info("Using OAuth client secrets from %s", client_secrets)
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=SCOPES)
    else:
        flow = _create_oauth_flow()

    log.info("Starting OAuth2 browser flow (timeout=%ds)...", timeout)

    # run_local_server blocks until the browser redirects back.
    # Run it in a thread with a timeout so a closed browser doesn't hang forever.
    result = [None]
    error = [None]

    def _run():
        try:
            creds = flow.run_local_server(
                port=0,
                prompt="consent",
                success_message="Authorization complete. You may close this tab.",
                timeout_seconds=timeout,
            )
            result[0] = creds
        except Exception as exc:
            error[0] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout + 5)

    if thread.is_alive():
        # Thread is still blocking — the user didn't complete auth.
        # The thread is a daemon thread, so it will be cleaned up on exit.
        log.warning("OAuth flow timed out after %ds", timeout)
        raise TimeoutError(
            f"Authentication timed out after {timeout} seconds. "
            "Please try again and complete the sign-in in your browser."
        )

    if error[0] is not None:
        raise error[0]

    if result[0] is None:
        raise RuntimeError("OAuth flow completed without credentials")

    log.info("OAuth2 authorization successful")
    return result[0]
