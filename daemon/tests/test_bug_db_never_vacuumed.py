"""Regression tests for issue #49.

``state.db`` reached 4.4 GB while every table held 0 rows: SQLite returns deleted
pages to the freelist but never shrinks the file, ``auto_vacuum`` was NONE, and
nothing ever ran VACUUM. 1,141,251 of 1,141,268 pages were free.

Three mechanics decide whether a fix actually returns disk, and each one fails
silently if got wrong — so each is asserted directly:

* ``PRAGMA auto_vacuum`` must precede ``journal_mode``, or it stays at NONE.
* ``PRAGMA incremental_vacuum`` must be stepped, or it frees exactly one page.
* In WAL mode the main file keeps its old size until a checkpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cloud_drive_sync.db.database import (
    SYNC_LOG_PRUNE_LIMIT,
    SYNC_LOG_RETENTION_DAYS,
    VACUUM_MIN_RECLAIM_BYTES,
    Database,
)
from cloud_drive_sync.db.models import SyncLogEntry

ROW_PAYLOAD = "x" * 2000


async def _open(path):
    db = Database(path)
    await db.open()
    return db


async def _churn(db: Database, rows: int = 40_000) -> None:
    """Insert then delete rows, leaving a large freelist behind."""
    await db.db.executemany(
        "INSERT INTO sync_log (timestamp, action, path, pair_id, status) VALUES (?,?,?,?,?)",
        [
            (datetime.now(UTC).isoformat(), "upload", f"f{i}", "pair_0", "ok")
            for i in range(rows)
        ],
    )
    await db.db.execute("UPDATE sync_log SET detail = ?", (ROW_PAYLOAD,))
    await db.db.commit()
    await db.db.execute("DELETE FROM sync_log")
    await db.db.commit()


# ── auto_vacuum is actually enabled ─────────────────────────────────


@pytest.mark.asyncio
async def test_new_database_has_incremental_auto_vacuum(tmp_path):
    """2 == INCREMENTAL. This is the pragma that was NONE on the reported file."""
    db = await _open(tmp_path / "state.db")
    try:
        assert await db._pragma("auto_vacuum") == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_auto_vacuum_survives_wal_mode(tmp_path):
    """Ordering trap: setting journal_mode=WAL first leaves auto_vacuum at NONE.

    Both pragmas must hold together, so assert both rather than just the one
    being fixed.
    """
    db = await _open(tmp_path / "state.db")
    try:
        cursor = await db.db.execute("PRAGMA journal_mode")
        assert (await cursor.fetchone())[0].lower() == "wal"
        assert await db._pragma("auto_vacuum") == 2
    finally:
        await db.close()


# ── space is actually returned ──────────────────────────────────────


@pytest.mark.asyncio
async def test_incremental_vacuum_releases_every_free_page(tmp_path):
    """Stepping trap: an unstepped pragma frees one page and looks like success."""
    path = tmp_path / "state.db"
    db = await _open(path)
    try:
        await _churn(db)
        _, freelist_before, _ = await db.free_page_stats()
        assert freelist_before > 1000, "test needs a real freelist to reclaim"

        released = await db.incremental_vacuum()

        assert released > 1, "only one page released — the pragma was not stepped"
        assert released >= freelist_before - 1
        _, freelist_after, _ = await db.free_page_stats()
        assert freelist_after == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_incremental_vacuum_shrinks_the_file_on_disk(tmp_path):
    """The bug was disk usage, so assert bytes — not just page counters."""
    path = tmp_path / "state.db"
    db = await _open(path)
    try:
        await _churn(db)
        size_before = db.file_size()
        assert size_before > 10 * 1024 * 1024, "test needs a big file to shrink"

        await db.incremental_vacuum()

        assert db.file_size() < size_before / 10
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_incremental_vacuum_is_a_no_op_on_a_clean_database(tmp_path):
    db = await _open(tmp_path / "state.db")
    try:
        assert await db.incremental_vacuum() == 0
    finally:
        await db.close()


# ── existing bloated databases are migrated ─────────────────────────


@pytest.mark.asyncio
async def test_open_reclaims_a_legacy_bloated_database(tmp_path):
    """The reported file: auto_vacuum=NONE, near-100% freelist, multi-GB.

    incremental_vacuum cannot help such a file — auto_vacuum was never on — so
    open() must run a full VACUUM to both reclaim and enable it going forward.
    """
    import aiosqlite

    path = tmp_path / "state.db"

    # Build a database the way the old code did: WAL, no auto_vacuum.
    legacy = await aiosqlite.connect(path)
    await legacy.execute("PRAGMA journal_mode=WAL")
    await legacy.execute(
        "CREATE TABLE sync_log (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
        "action TEXT NOT NULL, path TEXT NOT NULL, pair_id TEXT NOT NULL, status TEXT NOT NULL, "
        "detail TEXT, reason TEXT)"
    )
    await legacy.executemany(
        "INSERT INTO sync_log (timestamp, action, path, pair_id, status, detail) "
        "VALUES (?,?,?,?,?,?)",
        [("2026-01-01T00:00:00+00:00", "upload", f"f{i}", "pair_0", "ok", "x" * 2500)
         for i in range(45_000)],
    )
    await legacy.commit()
    await legacy.execute("DELETE FROM sync_log")
    await legacy.commit()
    await legacy.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await legacy.close()

    size_before = path.stat().st_size
    assert size_before > VACUUM_MIN_RECLAIM_BYTES, (
        f"test fixture must exceed the reclaim threshold, got {size_before}"
    )

    db = Database(path)
    await db.open()
    try:
        assert db.file_size() < size_before / 10, "open() did not reclaim the free pages"
        # And it is now on incremental auto_vacuum, so this cannot recur.
        assert await db._pragma("auto_vacuum") == 2
        _, freelist, _ = await db.free_page_stats()
        assert freelist == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_open_leaves_a_healthy_database_alone(tmp_path):
    """A full VACUUM blocks startup, so it must not fire on ordinary databases."""
    path = tmp_path / "state.db"
    db = await _open(path)
    try:
        for i in range(50):
            await db.add_log_entry(
                SyncLogEntry(
                    timestamp=datetime.now(UTC),
                    action="upload",
                    path=f"f{i}",
                    pair_id="pair_0",
                    status="ok",
                )
            )
    finally:
        await db.close()

    size_before = path.stat().st_size
    db = Database(path)
    await db.open()
    try:
        # Nothing was rewritten and nothing was lost.
        assert db.file_size() == size_before
        assert len(await db.get_recent_log(limit=100)) == 50
    finally:
        await db.close()


# ── retention ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prune_removes_only_rows_past_retention(tmp_path):
    db = await _open(tmp_path / "state.db")
    try:
        now = datetime.now(UTC)
        for age_days, name in ((1, "recent"), (10, "midlife"), (400, "ancient")):
            await db.add_log_entry(
                SyncLogEntry(
                    timestamp=now - timedelta(days=age_days),
                    action="upload",
                    path=name,
                    pair_id="pair_0",
                    status="ok",
                )
            )

        pruned = await db.prune_sync_log()

        assert pruned == 1
        kept = {e.path for e in await db.get_recent_log(limit=100)}
        assert kept == {"recent", "midlife"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_prune_keeps_a_month_of_history_by_default(tmp_path):
    """Retention is user-visible; a row just inside the window must survive."""
    db = await _open(tmp_path / "state.db")
    try:
        now = datetime.now(UTC)
        await db.add_log_entry(
            SyncLogEntry(
                timestamp=now - timedelta(days=SYNC_LOG_RETENTION_DAYS - 1),
                action="upload",
                path="inside",
                pair_id="pair_0",
                status="ok",
            )
        )
        await db.add_log_entry(
            SyncLogEntry(
                timestamp=now - timedelta(days=SYNC_LOG_RETENTION_DAYS + 1),
                action="upload",
                path="outside",
                pair_id="pair_0",
                status="ok",
            )
        )

        await db.prune_sync_log()

        assert {e.path for e in await db.get_recent_log(limit=10)} == {"inside"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_prune_is_bounded_per_call(tmp_path):
    """An unbounded first run against a huge table would stall the caller's loop."""
    db = await _open(tmp_path / "state.db")
    try:
        old = (datetime.now(UTC) - timedelta(days=90)).isoformat()
        await db.db.executemany(
            "INSERT INTO sync_log (timestamp, action, path, pair_id, status) VALUES (?,?,?,?,?)",
            [(old, "upload", f"f{i}", "pair_0", "ok") for i in range(SYNC_LOG_PRUNE_LIMIT + 500)],
        )
        await db.db.commit()

        first = await db.prune_sync_log()
        assert first == SYNC_LOG_PRUNE_LIMIT

        # The next run continues where this one stopped.
        second = await db.prune_sync_log()
        assert second == 500
        assert await db.prune_sync_log() == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_maintain_prunes_then_reclaims(tmp_path):
    """Ordering matters: pruning creates the pages the vacuum hands back."""
    path = tmp_path / "state.db"
    db = await _open(path)
    try:
        old = (datetime.now(UTC) - timedelta(days=90)).isoformat()
        await db.db.executemany(
            "INSERT INTO sync_log (timestamp, action, path, pair_id, status, detail) "
            "VALUES (?,?,?,?,?,?)",
            [(old, "upload", f"f{i}", "pair_0", "ok", ROW_PAYLOAD) for i in range(8000)],
        )
        await db.db.commit()
        size_before = db.file_size()

        await db.maintain()

        assert db.file_size() < size_before
        _, freelist, _ = await db.free_page_stats()
        assert freelist == 0
        assert await db.get_recent_log(limit=5) == []
    finally:
        await db.close()
