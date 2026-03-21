"""High-level file operations: upload, download, delete.

This module re-exports from the providers.gdrive package for backward compatibility.
All new code should import from cloud_drive_sync.providers.gdrive.operations instead.
"""

from __future__ import annotations

from cloud_drive_sync.providers.gdrive.operations import (
    GoogleDriveFileOps as FileOperations,
    _format_size,
    _format_speed,
)

# Re-export for backward compatibility
__all__ = ["FileOperations", "_format_size", "_format_speed"]
