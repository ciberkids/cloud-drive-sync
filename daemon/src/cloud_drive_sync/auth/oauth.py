"""OAuth2 browser flow for Google Drive API authorization."""

from __future__ import annotations

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

# Timeout for the OAuth browser flow (seconds).
# If the user doesn't complete auth within this time, the flow is cancelled.
AUTH_TIMEOUT = 120


def run_oauth_flow(
    client_secrets: Path | None = None,
    timeout: int = AUTH_TIMEOUT,
) -> "google.oauth2.credentials.Credentials":  # noqa: F821
    """Run the OAuth2 browser-based consent flow.

    Opens the user's default browser for Google account authorization.
    Returns credentials on success.

    Args:
        client_secrets: Path to the OAuth client secrets JSON file.
            Defaults to ~/.config/cloud-drive-sync/client_secret.json.
        timeout: Maximum seconds to wait for the user to complete auth.

    Returns:
        Google OAuth2 credentials with the requested scopes.

    Raises:
        FileNotFoundError: If client secrets file doesn't exist.
        TimeoutError: If the user doesn't complete auth within the timeout.
    """
    secrets_path = client_secrets or DEFAULT_CLIENT_SECRETS
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"OAuth client secrets not found at {secrets_path}. "
            "Download it from the Google Cloud Console and place it there."
        )

    log.info("Starting OAuth2 browser flow (timeout=%ds)...", timeout)
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes=SCOPES)

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
