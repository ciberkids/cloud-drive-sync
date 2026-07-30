"""Token storage and credential management with encryption at rest."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

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
#
# The strength of that varies by platform, and it is worth being precise about it:
# on Linux, macOS and Windows the seed is a real machine identifier, so the file
# alone is not enough. Inside the Docker image there is no /etc/machine-id, so the
# fallback constant below is used — and that constant is public, in this file. For
# container deployments the encryption therefore stops the tokens being plaintext
# but does not bind them to the host, which makes the file mode the real control.
# Hence _write_private: both files are owner-only.
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
                check=False,  # falls through to the default key if this fails
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


def _write_private(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``, readable only by the owner.

    The mode is set *before* the content is written, and again for a file that
    already exists. Both matter: ``write_bytes`` followed by ``chmod`` leaves a
    window where the secret sits world-readable, and ``touch(mode=...)`` does not
    change the mode of an existing file — so anyone who authenticated before this
    was fixed would have kept their 0644 file forever.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_bytes(data)


def _ensure_salt() -> bytes:
    sp = _salt_path()
    if sp.exists():
        # Repair the mode on a salt written before this was locked down. It is the
        # one key input an attacker cannot obtain elsewhere — /etc/machine-id is
        # world-readable by design — so a readable salt gives away the key.
        if os.name == "posix" and sp.stat().st_mode & 0o077:
            sp.chmod(0o600)
        return sp.read_bytes()
    salt = os.urandom(16)
    _write_private(sp, salt)
    return salt


def _ensure_salt_at(path: Path) -> bytes:
    """Return the salt stored at ``path``, creating it if absent.

    A variant of :func:`_ensure_salt` for credentials that do **not** live in the
    data directory. OneDrive, Box and Nextcloud store theirs under the *config*
    directory, and in a container those are two separate volumes:

        -v cloud-drive-sync-config:/root/.config/cloud-drive-sync
        -v cloud-drive-sync-data:/root/.local/share/cloud-drive-sync

    Encrypting those against the shared data-directory salt would mean the
    ciphertext and the only key to it live in different volumes. Restoring just the
    config volume — the intuitive thing to back up, since that is where the
    credentials are — would then hit ``_ensure_salt``, which *mints a new salt*
    rather than failing, leaving the old ciphertext permanently unreadable and
    every account silently gone. Today that restore works, because those files are
    plaintext, so introducing the dependency would be a regression.

    Keeping the salt beside the ciphertext removes the coupling and keeps the
    property that matters: the key is still PBKDF2 over the machine id, so a copy
    of the whole directory is not enough to decrypt it elsewhere.
    """
    if path.exists():
        if os.name == "posix" and path.stat().st_mode & 0o077:
            path.chmod(0o600)
        return path.read_bytes()
    salt = os.urandom(16)
    _write_private(path, salt)
    return salt


def write_encrypted_json(path: Path, payload: Any, salt_path: Path | None = None) -> None:
    """Encrypt ``payload`` as JSON and write it owner-only.

    ``salt_path`` selects which salt derives the key — pass one beside ``path`` for
    credentials outside the data directory; omit it to use the shared salt.
    """
    salt = _ensure_salt_at(salt_path) if salt_path is not None else _ensure_salt()
    fernet = _get_fernet(salt)
    _write_private(path, fernet.encrypt(json.dumps(payload).encode()))


def read_encrypted_json(
    path: Path,
    salt_path: Path | None = None,
    *,
    label: str = "credentials",
) -> Any | None:
    """Read a credential file, transparently upgrading a plaintext one.

    Returns ``None`` when the file is absent or cannot be recovered.

    Files written before these credentials were encrypted are plaintext JSON.
    Refusing them would silently disconnect every existing account, so they are
    read, re-written encrypted, and returned. The upgrade has to happen here rather
    than on save, because for these providers ``save_credentials`` is only called
    when an account is added — nothing rewrites the file afterwards, so a plaintext
    file would otherwise stay plaintext forever.

    Plaintext is detected by trying to parse first: a Fernet token is url-safe
    base64 and cannot parse as a JSON object, so there is no ambiguity.
    """
    if not path.exists():
        return None

    raw = path.read_bytes()

    # Plaintext from before encryption landed.
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        data = None
    if isinstance(data, dict):
        log.info("Upgrading %s at %s to encrypted storage", label, path)
        try:
            write_encrypted_json(path, data, salt_path)
        except Exception as exc:
            # The credentials were read fine; only the upgrade failed — a read-only
            # filesystem, or ownership changed by a PUID remap. Returning them
            # keeps the account working. Raising here would turn a cosmetic upgrade
            # into every account failing to load.
            log.warning("Could not re-encrypt %s at %s (%s); leaving as-is", label, path, exc)
        return data

    sp = salt_path if salt_path is not None else _salt_path()
    if not sp.exists():
        # Loud, because the silent version of this is "all my accounts vanished".
        log.error(
            "%s at %s is encrypted but its salt %s is missing, so it cannot be "
            "decrypted — restore the salt or re-add the account",
            label,
            path,
            sp,
        )
        return None

    try:
        return json.loads(_get_fernet(sp.read_bytes()).decrypt(raw))
    except Exception:
        log.error(
            "Failed to decrypt %s at %s, may need to re-authenticate", label, path
        )
        return None


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
    _write_private(path, encrypted)
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
