"""Install / uninstall the Nautilus overlay extension."""

from __future__ import annotations

import os
from pathlib import Path


def _extensions_dir() -> Path:
    """Return the nautilus-python extensions directory for the current user."""
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "nautilus-python" / "extensions"


def _extension_source() -> Path:
    """Return the path to our nautilus_overlay.py module."""
    return Path(__file__).with_name("nautilus_overlay.py")


def install() -> Path:
    """Symlink the overlay extension into the Nautilus extensions directory.

    Returns the path to the created symlink.
    """
    target_dir = _extensions_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    link_path = target_dir / "cloud_drive_sync_overlay.py"
    source = _extension_source()

    # Remove any existing link/file at the target
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()

    link_path.symlink_to(source)
    return link_path


def uninstall() -> bool:
    """Remove the overlay extension symlink.

    Returns True if a symlink was removed, False if none existed.
    """
    link_path = _extensions_dir() / "cloud_drive_sync_overlay.py"

    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
        return True
    return False
