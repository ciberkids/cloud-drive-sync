"""Nextcloud provider implementation."""

from cloud_drive_sync.providers.nextcloud.auth import NextcloudAuth
from cloud_drive_sync.providers.nextcloud.changes import NextcloudChangePoller
from cloud_drive_sync.providers.nextcloud.client import NextcloudClient
from cloud_drive_sync.providers.nextcloud.operations import NextcloudFileOps
from cloud_drive_sync.providers.nextcloud.push import NextcloudPushPoller
from cloud_drive_sync.providers.registry import register


def make_change_poller(client, *, force_polling: bool = False) -> NextcloudPushPoller:
    """Build the change poller for a Nextcloud pair.

    Always returns the push-preferring poller. It discovers notify_push support at
    runtime and degrades to the ETag walk when the server does not advertise it,
    so this is safe for every instance and needs no user configuration (#56).
    ``force_polling`` is the escape hatch for when the automatic choice is wrong.
    """
    return NextcloudPushPoller(
        client, NextcloudChangePoller(client), force_polling=force_polling
    )

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
    poller_cls=make_change_poller,
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
    "NextcloudPushPoller",
    "make_change_poller",
]
