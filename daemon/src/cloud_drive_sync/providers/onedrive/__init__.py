"""OneDrive provider implementation."""

from cloud_drive_sync.providers.onedrive.auth import OneDriveAuth
from cloud_drive_sync.providers.onedrive.changes import OneDriveChangePoller
from cloud_drive_sync.providers.onedrive.client import OneDriveClient
from cloud_drive_sync.providers.onedrive.operations import OneDriveFileOps
from cloud_drive_sync.providers.registry import register

# Check if required packages are installed
_available = False
try:
    import msgraph  # noqa: F401
    import azure.identity  # noqa: F401
    _available = True
except ImportError:
    pass

# Register the OneDrive provider
register(
    "onedrive",
    client_cls=OneDriveClient,
    ops_cls=OneDriveFileOps,
    poller_cls=OneDriveChangePoller,
    auth_cls=OneDriveAuth,
    available=_available,
    display_name="OneDrive",
    description="Microsoft OneDrive via Microsoft Graph API",
)

__all__ = [
    "OneDriveAuth",
    "OneDriveChangePoller",
    "OneDriveClient",
    "OneDriveFileOps",
]
