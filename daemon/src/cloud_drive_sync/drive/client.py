"""Google Drive API v3 wrapper.

This module re-exports from the providers.gdrive package for backward compatibility.
All new code should import from cloud_drive_sync.providers.gdrive.client instead.
"""

from __future__ import annotations

from cloud_drive_sync.providers.gdrive.client import (
    FIELDS_FILE,
    FOLDER_MIME,
)
from cloud_drive_sync.providers.gdrive.client import (
    GoogleDriveClient as DriveClient,
)

# Re-export for backward compatibility
__all__ = ["FIELDS_FILE", "FOLDER_MIME", "DriveClient"]
