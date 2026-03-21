"""Nautilus Python extension that shows sync-state overlay icons on files.

This module is loaded by nautilus-python when installed into
~/.local/share/nautilus-python/extensions/.  It reads the daemon's SQLite
database directly (read-only) so that overlays update without IPC round-trips.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from gi.repository import GObject, Nautilus  # type: ignore[import-untyped]
except ImportError:
    # Allow importing the module in environments without gi (e.g. tests)
    GObject = None  # type: ignore[assignment,misc]
    Nautilus = None  # type: ignore[assignment,misc]

# Map FileState values to Nautilus emblems
_STATE_EMBLEM_MAP: dict[str, str] = {
    "synced": "emblem-default",
    "uploading": "emblem-synchronizing",
    "downloading": "emblem-synchronizing",
    "pending_upload": "emblem-synchronizing",
    "pending_download": "emblem-synchronizing",
    "conflict": "emblem-important",
    "error": "emblem-unreadable",
}


def _db_path() -> Path:
    """Return the path to the daemon's state database."""
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "cloud-drive-sync" / "state.db"


def _config_path() -> Path:
    """Return the path to the daemon's config file."""
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "cloud-drive-sync" / "config.toml"


def _get_sync_roots() -> list[str]:
    """Read local_path entries from config.toml.

    Returns a list of absolute local paths that are being synced.
    Uses a simple parser to avoid depending on tomllib at runtime inside
    the Nautilus process.
    """
    cfg = _config_path()
    if not cfg.exists():
        return []
    roots: list[str] = []
    try:
        text = cfg.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("local_path"):
                # local_path = "/home/user/Drive"
                _, _, value = stripped.partition("=")
                value = value.strip().strip('"').strip("'")
                if value:
                    roots.append(value)
    except Exception:
        pass
    return roots


def _query_file_state(file_path: str) -> str | None:
    """Query the daemon database for a file's sync state.

    Opens the database in read-only mode with a short timeout.
    Returns the state string or None if not tracked.
    """
    db = _db_path()
    if not db.exists():
        return None

    try:
        uri = f"file:{db}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            cursor = conn.execute(
                "SELECT state FROM sync_state WHERE path = ? LIMIT 1",
                (file_path,),
            )
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _file_uri_to_path(uri: str) -> str | None:
    """Convert a file:// URI to an absolute filesystem path."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return unquote(parsed.path)


def _build_provider_class():
    """Dynamically build the Nautilus info-provider class.

    We build it at import time only if the gi bindings are available, so the
    module can still be imported in test environments without GTK.
    """
    if GObject is None or Nautilus is None:
        return None

    class SyncOverlayProvider(GObject.GObject, Nautilus.InfoProvider):
        """Nautilus extension that sets emblems based on sync state."""

        def __init__(self):
            super().__init__()
            self._sync_roots: list[str] | None = None

        @property
        def sync_roots(self) -> list[str]:
            if self._sync_roots is None:
                self._sync_roots = _get_sync_roots()
            return self._sync_roots

        def update_file_info(self, file_info) -> None:  # type: ignore[override]
            uri = file_info.get_uri()
            abs_path = _file_uri_to_path(uri)
            if abs_path is None:
                return

            # Check if the file is under a synced directory
            rel_path: str | None = None
            for root in self.sync_roots:
                if abs_path.startswith(root + "/"):
                    rel_path = abs_path[len(root) + 1:]
                    break
                if abs_path == root:
                    rel_path = ""
                    break

            if rel_path is None:
                return

            state = _query_file_state(rel_path)
            if state is None:
                return

            emblem = _STATE_EMBLEM_MAP.get(state)
            if emblem:
                file_info.add_emblem(emblem)

    return SyncOverlayProvider


# The class is registered at module scope so Nautilus discovers it.
SyncOverlayProvider = _build_provider_class()
