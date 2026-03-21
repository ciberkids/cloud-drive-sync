"""Box provider implementation."""

from cloud_drive_sync.providers.box.auth import BoxAuth
from cloud_drive_sync.providers.box.changes import BoxChangePoller
from cloud_drive_sync.providers.box.client import BoxClient
from cloud_drive_sync.providers.box.operations import BoxFileOps
from cloud_drive_sync.providers.registry import register

# Check if box-sdk-gen is installed
_available = False
try:
    import box_sdk_gen  # noqa: F401
    _available = True
except ImportError:
    pass

# Register the Box provider
register(
    "box",
    client_cls=BoxClient,
    ops_cls=BoxFileOps,
    poller_cls=BoxChangePoller,
    auth_cls=BoxAuth,
    available=_available,
    display_name="Box",
    description="Box cloud storage with chunked upload support",
)

__all__ = [
    "BoxAuth",
    "BoxChangePoller",
    "BoxClient",
    "BoxFileOps",
]
