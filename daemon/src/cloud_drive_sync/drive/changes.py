"""Remote change polling via the Drive Changes API.

This module re-exports from the providers.gdrive package for backward compatibility.
All new code should import from cloud_drive_sync.providers.gdrive.changes instead.
"""

from __future__ import annotations

from cloud_drive_sync.providers.gdrive.changes import (
    GoogleDriveChangePoller as ChangePoller,
)
from cloud_drive_sync.providers.gdrive.changes import (
    RemoteChange,
)

# Re-export for backward compatibility
__all__ = ["ChangePoller", "RemoteChange"]
