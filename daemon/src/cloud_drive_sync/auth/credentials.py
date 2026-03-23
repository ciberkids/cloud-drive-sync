"""Token storage and credential management with encryption at rest."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from cloud_drive_sync.auth.oauth import SCOPES, run_oauth_flow
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.paths import credentials_path, data_dir

log = get_logger("auth.credentials")

# The encryption key is derived from a machine-specific seed so tokens are
# not trivially readable if the file is copied to another machine.
_SALT_FILE = "token_salt"


def _get_machine_id() -> bytes:
    """Get a stable machine-specific identifier for key derivation."""
    if sys.platform == "linux":
        mid_path = Path("/etc/machine-id")
        if mid_path.exists():
            return mid_path.read_bytes().strip()
    elif sys.platform == "darwin":
        import subprocess
        try:
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "IOPlatformUUID" in line:
                    uuid = line.split('"')[-2]
                    return uuid.encode()
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            )
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            return value.encode()
        except Exception:
            pass
    return b"cloud-drive-sync-default-key"


def _get_fernet(salt: bytes) -> Fernet:
    """Derive a Fernet key from the machine ID + salt."""
    machine_id = _get_machine_id()

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(machine_id))
    return Fernet(key)


def _salt_path() -> Path:
    return data_dir() / _SALT_FILE


def _ensure_salt() -> bytes:
    sp = _salt_path()
    if sp.exists():
        return sp.read_bytes()
    salt = os.urandom(16)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_bytes(salt)
    return salt


def save_credentials(creds: Credentials, path: Path | None = None) -> None:
    """Encrypt and persist credentials to disk."""
    path = path or credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    }

    salt = _ensure_salt()
    fernet = _get_fernet(salt)
    encrypted = fernet.encrypt(json.dumps(payload).encode())
    path.write_bytes(encrypted)
    log.info("Credentials saved to %s", path)


def load_credentials(
    path: Path | None = None,
    on_refresh: callable | None = None,
) -> Credentials | None:
    """Load and decrypt credentials from disk, refreshing if expired.

    Args:
        path: Optional path to the credentials file.
        on_refresh: Optional callback invoked after a token refresh succeeds.
    """
    path = path or credentials_path()
    if not path.exists():
        log.debug("No stored credentials at %s", path)
        return None

    salt_p = _salt_path()
    if not salt_p.exists():
        log.warning("Salt file missing, cannot decrypt credentials")
        return None

    salt = salt_p.read_bytes()
    fernet = _get_fernet(salt)

    try:
        data = json.loads(fernet.decrypt(path.read_bytes()))
    except Exception:
        log.error("Failed to decrypt credentials, may need to re-authenticate")
        return None

    creds = Credentials(
        token=data["token"],
        refresh_token=data["refresh_token"],
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data.get("scopes", SCOPES),
    )

    if creds.expired and creds.refresh_token:
        log.info("Refreshing expired credentials")
        creds.refresh(Request())
        save_credentials(creds, path)
        if on_refresh:
            on_refresh()

    return creds


def save_account_credentials(creds: Credentials, account_id: str, path: Path | None = None) -> None:
    """Encrypt and persist credentials for a specific account."""
    from cloud_drive_sync.util.paths import account_credentials_path
    path = path or account_credentials_path(account_id)
    save_credentials(creds, path)


def load_account_credentials(account_id: str, on_refresh: callable | None = None) -> Credentials | None:
    """Load and decrypt credentials for a specific account."""
    from cloud_drive_sync.util.paths import account_credentials_path
    path = account_credentials_path(account_id)
    return load_credentials(path, on_refresh=on_refresh)


def get_credentials(path: Path | None = None) -> Credentials:
    """Load existing credentials or run the OAuth flow.

    Returns valid credentials, running the browser flow if necessary.
    """
    creds = load_credentials(path)
    if creds and creds.valid:
        return creds

    log.info("No valid credentials found, starting OAuth flow")
    creds = run_oauth_flow()
    save_credentials(creds, path)
    return creds
