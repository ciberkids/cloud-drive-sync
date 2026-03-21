"""Google Drive provider implementation."""

from cloud_drive_sync.providers.gdrive.auth import GoogleDriveAuth
from cloud_drive_sync.providers.gdrive.changes import GoogleDriveChangePoller
from cloud_drive_sync.providers.gdrive.client import GoogleDriveClient
from cloud_drive_sync.providers.gdrive.operations import GoogleDriveFileOps
from cloud_drive_sync.providers.registry import register

# Register the Google Drive provider
register(
    "gdrive",
    client_cls=GoogleDriveClient,
    ops_cls=GoogleDriveFileOps,
    poller_cls=GoogleDriveChangePoller,
    auth_cls=GoogleDriveAuth,
    available=True,
    display_name="Google Drive",
    description="Google Drive with Shared Drives support",
)

__all__ = [
    "GoogleDriveAuth",
    "GoogleDriveChangePoller",
    "GoogleDriveClient",
    "GoogleDriveFileOps",
]
