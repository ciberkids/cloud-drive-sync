"""Auth lifecycle rows were silently discarded on the IPC and HTTP paths (#58).

``Daemon._log_auth_event`` scheduled its database write with
``asyncio.get_event_loop()`` under a bare ``except RuntimeError: pass``. But
``_do_auth`` does not run on the event loop -- the IPC and HTTP handlers invoke it via
``await asyncio.to_thread(self._auth_callback, ...)``, and in a worker thread
``get_event_loop()`` raises::

    RuntimeError: There is no current event loop in thread 'asyncio_0'.

The bare except swallowed it, so all four rows vanished: authentication started,
succeeded, failed, and code-exchange succeeded. The failure one is the one that
mattered -- an expired credential left no trace in the activity log.

The bug hid because the log is not *wholly* blank for auth: ``_start_auth`` writes its
own rows from the async context, and those always worked. Only the daemon-side
lifecycle rows disappeared.

The first test here reproduces the raise directly, so the reason for using
``self._loop`` is pinned rather than implied -- if a future Python makes
``get_event_loop()`` succeed in a worker thread, that test tells us the constraint has
changed instead of quietly passing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cloud_drive_sync.daemon import Daemon
from cloud_drive_sync.db.database import Database


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "auth_events.db")
    await database.open()
    yield database
    await database.close()


class TestTheUnderlyingConstraint:
    @pytest.mark.asyncio
    async def test_get_event_loop_raises_in_a_to_thread_worker(self):
        """The reason `self._loop` is required. Pinned so a behaviour change surfaces."""

        def worker():
            try:
                asyncio.get_event_loop()
            except RuntimeError as exc:
                return str(exc)
            return None

        assert await asyncio.to_thread(worker) is not None, (
            "get_event_loop() no longer raises in a worker thread -- revisit #58"
        )


class TestAuthRowsSurviveTheWorkerThread:
    @pytest.mark.asyncio
    async def test_an_auth_event_raised_from_a_worker_thread_is_recorded(self, db, tmp_path):
        """The regression test for #58, on the real call path.

        `_log_auth_event` is invoked from a worker thread exactly as `_do_auth` does.
        """
        daemon = Daemon(config_path=tmp_path / "config.toml")
        daemon._db = db
        daemon._loop = asyncio.get_running_loop()

        await asyncio.to_thread(
            daemon._log_auth_event, "auth", "Authentication failed: token expired", "error"
        )
        await asyncio.sleep(0.1)  # let the scheduled coroutine run

        rows = await db.get_recent_log(limit=10)
        auth_rows = [r for r in rows if r.action == "auth"]
        assert auth_rows, "the auth row was dropped -- #58 has regressed"
        assert auth_rows[0].status == "error"
        assert "token expired" in auth_rows[0].detail
        assert auth_rows[0].pair_id == "_system"

    @pytest.mark.asyncio
    async def test_all_four_lifecycle_statuses_are_recorded(self, db, tmp_path):
        daemon = Daemon(config_path=tmp_path / "config.toml")
        daemon._db = db
        daemon._loop = asyncio.get_running_loop()

        events = [
            ("auth", "Authentication started (gdrive)", "in_progress"),
            ("auth", "Authentication successful (me@example.com)", "success"),
            ("auth", "Authentication failed: bad code", "error"),
            ("auth", "Authentication successful (other@example.com)", "success"),
        ]
        for action, detail, status in events:
            await asyncio.to_thread(daemon._log_auth_event, action, detail, status)
        await asyncio.sleep(0.2)

        rows = [r for r in await db.get_recent_log(limit=20) if r.action == "auth"]
        assert len(rows) == len(events), f"expected {len(events)} auth rows, got {len(rows)}"
        assert {r.status for r in rows} == {"in_progress", "success", "error"}

    @pytest.mark.asyncio
    async def test_no_loop_yet_is_survived_without_raising(self, db, tmp_path):
        """Before `run()` sets `_loop` there is nothing to schedule on. That must warn
        and return, not raise into the caller -- an auth flow must not die because its
        bookkeeping could not be written."""
        daemon = Daemon(config_path=tmp_path / "config.toml")
        daemon._db = db
        daemon._loop = None

        daemon._log_auth_event("auth", "no loop yet", "error")  # must not raise

    @pytest.mark.asyncio
    async def test_no_database_yet_is_survived_without_raising(self, tmp_path):
        daemon = Daemon(config_path=tmp_path / "config.toml")
        daemon._db = None
        daemon._loop = asyncio.get_running_loop()

        daemon._log_auth_event("auth", "no db yet", "error")  # must not raise
