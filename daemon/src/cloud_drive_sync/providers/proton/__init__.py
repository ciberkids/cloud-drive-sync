"""Proton Drive provider implementation (stub)."""

from cloud_drive_sync.providers.proton.auth import ProtonDriveAuth
from cloud_drive_sync.providers.proton.changes import ProtonDriveChangePoller
from cloud_drive_sync.providers.proton.client import ProtonDriveClient
from cloud_drive_sync.providers.proton.operations import ProtonDriveFileOps
from cloud_drive_sync.providers.registry import register

# Register the Proton Drive provider (not yet available)
register(
    "proton",
    client_cls=ProtonDriveClient,
    ops_cls=ProtonDriveFileOps,
    poller_cls=ProtonDriveChangePoller,
    auth_cls=ProtonDriveAuth,
    available=False,
    display_name="Proton Drive",
    description="Proton Drive end-to-end encrypted storage (coming soon)",
)

__all__ = [
    "ProtonDriveAuth",
    "ProtonDriveChangePoller",
    "ProtonDriveClient",
    "ProtonDriveFileOps",
]
