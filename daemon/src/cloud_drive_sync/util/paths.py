"""Cross-platform path resolution for cloud-drive-sync."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

APP_NAME = "cloud-drive-sync"


def config_dir() -> Path:
    """Return the config directory (platform-appropriate)."""
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def data_dir() -> Path:
    """Return the data directory (platform-appropriate)."""
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def runtime_dir() -> Path:
    """Return the runtime directory (platform-appropriate)."""
    return Path(platformdirs.user_runtime_dir(APP_NAME, appauthor=False))


def config_path() -> Path:
    """Return the path to config.toml."""
    return config_dir() / "config.toml"


def db_path() -> Path:
    """Return the path to state.db."""
    return data_dir() / "state.db"


def ipc_address():
    """Return the IPC address: socket path on Unix, (host, port) on Windows."""
    if sys.platform == "win32":
        port_file = runtime_dir() / "cloud-drive-sync.port"
        return ("127.0.0.1", port_file)
    return runtime_dir() / "cloud-drive-sync.sock"


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
    """Create all required directories if they don't exist.

    Also migrates files from the old 'gdrive-sync' paths if they exist.
    """
    config_dir().mkdir(parents=True, exist_ok=True)
    data_dir().mkdir(parents=True, exist_ok=True)
    _migrate_old_paths()


_OLD_APP_NAME = "gdrive-sync"


def _migrate_old_paths() -> None:
    """Copy files from old gdrive-sync directories to cloud-drive-sync if needed."""
    if sys.platform != "linux":
        return
    import shutil
    import logging

    log = logging.getLogger("cloud_drive_sync.paths")

    old_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / _OLD_APP_NAME
    old_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / _OLD_APP_NAME

    migrated = False

    # Migrate config files (client_secret.json, config.toml)
    if old_config.is_dir():
        for name in ("client_secret.json", "config.toml"):
            old_file = old_config / name
            new_file = config_dir() / name
            if old_file.exists() and not new_file.exists():
                shutil.copy2(str(old_file), str(new_file))
                log.info("Migrated %s -> %s", old_file, new_file)
                migrated = True

    # Migrate data files (credentials, token salt)
    if old_data.is_dir():
        for name in ("credentials.enc", "token_salt"):
            old_file = old_data / name
            new_file = data_dir() / name
            if old_file.exists() and not new_file.exists():
                shutil.copy2(str(old_file), str(new_file))
                log.info("Migrated %s -> %s", old_file, new_file)
                migrated = True
        # Also migrate per-account credential files
        for old_file in old_data.glob("credentials-*.enc"):
            new_file = data_dir() / old_file.name
            if not new_file.exists():
                shutil.copy2(str(old_file), str(new_file))
                log.info("Migrated %s -> %s", old_file, new_file)
                migrated = True

    if migrated:
        log.info("Migration from gdrive-sync paths complete")
