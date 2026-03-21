"""Async SQLite database wrapper for sync state."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from cloud_drive_sync.db.models import (
    ChangeToken,
    ConflictRecord,
    FileState,
    PartialTransfer,
    SyncEntry,
    SyncLogEntry,
)
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.paths import db_path

log = get_logger("database")

SCHEMA_VERSION = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    path TEXT NOT NULL,
    pair_id TEXT NOT NULL,
    local_md5 TEXT,
    remote_md5 TEXT,
    remote_id TEXT,
    state TEXT NOT NULL DEFAULT 'unknown',
    local_mtime REAL,
    remote_mtime REAL,
    last_synced TEXT,
    remote_native_mime TEXT,
    PRIMARY KEY (path, pair_id)
);

CREATE TABLE IF NOT EXISTS change_tokens (
    pair_id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    pair_id TEXT NOT NULL,
    local_md5 TEXT NOT NULL,
    remote_md5 TEXT NOT NULL,
    local_mtime REAL NOT NULL,
    remote_mtime REAL NOT NULL,
    detected_at TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    resolution TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    path TEXT NOT NULL,
    pair_id TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS partial_transfers (
    path TEXT NOT NULL,
    pair_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    remote_id TEXT,
    upload_uri TEXT,
    bytes_transferred INTEGER NOT NULL DEFAULT 0,
    total_size INTEGER NOT NULL DEFAULT 0,
    temp_path TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (path, pair_id)
);

CREATE INDEX IF NOT EXISTS idx_sync_state_pair ON sync_state(pair_id);
CREATE INDEX IF NOT EXISTS idx_sync_state_state ON sync_state(state);
CREATE INDEX IF NOT EXISTS idx_conflicts_unresolved ON conflicts(resolved) WHERE resolved = 0;
CREATE INDEX IF NOT EXISTS idx_sync_log_ts ON sync_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_partial_transfers_pair ON partial_transfers(pair_id);
"""


