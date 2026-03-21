"""Dropbox provider implementation."""

try:
    import dropbox as _dropbox_lib  # noqa: F401

    _available = True
except ImportError:
    _available = False

from cloud_drive_sync.providers.dropbox.auth import DropboxAuth
from cloud_drive_sync.providers.dropbox.changes import DropboxChangePoller
from cloud_drive_sync.providers.dropbox.client import DropboxClient
from cloud_drive_sync.providers.dropbox.operations import DropboxFileOps
from cloud_drive_sync.providers.registry import register

register(
    "dropbox",
    client_cls=DropboxClient,
    ops_cls=DropboxFileOps,
    poller_cls=DropboxChangePoller,
    auth_cls=DropboxAuth,
    available=_available,
    display_name="Dropbox",
    description="Dropbox cloud storage with content hash sync",
)

__all__ = [
    "DropboxAuth",
    "DropboxChangePoller",
    "DropboxClient",
    "DropboxFileOps",
]
