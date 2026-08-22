"""A raising notify consumer could stop a pair syncing until restart (#59).

``_initial_sync`` awaited the notify callback twice -- ``sync_complete`` then
``status_changed`` -- with no exception guard, inside the ``try`` whose
``except Exception`` reports "Initial sync failed". What came *after* those awaits is
load-bearing::

    await self._db.upsert_change_token(ChangeToken(pair_id=pair_id, token=token))
    if not self._stop_event.is_set():
        await self._start_continuous(ps)

So a consumer that raised skipped the change-token upsert **and** skipped
``_start_continuous``. The pair never entered continuous sync, its change token was
never persisted, and the user saw "Sync failed: <consumer exception>" against a folder
that had quietly stopped syncing -- recoverable only by restarting the daemon.

The three other emission sites already guarded this (``delete_blocked``,
``activity_stopped``, ``activity_resumed`` all use ``contextlib.suppress``). This one
was the outlier, so the fix is to make it consistent.

Today the only consumer is ``ipc_server.notify_all``, which makes this latent rather
than live -- but the event bus the webhook feature adds turns "the only consumer" into
"one of several", and a misconfigured webhook must never be able to stop a sync.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cloud_drive_sync.config import Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.drive.mock_client import MockChangePoller, MockDriveClient, MockFileOperations
from cloud_drive_sync.sync.engine import SyncEngine


@pytest.fixture
def demo_dirs(tmp_path: Path):
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    return local, remote


@pytest.fixture
def config(demo_dirs):
    local, _ = demo_dirs
    cfg = Config()
    cfg.sync = SyncConfig(
        poll_interval=60,  # long, so background polling does not add noise
        conflict_strategy="keep_both",
        max_concurrent_transfers=2,
        debounce_delay=0.1,
        pairs=[SyncPair(local_path=str(local), remote_folder_id="root", enabled=True)],
    )
    return cfg


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "notify_exc.db")
    await database.open()
    yield database
    await database.close()


async def _run_engine_with_callback(config, db, mock_client, callback):
    ops = MockFileOperations(mock_client)
    poller = MockChangePoller(mock_client)
    engine = SyncEngine(config, db, mock_client, file_ops=ops, change_poller=poller)
    engine.set_notify_callback(callback)
    try:
        await engine.start()
        await asyncio.sleep(0.5)
        yield engine
    finally:
        await engine.stop()


class TestARaisingConsumerDoesNotBreakTheSyncPass:
    @pytest.mark.asyncio
    async def test_change_token_is_still_persisted(self, config, db, demo_dirs):
        """The upsert sits after the notify calls. A raising consumer used to skip it,
        so the next poll had no token to start from."""
        local, remote = demo_dirs
        (local / "file.txt").write_text("data")

        async def exploding(method, params):
            raise RuntimeError("consumer blew up")

        gen = _run_engine_with_callback(config, db, MockDriveClient(remote), exploding)
        async for _engine in gen:
            token = await db.get_change_token("pair_0")
            assert token is not None, (
                "the change token was not persisted -- the consumer exception "
                "propagated and aborted the pass (#59)"
            )

    @pytest.mark.asyncio
    async def test_continuous_sync_still_starts(self, config, db, demo_dirs):
        """`_start_continuous` is the last thing `_initial_sync` does. Skipping it left
        the pair with no watcher and no poller -- syncing had silently stopped."""
        local, remote = demo_dirs
        (local / "file.txt").write_text("data")

        async def exploding(method, params):
            raise RuntimeError("consumer blew up")

        gen = _run_engine_with_callback(config, db, MockDriveClient(remote), exploding)
        async for engine in gen:
            ps = engine.pairs["pair_0"]
            assert ps.watcher is not None
            # _start_continuous appends the local-change and remote-poll loop tasks.
            live = [t for t in engine._tasks if not t.done()]
            assert len(live) >= 2, (
                f"expected the two continuous-sync loops to be running, saw {len(live)} "
                "live tasks -- _start_continuous was skipped (#59)"
            )

    @pytest.mark.asyncio
    async def test_the_pass_is_not_reported_as_failed(self, config, db, demo_dirs):
        """A consumer's problem must not be reported to the user as a sync failure."""
        local, remote = demo_dirs
        (local / "file.txt").write_text("data")

        async def exploding(method, params):
            raise RuntimeError("consumer blew up")

        gen = _run_engine_with_callback(config, db, MockDriveClient(remote), exploding)
        async for _engine in gen:
            rows = await db.get_recent_log(limit=50)
            failures = [
                r for r in rows
                if r.action == "sync" and r.status == "error" and "consumer blew up" in r.detail
            ]
            assert not failures, (
                f"the consumer exception was reported as a sync failure: "
                f"{[r.detail for r in failures]}"
            )

    @pytest.mark.asyncio
    async def test_a_healthy_consumer_still_receives_the_events(self, config, db, demo_dirs):
        """The guard must suppress exceptions, not the notifications themselves."""
        local, remote = demo_dirs
        (local / "file.txt").write_text("data")

        seen: list[str] = []

        async def recording(method, params):
            seen.append(method)

        gen = _run_engine_with_callback(config, db, MockDriveClient(remote), recording)
        async for _engine in gen:
            assert "sync_complete" in seen, f"sync_complete was not delivered; saw {seen}"
            assert "status_changed" in seen, f"status_changed was not delivered; saw {seen}"

    @pytest.mark.asyncio
    async def test_a_consumer_raising_on_one_event_still_gets_the_next(
        self, config, db, demo_dirs
    ):
        """Isolation is per delivery, not per pass.

        Worth pinning because the weaker version is a plausible implementation and is
        noticeably worse. If the guard were a single ``suppress`` around both awaits,
        a consumer that failed on ``sync_complete`` would also silently lose
        ``status_changed`` -- so one bad event would leave the UI showing a pair as
        still syncing forever. ``EventBus.emit`` wraps each subscriber call, so the
        next event is delivered normally.
        """
        local, remote = demo_dirs
        (local / "file.txt").write_text("data")

        seen: list[str] = []

        async def explode_on_first(method, params):
            seen.append(method)
            if method == "sync_complete":
                raise RuntimeError("boom")

        gen = _run_engine_with_callback(config, db, MockDriveClient(remote), explode_on_first)
        async for _engine in gen:
            assert "sync_complete" in seen
            assert "status_changed" in seen, (
                "a raise on one event must not suppress the next one; "
                f"saw {seen}"
            )
            # And the sync itself must still have completed.
            assert await db.get_change_token("pair_0") is not None
