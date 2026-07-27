"""Nextcloud provider implementation."""

from cloud_drive_sync.providers.nextcloud.auth import NextcloudAuth
from cloud_drive_sync.providers.nextcloud.changes import NextcloudChangePoller
from cloud_drive_sync.providers.nextcloud.client import NextcloudClient
from cloud_drive_sync.providers.nextcloud.operations import NextcloudFileOps
from cloud_drive_sync.providers.registry import register

# Check if nc-py-api is available
_available = True
try:
    import nc_py_api  # noqa: F401
except ImportError:
    _available = False
else:
    # Correct nc-py-api's in-place mutation of its PROPFIND property lists
    # before any client can issue a request (issue #47).
    from cloud_drive_sync.providers.nextcloud import nc_patch

    nc_patch.apply()

# Register the Nextcloud provider
register(
    "nextcloud",
    client_cls=NextcloudClient,
    ops_cls=NextcloudFileOps,
    poller_cls=NextcloudChangePoller,
    auth_cls=NextcloudAuth,
    available=_available,
    display_name="Nextcloud",
    description="Nextcloud/ownCloud via WebDAV (self-hosted)",
)

__all__ = [
    "NextcloudAuth",
    "NextcloudChangePoller",
    "NextcloudClient",
    "NextcloudFileOps",
]
