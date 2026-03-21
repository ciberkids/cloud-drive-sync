"""Data models for the sync state database."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


class FileState(enum.Enum):
    """Possible states for a tracked file."""

    UNKNOWN = "unknown"
    SYNCED = "synced"
    PENDING_UPLOAD = "pending_upload"
    PENDING_DOWNLOAD = "pending_download"
    UPLOADING = "uploading"
    DOWNLOADING = "downloading"
    CONFLICT = "conflict"
    ERROR = "error"


@dataclass
class SyncEntry:
    """A tracked file in the sync state database."""

    path: str
    local_md5: str | None = None
    remote_md5: str | None = None
    remote_id: str | None = None
    state: FileState = FileState.UNKNOWN
    local_mtime: float | None = None
    remote_mtime: float | None = None
    last_synced: datetime | None = None
    pair_id: str = ""
    remote_native_mime: str | None = None  # For Google Docs conversion tracking

    def to_row(self) -> tuple:
        return (
            self.path,
            self.local_md5,
            self.remote_md5,
            self.remote_id,
            self.state.value,
            self.local_mtime,
            self.remote_mtime,
            self.last_synced.isoformat() if self.last_synced else None,
            self.pair_id,
            self.remote_native_mime,
        )

    @classmethod
    def from_row(cls, row: tuple) -> SyncEntry:
        return cls(
            path=row[0],
            local_md5=row[1],
            remote_md5=row[2],
            remote_id=row[3],
            state=FileState(row[4]),
            local_mtime=row[5],
            remote_mtime=row[6],
            last_synced=datetime.fromisoformat(row[7]) if row[7] else None,
            pair_id=row[8],
            remote_native_mime=row[9] if len(row) > 9 else None,
        )


@dataclass
class ChangeToken:
    """Stored change polling token for a sync pair."""

    pair_id: str
    token: str
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ConflictRecord:
    """A recorded file conflict awaiting resolution."""

    id: int | None = None
    path: str = ""
    pair_id: str = ""
    local_md5: str = ""
    remote_md5: str = ""
    local_mtime: float = 0.0
    remote_mtime: float = 0.0
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved: bool = False
    resolution: str | None = None

    def to_row(self) -> tuple:
        return (
            self.path,
            self.pair_id,
            self.local_md5,
            self.remote_md5,
            self.local_mtime,
            self.remote_mtime,
            self.detected_at.isoformat(),
            self.resolved,
            self.resolution,
        )

    @classmethod
    def from_row(cls, row: tuple) -> ConflictRecord:
        return cls(
            id=row[0],
            path=row[1],
            pair_id=row[2],
            local_md5=row[3],
            remote_md5=row[4],
            local_mtime=row[5],
            remote_mtime=row[6],
            detected_at=datetime.fromisoformat(row[7]),
            resolved=bool(row[8]),
            resolution=row[9],
        )


@dataclass
class SyncLogEntry:
    """An entry in the sync activity log."""

    id: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    action: str = ""
    path: str = ""
    pair_id: str = ""
    status: str = ""
    detail: str = ""

    def to_row(self) -> tuple:
        return (
            self.timestamp.isoformat(),
            self.action,
            self.path,
            self.pair_id,
            self.status,
            self.detail,
        )

    @classmethod
    def from_row(cls, row: tuple) -> SyncLogEntry:
        return cls(
            id=row[0],
            timestamp=datetime.fromisoformat(row[1]),
            action=row[2],
            path=row[3],
            pair_id=row[4],
            status=row[5],
            detail=row[6],
        )


@dataclass
class PartialTransfer:
    """Tracks an interrupted file transfer for resumption."""

    path: str = ""
    pair_id: str = ""
    direction: Literal["upload", "download"] = "upload"
    remote_id: str | None = None
    upload_uri: str | None = None
    bytes_transferred: int = 0
    total_size: int = 0
    temp_path: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_row(self) -> tuple:
        return (
            self.path,
            self.pair_id,
            self.direction,
            self.remote_id,
            self.upload_uri,
            self.bytes_transferred,
            self.total_size,
            self.temp_path,
            self.created_at.isoformat(),
        )

    @classmethod
    def from_row(cls, row: tuple) -> PartialTransfer:
        return cls(
            path=row[0],
            pair_id=row[1],
            direction=row[2],
            remote_id=row[3],
            upload_uri=row[4],
            bytes_transferred=row[5],
            total_size=row[6],
            temp_path=row[7],
            created_at=datetime.fromisoformat(row[8]),
        )
