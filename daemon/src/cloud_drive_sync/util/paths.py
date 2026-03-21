"""XDG-compliant path resolution for cloud-drive-sync."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "cloud-drive-sync"


def config_dir() -> Path:
    """Return the config directory (~/.config/cloud-drive-sync/)."""
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def data_dir() -> Path:
    """Return the data directory (~/.local/share/cloud-drive-sync/)."""
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


def runtime_dir() -> Path:
    """Return the runtime directory ($XDG_RUNTIME_DIR or /run/user/$UID)."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg)
    return Path(f"/run/user/{os.getuid()}")


def config_path() -> Path:
    """Return the path to config.toml."""
    return config_dir() / "config.toml"


def db_path() -> Path:
    """Return the path to state.db."""
    return data_dir() / "state.db"


def socket_path() -> Path:
    """Return the path to the IPC unix domain socket."""
    return runtime_dir() / "cloud-drive-sync.sock"


def pid_path() -> Path:
    """Return the path to the PID file."""
    return runtime_dir() / "cloud-drive-sync.pid"


def credentials_path() -> Path:
    """Return the path to stored OAuth credentials."""
    return data_dir() / "credentials.enc"


def account_credentials_path(account_id: str) -> Path:
    """Return the path to stored OAuth credentials for a specific account."""
    safe_id = account_id.replace("@", "_at_").replace(".", "_")
    return data_dir() / f"credentials-{safe_id}.enc"


def ensure_dirs() -> None:
    """Create all required directories if they don't exist."""
    config_dir().mkdir(parents=True, exist_ok=True)
    data_dir().mkdir(parents=True, exist_ok=True)
