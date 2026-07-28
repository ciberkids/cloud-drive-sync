"""Tests for the database size gauge in get_status (#49 follow-up).

Issue #49 was a 4.4 GB state.db holding zero rows. SQLite reuses freed pages but
never shrinks the file, so the bloat was invisible until someone looked at disk
usage. This surfaces it in the status payload — reclaimable_ratio being the
number that distinguishes "large because it holds data" from "large because it
is mostly dead space".
"""

from __future__ import annotations

from cloud_drive_sync.db.database import Database
from cloud_drive_sync.ipc.handlers import RequestHandler


async def _handler(tmp_path):
    db = Database(tmp_path / "state.db")
    await db.open()
    handler = RequestHandler.__new__(RequestHandler)
    handler._db = db
    handler._engine = None
    return handler, db


async def test_status_reports_database_size(tmp_path):
    handler, db = await _handler(tmp_path)
    try:
        info = await handler._database_info()
    finally:
        await db.close()

    assert info is not None
    assert info["size_bytes"] > 0
    assert info["page_count"] > 0
    assert info["size_formatted"].endswith(("B", "KB", "MB", "GB"))


async def test_reclaimable_ratio_is_low_on_a_fresh_database(tmp_path):
    handler, db = await _handler(tmp_path)
    try:
        info = await handler._database_info()
    finally:
        await db.close()

    assert 0.0 <= info["reclaimable_ratio"] < 0.5


async def test_reclaimable_ratio_rises_with_churn(tmp_path):
    """The signal that mattered in #49: rows deleted, file not shrunk."""
    handler, db = await _handler(tmp_path)
    try:
        await db.db.executemany(
            "INSERT INTO sync_log (timestamp, action, path, pair_id, status, detail) "
            "VALUES (?,?,?,?,?,?)",
            [("2026-01-01T00:00:00+00:00", "upload", f"f{i}", "pair_0", "ok", "x" * 2000)
             for i in range(8000)],
        )
        await db.db.commit()
        await db.db.execute("DELETE FROM sync_log")
        await db.db.commit()

        info = await handler._database_info()

        assert info["freelist_count"] > 100, "test needs real freed pages"
        assert info["reclaimable_ratio"] > 0.5
        assert info["reclaimable_bytes"] > 0
    finally:
        await db.close()


async def test_database_info_is_absent_rather_than_fatal_without_a_database():
    """get_status must still answer before the database is open."""
    handler = RequestHandler.__new__(RequestHandler)
    handler._db = None
    handler._engine = None

    assert await handler._database_info() is None


async def test_byte_formatting_is_human_readable():
    fmt = RequestHandler._format_bytes
    assert fmt(512) == "512 B"
    assert fmt(2048) == "2.0 KB"
    assert fmt(5 * 1024 * 1024) == "5.0 MB"
    assert fmt(4_724_464_026) == "4.4 GB"  # the size from the #49 report