class Database:
    """Async wrapper around the SQLite sync-state database."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or db_path()
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open the database and ensure the schema exists."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._migrate()
        log.info("Database opened at %s", self._path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not opened")
        return self._db

    async def _migrate(self) -> None:
        """Run schema creation and any necessary migrations."""
        await self.db.executescript(SCHEMA_SQL)
        cursor = await self.db.execute("SELECT version FROM schema_version")
        row = await cursor.fetchone()
        current_version = row[0] if row else 0

        if current_version == 0:
            await self.db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        elif current_version < SCHEMA_VERSION:
            # Migration from v1 -> v2: add remote_native_mime column
            if current_version < 2:
                try:
                    await self.db.execute(
                        "ALTER TABLE sync_state ADD COLUMN remote_native_mime TEXT"
                    )
                    log.info("Migrated database to v2: added remote_native_mime column")
                except Exception:
                    pass  # Column may already exist
            # Migration from v2 -> v3: add partial_transfers table
            if current_version < 3:
                try:
                    await self.db.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS partial_transfers (
                            path TEXT NOT NULL,
                            pair_id TEXT NOT NULL,
                            direction TEXT NOT NULL,
                            remote_id TEXT,
                            upload_uri TEXT,
                            bytes_transferred INTEGER NOT NULL DEFAULT 0,
                            total_size INTEGER NOT NULL DEFAULT 0,
                            temp_path TEXT,
                            created_at TEXT NOT NULL,
                            PRIMARY KEY (path, pair_id)
                        );
                        CREATE INDEX IF NOT EXISTS idx_partial_transfers_pair
                            ON partial_transfers(pair_id);
                        """
                    )
                    log.info("Migrated database to v3: added partial_transfers table")
                except Exception:
                    pass  # Table may already exist
            await self.db.execute(
                "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,)
            )
        await self.db.commit()

    # ── SyncEntry CRUD ──────────────────────────────────────────────

    async def upsert_sync_entry(self, entry: SyncEntry) -> None:
        await self.db.execute(
            """INSERT INTO sync_state
               (path, pair_id, local_md5, remote_md5, remote_id, state,
                local_mtime, remote_mtime, last_synced, remote_native_mime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path, pair_id) DO UPDATE SET
                 local_md5=excluded.local_md5,
                 remote_md5=excluded.remote_md5,
                 remote_id=excluded.remote_id,
                 state=excluded.state,
                 local_mtime=excluded.local_mtime,
                 remote_mtime=excluded.remote_mtime,
                 last_synced=excluded.last_synced,
                 remote_native_mime=excluded.remote_native_mime""",
            (
                entry.path,
                entry.pair_id,
                entry.local_md5,
                entry.remote_md5,
                entry.remote_id,
                entry.state.value,
                entry.local_mtime,
                entry.remote_mtime,
                entry.last_synced.isoformat() if entry.last_synced else None,
                entry.remote_native_mime,
            ),
        )
        await self.db.commit()

    async def get_sync_entry(self, path: str, pair_id: str) -> SyncEntry | None:
        cursor = await self.db.execute(
            "SELECT path, local_md5, remote_md5, remote_id, state, "
            "local_mtime, remote_mtime, last_synced, pair_id, remote_native_mime "
            "FROM sync_state WHERE path = ? AND pair_id = ?",
            (path, pair_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return SyncEntry.from_row(tuple(row))

    async def get_all_entries(self, pair_id: str) -> list[SyncEntry]:
        cursor = await self.db.execute(
            "SELECT path, local_md5, remote_md5, remote_id, state, "
            "local_mtime, remote_mtime, last_synced, pair_id, remote_native_mime "
            "FROM sync_state WHERE pair_id = ?",
            (pair_id,),
        )
        rows = await cursor.fetchall()
        return [SyncEntry.from_row(tuple(r)) for r in rows]

    async def delete_sync_entry(self, path: str, pair_id: str) -> None:
        await self.db.execute(
            "DELETE FROM sync_state WHERE path = ? AND pair_id = ?", (path, pair_id)
        )
        await self.db.commit()

    async def delete_sync_entries_by_prefix(self, path_prefix: str, pair_id: str) -> int:
        """Delete all entries whose path starts with path_prefix/"""
        cursor = await self.db.execute(
            "DELETE FROM sync_state WHERE path LIKE ? AND pair_id = ?",
            (path_prefix + "/%", pair_id),
        )
        await self.db.commit()
        return cursor.rowcount

    async def get_entries_by_state(self, state: FileState, pair_id: str) -> list[SyncEntry]:
        cursor = await self.db.execute(
            "SELECT path, local_md5, remote_md5, remote_id, state, "
            "local_mtime, remote_mtime, last_synced, pair_id, remote_native_mime "
            "FROM sync_state WHERE state = ? AND pair_id = ?",
            (state.value, pair_id),
        )
        rows = await cursor.fetchall()
        return [SyncEntry.from_row(tuple(r)) for r in rows]

    # ── ChangeToken CRUD ────────────────────────────────────────────

    async def get_change_token(self, pair_id: str) -> ChangeToken | None:
        cursor = await self.db.execute(
            "SELECT pair_id, token, updated_at FROM change_tokens WHERE pair_id = ?",
            (pair_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return ChangeToken(
            pair_id=row[0],
            token=row[1],
            updated_at=datetime.fromisoformat(row[2]),
        )

    async def upsert_change_token(self, ct: ChangeToken) -> None:
        await self.db.execute(
            """INSERT INTO change_tokens (pair_id, token, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(pair_id) DO UPDATE SET
                 token=excluded.token, updated_at=excluded.updated_at""",
            (ct.pair_id, ct.token, ct.updated_at.isoformat()),
        )
        await self.db.commit()

    # ── Conflict CRUD ───────────────────────────────────────────────

    async def add_conflict(self, conflict: ConflictRecord) -> int:
        cursor = await self.db.execute(
            """INSERT INTO conflicts
               (path, pair_id, local_md5, remote_md5, local_mtime,
                remote_mtime, detected_at, resolved, resolution)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            conflict.to_row(),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_unresolved_conflicts(self, pair_id: str | None = None) -> list[ConflictRecord]:
        if pair_id:
            cursor = await self.db.execute(
                "SELECT id, path, pair_id, local_md5, remote_md5, local_mtime, "
                "remote_mtime, detected_at, resolved, resolution "
                "FROM conflicts WHERE resolved = 0 AND pair_id = ?",
                (pair_id,),
            )
        else:
            cursor = await self.db.execute(
                "SELECT id, path, pair_id, local_md5, remote_md5, local_mtime, "
                "remote_mtime, detected_at, resolved, resolution "
                "FROM conflicts WHERE resolved = 0"
            )
        rows = await cursor.fetchall()
        return [ConflictRecord.from_row(tuple(r)) for r in rows]

    async def resolve_conflict(self, conflict_id: int, resolution: str) -> None:
        await self.db.execute(
            "UPDATE conflicts SET resolved = 1, resolution = ? WHERE id = ?",
            (resolution, conflict_id),
        )
        await self.db.commit()

    # ── SyncLog CRUD ────────────────────────────────────────────────

    async def add_log_entry(self, entry: SyncLogEntry) -> None:
        await self.db.execute(
            """INSERT INTO sync_log (timestamp, action, path, pair_id, status, detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            entry.to_row(),
        )
        await self.db.commit()

    async def get_recent_log(
        self, limit: int = 50, offset: int = 0, pair_id: str | None = None
    ) -> list[SyncLogEntry]:
        if pair_id:
            cursor = await self.db.execute(
                "SELECT id, timestamp, action, path, pair_id, status, detail "
                "FROM sync_log WHERE pair_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (pair_id, limit, offset),
            )
        else:
            cursor = await self.db.execute(
                "SELECT id, timestamp, action, path, pair_id, status, detail "
                "FROM sync_log ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = await cursor.fetchall()
        return [SyncLogEntry.from_row(tuple(r)) for r in rows]

    # ── PartialTransfer CRUD ──────────────────────────────────────────

    async def upsert_partial_transfer(self, pt: PartialTransfer) -> None:
        await self.db.execute(
            """INSERT INTO partial_transfers
               (path, pair_id, direction, remote_id, upload_uri,
                bytes_transferred, total_size, temp_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path, pair_id) DO UPDATE SET
                 direction=excluded.direction,
                 remote_id=excluded.remote_id,
                 upload_uri=excluded.upload_uri,
                 bytes_transferred=excluded.bytes_transferred,
                 total_size=excluded.total_size,
                 temp_path=excluded.temp_path,
                 created_at=excluded.created_at""",
            pt.to_row(),
        )
        await self.db.commit()

    async def get_partial_transfer(self, path: str, pair_id: str) -> PartialTransfer | None:
        cursor = await self.db.execute(
            "SELECT path, pair_id, direction, remote_id, upload_uri, "
            "bytes_transferred, total_size, temp_path, created_at "
            "FROM partial_transfers WHERE path = ? AND pair_id = ?",
            (path, pair_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return PartialTransfer.from_row(tuple(row))

    async def delete_partial_transfer(self, path: str, pair_id: str) -> None:
        await self.db.execute(
            "DELETE FROM partial_transfers WHERE path = ? AND pair_id = ?",
            (path, pair_id),
        )
        await self.db.commit()

    async def get_all_partial_transfers(self, pair_id: str | None = None) -> list[PartialTransfer]:
        if pair_id:
            cursor = await self.db.execute(
                "SELECT path, pair_id, direction, remote_id, upload_uri, "
                "bytes_transferred, total_size, temp_path, created_at "
                "FROM partial_transfers WHERE pair_id = ?",
                (pair_id,),
            )
        else:
            cursor = await self.db.execute(
                "SELECT path, pair_id, direction, remote_id, upload_uri, "
                "bytes_transferred, total_size, temp_path, created_at "
                "FROM partial_transfers"
            )
        rows = await cursor.fetchall()
        return [PartialTransfer.from_row(tuple(r)) for r in rows]

    async def cleanup_stale_partial_transfers(self, max_age_days: int = 7) -> int:
        """Delete partial transfer records older than max_age_days."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        cursor = await self.db.execute(
            "DELETE FROM partial_transfers WHERE created_at < ?", (cutoff,)
        )
        await self.db.commit()
        count = cursor.rowcount
        if count:
            log.info("Cleaned up %d stale partial transfers", count)
        return count

    # ── Utility ─────────────────────────────────────────────────────

    async def count_by_state(self, pair_id: str) -> dict[str, int]:
        cursor = await self.db.execute(
            "SELECT state, COUNT(*) FROM sync_state WHERE pair_id = ? GROUP BY state",
            (pair_id,),
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def clear_pair(self, pair_id: str) -> None:
        await self.db.execute("DELETE FROM sync_state WHERE pair_id = ?", (pair_id,))
        await self.db.execute("DELETE FROM change_tokens WHERE pair_id = ?", (pair_id,))
        await self.db.execute("DELETE FROM conflicts WHERE pair_id = ?", (pair_id,))
        await self.db.execute("DELETE FROM sync_log WHERE pair_id = ?", (pair_id,))
        await self.db.execute("DELETE FROM partial_transfers WHERE pair_id = ?", (pair_id,))
        await self.db.commit()

    async def cleanup_stale_pairs(self, active_pair_ids: set[str]) -> int:
        """Remove all data for pairs not in the active set."""
        cursor = await self.db.execute(
            "SELECT DISTINCT pair_id FROM sync_state "
            "UNION SELECT DISTINCT pair_id FROM change_tokens "
            "UNION SELECT DISTINCT pair_id FROM conflicts "
            "UNION SELECT DISTINCT pair_id FROM sync_log "
            "UNION SELECT DISTINCT pair_id FROM partial_transfers"
        )
        rows = await cursor.fetchall()
        all_pair_ids = {row[0] for row in rows}
        stale_ids = all_pair_ids - active_pair_ids - {"_system"}
        count = 0
        for pair_id in stale_ids:
            await self.clear_pair(pair_id)
            count += 1
        if count:
            log.info("Cleaned up %d stale pairs", count)
        return count
