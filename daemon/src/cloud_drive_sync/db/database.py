"""Async SQLite database wrapper for sync state."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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

SCHEMA_VERSION = 5

# A full VACUUM rewrites the whole database and blocks startup, so it only runs
# when a meaningful fraction *and* a meaningful absolute amount is wasted. The
# reported case was 4.4 GB at 99.998% free (issue #49).
VACUUM_FREELIST_RATIO = 0.25
VACUUM_MIN_RECLAIM_BYTES = 64 * 1024 * 1024

# Activity-history retention. sync_log is written on every poll cycle, and its
# churn is what drove the file's high-water mark.
SYNC_LOG_RETENTION_DAYS = 30
SYNC_LOG_PRUNE_LIMIT = 10_000

MEGABYTE = 1024 * 1024

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
    detail TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS pending_deletions (
    pair_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    count INTEGER NOT NULL,
    tracked INTEGER NOT NULL DEFAULT 0,
    limit_value INTEGER NOT NULL,
    sample TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (pair_id, direction)
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
        # Must come before journal_mode and before any table exists. On a new
        # database auto_vacuum can only be set while it is still empty, and
        # setting journal_mode=WAL first is enough to make this silently stick at
        # NONE. On an existing database it takes effect only after the next full
        # VACUUM, which _reclaim_free_pages performs when the waste warrants it.
        await self._db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._migrate()
        await self._reclaim_free_pages()
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

    # ── Space management ────────────────────────────────────────────

    def file_size(self) -> int:
        """Size of the main database file in bytes (0 if it does not exist)."""
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    async def _pragma(self, name: str) -> int:
        cursor = await self.db.execute(f"PRAGMA {name}")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def free_page_stats(self) -> tuple[int, int, int]:
        """Return ``(page_count, freelist_count, page_size)``."""
        return (
            await self._pragma("page_count"),
            await self._pragma("freelist_count"),
            await self._pragma("page_size"),
        )

    async def _reclaim_free_pages(self) -> None:
        """Return disk to the filesystem when the file is mostly freelist.

        SQLite never shrinks a database on DELETE: freed pages go on the
        freelist and are reused for later inserts, so a sync engine with high
        row churn keeps raising the high-water mark and never gives it back. A
        live instance reached 4.4 GB with every table empty (issue #49).
        """
        page_count, freelist, page_size = await self.free_page_stats()
        if not page_count:
            return

        reclaimable = freelist * page_size
        ratio = freelist / page_count
        if ratio < VACUUM_FREELIST_RATIO or reclaimable < VACUUM_MIN_RECLAIM_BYTES:
            return

        before = self.file_size()
        log.info(
            "Reclaiming %.1f MB of free database pages (%.2f%% of the file) — this "
            "rewrites %s and may take a while",
            reclaimable / MEGABYTE,
            ratio * 100,
            self._path,
        )
        # VACUUM cannot run inside a transaction, and aiosqlite opens one
        # implicitly for DML.
        await self.db.commit()
        await self.db.execute("VACUUM")
        await self.db.commit()
        # In WAL mode the rewrite lands in the WAL, and the main file keeps its
        # old size until a checkpoint — without this nothing is actually
        # returned to the filesystem.
        await self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        log.info(
            "Database reclaimed: %.1f MB -> %.1f MB",
            before / MEGABYTE,
            self.file_size() / MEGABYTE,
        )

    async def incremental_vacuum(self) -> int:
        """Release freed pages to the filesystem. Returns pages released.

        The pragma has to be stepped to completion: executing it without reading
        the result releases exactly one page, which looks like success and
        reclaims essentially nothing.
        """
        before = await self._pragma("freelist_count")
        if not before:
            return 0
        cursor = await self.db.execute("PRAGMA incremental_vacuum")
        await cursor.fetchall()
        await self.db.commit()
        return before - await self._pragma("freelist_count")

    async def prune_sync_log(self, retention_days: int = SYNC_LOG_RETENTION_DAYS) -> int:
        """Delete activity-log rows older than the retention window.

        Bounded per call so the first run against an already-huge table cannot
        stall the caller's loop; the next run continues where this one stopped.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        cursor = await self.db.execute(
            """DELETE FROM sync_log WHERE id IN (
                   SELECT id FROM sync_log WHERE timestamp < ? LIMIT ?
               )""",
            (cutoff, SYNC_LOG_PRUNE_LIMIT),
        )
        await self.db.commit()
        return max(cursor.rowcount, 0)

    async def maintain(self) -> None:
        """Periodic upkeep: prune old activity rows, then release free pages.

        Ordered deliberately — pruning creates the free pages that the
        incremental vacuum then hands back.
        """
        pruned = await self.prune_sync_log()
        released = await self.incremental_vacuum()
        if pruned or released:
            log.info(
                "Database maintenance: pruned %d log rows, released %d pages (%.1f MB file)",
                pruned,
                released,
                self.file_size() / MEGABYTE,
            )

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
                except Exception as exc:
                    # Expected when re-running against an already-migrated database.
                    # Logged so a genuine failure (disk full, corruption) leaves a trace
                    # instead of the schema version being bumped over a missing column.
                    log.debug("v2 migration step skipped: %s", exc)
            # Migration from v2 -> v3: add partial_transfers table
            if current_version < 3:
                try:
                    await self.db.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS pending_deletions (
    pair_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    count INTEGER NOT NULL,
    tracked INTEGER NOT NULL DEFAULT 0,
    limit_value INTEGER NOT NULL,
    sample TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (pair_id, direction)
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
                        CREATE INDEX IF NOT EXISTS idx_partial_transfers_pair
                            ON partial_transfers(pair_id);
                        """
                    )
                    log.info("Migrated database to v3: added partial_transfers table")
                except Exception as exc:
                    log.debug("v3 migration step skipped: %s", exc)
            # Migration from v3 -> v4: add reason column to sync_log
            if current_version < 4:
                try:
                    await self.db.execute(
                        "ALTER TABLE sync_log ADD COLUMN reason TEXT"
                    )
                    log.info("Migrated database to v4: added reason column to sync_log")
                except Exception as exc:
                    log.debug("v4 migration step skipped: %s", exc)
            # Migration from v4 -> v5: add pending_deletions table (#53)
            if current_version < 5:
                try:
                    await self.db.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS pending_deletions (
                            pair_id TEXT NOT NULL,
                            direction TEXT NOT NULL,
                            count INTEGER NOT NULL,
                            tracked INTEGER NOT NULL DEFAULT 0,
                            limit_value INTEGER NOT NULL,
                            sample TEXT NOT NULL DEFAULT '[]',
                            created_at TEXT NOT NULL,
                            PRIMARY KEY (pair_id, direction)
                        );
                        """
                    )
                    log.info("Migrated database to v5: added pending_deletions table")
                except Exception as exc:
                    log.debug("v5 migration step skipped: %s", exc)
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
            """INSERT INTO sync_log (timestamp, action, path, pair_id, status, detail, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            entry.to_row(),
        )
        await self.db.commit()

    async def get_recent_log(
        self,
        limit: int = 50,
        offset: int = 0,
        pair_id: str | None = None,
        status: str | None = None,
        actions: list[str] | None = None,
    ) -> list[SyncLogEntry]:
        conditions: list[str] = []
        params: list = []
        if pair_id:
            conditions.append("pair_id = ?")
            params.append(pair_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if actions:
            placeholders = ",".join("?" * len(actions))
            conditions.append(f"action IN ({placeholders})")
            params.extend(actions)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        cursor = await self.db.execute(
            f"SELECT id, timestamp, action, path, pair_id, status, detail, reason "
            f"FROM sync_log {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params,
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

        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()
        cursor = await self.db.execute(
            "DELETE FROM partial_transfers WHERE created_at < ?", (cutoff,)
        )
        await self.db.commit()
        count = cursor.rowcount
        if count:
            log.info("Cleaned up %d stale partial transfers", count)
        return count

    # ── Pending deletions (delete fail-safe, #53) ───────────────────

    async def record_pending_deletions(
        self, pair_id: str, direction: str, count: int, tracked: int, limit: int, sample: list[str]
    ) -> None:
        """Persist a refused deletion batch awaiting a human decision.

        Stored rather than held in memory so restarting the daemon cannot
        silently resolve the question — otherwise a container restart policy
        would quietly undo the safety decision.
        """
        await self.db.execute(
            """INSERT INTO pending_deletions
               (pair_id, direction, count, tracked, limit_value, sample, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(pair_id, direction) DO UPDATE SET
                 count=excluded.count,
                 tracked=excluded.tracked,
                 limit_value=excluded.limit_value,
                 sample=excluded.sample,
                 created_at=excluded.created_at""",
            (
                pair_id,
                direction,
                count,
                tracked,
                limit,
                json.dumps(sample),
                datetime.now(UTC).isoformat(),
            ),
        )
        await self.db.commit()

    async def get_pending_deletions(self, pair_id: str | None = None) -> list[dict]:
        """Refused batches awaiting a decision, newest first."""
        if pair_id:
            cursor = await self.db.execute(
                "SELECT * FROM pending_deletions WHERE pair_id = ? ORDER BY created_at DESC",
                (pair_id,),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM pending_deletions ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["sample"] = json.loads(item.get("sample") or "[]")
            except (TypeError, ValueError):
                item["sample"] = []
            item["limit"] = item.pop("limit_value", 0)
            result.append(item)
        return result

    async def clear_pending_deletions(self, pair_id: str, direction: str | None = None) -> int:
        """Drop the record once the user has decided. Returns rows removed."""
        if direction:
            cursor = await self.db.execute(
                "DELETE FROM pending_deletions WHERE pair_id = ? AND direction = ?",
                (pair_id, direction),
            )
        else:
            cursor = await self.db.execute(
                "DELETE FROM pending_deletions WHERE pair_id = ?", (pair_id,)
            )
        await self.db.commit()
        return max(cursor.rowcount, 0)

    # ── Utility ─────────────────────────────────────────────────────

    async def count_by_state(self, pair_id: str) -> dict[str, int]:
        cursor = await self.db.execute(
            "SELECT state, COUNT(*) FROM sync_state WHERE pair_id = ? GROUP BY state",
            (pair_id,),
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def prune_stale_entries(self, pair_id: str, known_paths: set[str]) -> int:
        """Delete sync_state rows for paths no longer in local or remote."""
        existing = await self.get_all_entries(pair_id)
        stale = [e.path for e in existing if e.path not in known_paths]
        for path in stale:
            await self.delete_sync_entry(path, pair_id)
        if stale:
            await self.db.commit()
            log.info("Pruned %d stale sync_state entries for %s", len(stale), pair_id)
        return len(stale)

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
