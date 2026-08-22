"""Tests for the event bus and the continuous-loop pass reporting (#60).

The bus exists so more than one thing can observe daemon events. Its load-bearing
property is not fan-out but **isolation**: a consumer must not be able to affect the
thing it observes. See ``test_bug_notify_consumer_exception`` for why that matters at
the ``sync_complete`` site specifically (#59).

The second half covers #60. ``sync_complete`` used to be emitted only from
``_initial_sync``, so a consumer saw one event per pair per daemon start and nothing
afterwards, and both continuous loops swallowed their failures with a bare
``log.exception`` -- no activity-log row, nothing in the UI. A pair failing every
single cycle was invisible.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cloud_drive_sync.config import Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.drive.mock_client import MockChangePoller, MockDriveClient, MockFileOperations
from cloud_drive_sync.events import EventBus
from cloud_drive_sync.sync.engine import SyncEngine, summarise_actions
from cloud_drive_sync.sync.planner import ActionType, SyncAction


class TestEventBus:
    @pytest.mark.asyncio
    async def test_delivers_to_every_subscriber(self):
        bus = EventBus()
        a: list[tuple] = []
        b: list[tuple] = []

        async def first(event, params):
            a.append((event, params))

        async def second(event, params):
            b.append((event, params))

        bus.subscribe(first)
        bus.subscribe(second)
        await bus.emit("sync_complete", {"pair_id": "pair_0"})

        assert a == [("sync_complete", {"pair_id": "pair_0"})]
        assert b == a

    @pytest.mark.asyncio
    async def test_emit_with_no_subscribers_is_a_noop(self):
        await EventBus().emit("sync_complete", {})  # must not raise

    @pytest.mark.asyncio
    async def test_a_raising_subscriber_does_not_reach_the_emitter(self):
        bus = EventBus()

        async def exploding(event, params):
            raise RuntimeError("boom")

        bus.subscribe(exploding)
        await bus.emit("sync_complete", {})  # must not raise

    @pytest.mark.asyncio
    async def test_a_raising_subscriber_does_not_starve_the_others(self):
        """Order must not decide who gets the event."""
        bus = EventBus()
        delivered: list[str] = []

        async def exploding(event, params):
            raise RuntimeError("boom")

        async def healthy(event, params):
            delivered.append(event)

        bus.subscribe(exploding)   # registered first, on purpose
        bus.subscribe(healthy)
        await bus.emit("sync_complete", {})

        assert delivered == ["sync_complete"]

    @pytest.mark.asyncio
    async def test_every_subscriber_raising_is_still_survivable(self):
        bus = EventBus()

        async def exploding(event, params):
            raise RuntimeError("boom")

        async def also_exploding(event, params):
            raise ValueError("bang")

        bus.subscribe(exploding)
        bus.subscribe(also_exploding)
        await bus.emit("sync_complete", {})  # must not raise

    def test_subscribing_twice_registers_once(self):
        """`daemon.py` wires the IPC server in three places; the same callable
        arriving twice must not double-deliver."""
        bus = EventBus()

        async def consumer(event, params):
            pass

        bus.subscribe(consumer)
        bus.subscribe(consumer)
        assert bus.subscriber_count == 1

    def test_unsubscribe_removes_the_consumer(self):
        bus = EventBus()

        async def consumer(event, params):
            pass

        bus.subscribe(consumer)
        bus.unsubscribe(consumer)
        assert bus.subscriber_count == 0

    def test_unsubscribing_something_never_registered_is_harmless(self):
        async def consumer(event, params):
            pass

        EventBus().unsubscribe(consumer)  # must not raise

    @pytest.mark.asyncio
    async def test_a_subscriber_may_unsubscribe_itself_while_being_called(self):
        """`emit` iterates a copy, so mutating the list mid-delivery is safe."""
        bus = EventBus()
        calls: list[str] = []

        async def once(event, params):
            calls.append(event)
            bus.unsubscribe(once)

        bus.subscribe(once)
        await bus.emit("first", {})
        await bus.emit("second", {})

        assert calls == ["first"]


class TestEngineUsesTheBus:
    @pytest.mark.asyncio
    async def test_set_notify_callback_registers_rather_than_replaces(self, tmp_path: Path):
        """The old single slot meant a second consumer silently displaced the first --
        which is how adding any observer broke the live UI."""
        cfg = Config()
        db = Database(tmp_path / "bus.db")
        await db.open()
        try:
            engine = SyncEngine(cfg, db)

            async def first(event, params):
                pass

            async def second(event, params):
                pass

            engine.set_notify_callback(first)
            engine.set_notify_callback(second)
            assert engine.bus.subscriber_count == 2
        finally:
            await db.close()


class TestSummariseActions:
    def test_counts_by_action_type(self):
        actions = [
            SyncAction(action=ActionType.UPLOAD, path="a"),
            SyncAction(action=ActionType.UPLOAD, path="b"),
            SyncAction(action=ActionType.DOWNLOAD, path="c"),
            SyncAction(action=ActionType.MKDIR, path="d"),
            SyncAction(action=ActionType.DELETE_LOCAL, path="e"),
            SyncAction(action=ActionType.DELETE_REMOTE, path="f"),
        ]
        summary = summarise_actions(actions, [])
        assert summary["uploaded"] == 2
        assert summary["downloaded"] == 1
        assert summary["mkdirs"] == 1
        assert summary["deleted"] == 2
        assert summary["errors"] == 0

    def test_failed_actions_are_excluded_from_the_counts(self):
        """A failed upload did not happen, and must not be reported as one."""
        ok = SyncAction(action=ActionType.UPLOAD, path="ok")
        bad = SyncAction(action=ActionType.UPLOAD, path="bad")
        summary = summarise_actions([ok, bad], [bad])
        assert summary["uploaded"] == 1
        assert summary["errors"] == 1
        assert summary["files"]["uploaded"] == ["ok"]

    def test_samples_are_capped(self):
        actions = [SyncAction(action=ActionType.UPLOAD, path=f"f{i}") for i in range(500)]
        summary = summarise_actions(actions, [])
        assert summary["uploaded"] == 500, "the count must be the true total"
        assert len(summary["files"]["uploaded"]) == 200, "the sample must be capped"

    def test_conflicts_come_from_the_conflict_source(self):
        """Conflicts are counted from the pre-resolution list, because resolution
        rewrites them into uploads and downloads."""
        planned = [SyncAction(action=ActionType.CONFLICT, path="clash")]
        resolved = [SyncAction(action=ActionType.UPLOAD, path="clash")]
        summary = summarise_actions(resolved, [], conflict_source=planned)
        assert summary["files"]["conflicted"] == ["clash"]
        assert summary["uploaded"] == 1


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
        poll_interval=1,  # short, so the remote loop actually runs during the test
        conflict_strategy="keep_both",
        max_concurrent_transfers=2,
        debounce_delay=0.1,
        pairs=[SyncPair(local_path=str(local), remote_folder_id="root", enabled=True)],
    )
    return cfg


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "loops.db")
    await database.open()
    yield database
    await database.close()


class TestContinuousLoopsReportTheirPasses:
    @pytest.mark.asyncio
    async def test_a_local_change_after_startup_emits_sync_complete(
        self, config, db, demo_dirs
    ):
        """The #60 regression test. Before the fix, `sync_complete` arrived once during
        the initial sync and never again, however many files changed afterwards."""
        local, remote = demo_dirs
        events: list[tuple[str, dict]] = []

        async def record(event, params):
            events.append((event, params))

        ops = MockFileOperations(MockDriveClient(remote))
        engine = SyncEngine(
            config, db, MockDriveClient(remote),
            file_ops=ops, change_poller=MockChangePoller(MockDriveClient(remote)),
        )
        engine.set_notify_callback(record)
        try:
            await engine.start()
            await asyncio.sleep(0.6)
            events.clear()  # discard whatever the initial sync produced

            (local / "added-after-startup.txt").write_text("new content")
            await asyncio.sleep(2.0)

            completions = [p for e, p in events if e == "sync_complete"]
            assert completions, (
                "no sync_complete after a post-startup local change -- the continuous "
                f"loop is not reporting its passes (#60); saw {[e for e, _ in events]}"
            )
            assert completions[0]["pair_id"] == "pair_0"
            assert "duration_seconds" in completions[0]
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_a_failing_local_pass_is_recorded_and_announced(
        self, config, db, demo_dirs
    ):
        """Both loops used to swallow failures entirely: no log row, no notification,
        so a pair failing every cycle showed nothing anywhere the user looks."""
        local, remote = demo_dirs
        events: list[tuple[str, dict]] = []

        async def record(event, params):
            events.append((event, params))

        ops = MockFileOperations(MockDriveClient(remote))
        engine = SyncEngine(
            config, db, MockDriveClient(remote),
            file_ops=ops, change_poller=MockChangePoller(MockDriveClient(remote)),
        )
        engine.set_notify_callback(record)
        try:
            await engine.start()
            await asyncio.sleep(0.6)
            events.clear()

            # Break the executor the loop is about to use.
            ps = engine.pairs["pair_0"]

            async def explode(actions):
                raise RuntimeError("executor exploded")

            ps.executor.execute_all = explode

            (local / "will-fail.txt").write_text("data")
            await asyncio.sleep(2.0)

            failures = [p for e, p in events if e == "sync_failed"]
            assert failures, (
                f"no sync_failed emitted; saw {[e for e, _ in events]}"
            )
            assert failures[0]["pair_id"] == "pair_0"
            assert "executor exploded" in failures[0]["error"]

            rows = await db.get_recent_log(limit=50)
            errors = [
                r for r in rows
                if r.status == "error" and "executor exploded" in r.detail
            ]
            assert errors, "the failure left no activity-log row -- invisible in the UI"
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_an_idle_pass_emits_nothing(self, config, db, demo_dirs):
        """A poll that finds nothing is not an event. Emitting one every interval would
        turn any consumer into a firehose of empty notifications."""
        _local, remote = demo_dirs
        events: list[str] = []

        async def record(event, params):
            events.append(event)

        engine = SyncEngine(
            config, db, MockDriveClient(remote),
            file_ops=MockFileOperations(MockDriveClient(remote)),
            change_poller=MockChangePoller(MockDriveClient(remote)),
        )
        engine.set_notify_callback(record)
        try:
            await engine.start()
            await asyncio.sleep(0.6)
            events.clear()

            await asyncio.sleep(2.5)  # several poll intervals, no changes
            assert "sync_complete" not in events, (
                f"an idle pass produced a completion event; saw {events}"
            )
        finally:
            await engine.stop()


class TestRepeatFailureSuppression:
    """A loop that keeps failing fails every poll interval.

    Reporting each one would fill the activity log and flood any webhook receiver with
    the same message forever. Surfaced by an end-to-end run: demo mode's poller raises
    on every cycle, and before suppression that was one event every 5 seconds.
    """

    @pytest.mark.asyncio
    async def test_an_unchanged_failure_is_reported_once_then_suppressed(
        self, config, db, demo_dirs
    ):
        _local, remote = demo_dirs
        events: list[str] = []

        async def record(event, params):
            events.append(event)

        engine = SyncEngine(
            config, db, MockDriveClient(remote),
            file_ops=MockFileOperations(MockDriveClient(remote)),
            change_poller=MockChangePoller(MockDriveClient(remote)),
        )
        engine.set_notify_callback(record)
        ps = engine.pairs.get("pair_0")
        if ps is None:
            await engine.start()
            await asyncio.sleep(0.5)
            ps = engine.pairs["pair_0"]
        try:
            events.clear()
            boom = RuntimeError("the same failure over and over")
            for _ in range(5):
                await engine._report_failure(ps, "Remote poll", boom)
            assert events.count("sync_failed") == 1, (
                f"an unchanged failure should report once; saw {events}"
            )
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_a_different_failure_is_never_hidden_behind_an_ongoing_one(
        self, config, db, demo_dirs
    ):
        _local, remote = demo_dirs
        events: list[tuple[str, dict]] = []

        async def record(event, params):
            events.append((event, params))

        engine = SyncEngine(
            config, db, MockDriveClient(remote),
            file_ops=MockFileOperations(MockDriveClient(remote)),
            change_poller=MockChangePoller(MockDriveClient(remote)),
        )
        engine.set_notify_callback(record)
        try:
            await engine.start()
            await asyncio.sleep(0.5)
            ps = engine.pairs["pair_0"]
            events.clear()
            await engine._report_failure(ps, "Remote poll", RuntimeError("first problem"))
            await engine._report_failure(ps, "Remote poll", RuntimeError("first problem"))
            await engine._report_failure(ps, "Remote poll", RuntimeError("a new problem"))
            errors = [p["error"] for e, p in events if e == "sync_failed"]
            assert errors == ["first problem", "a new problem"], (
                f"a new failure must not be suppressed by an ongoing one; saw {errors}"
            )
        finally:
            await engine.stop()

    @pytest.mark.asyncio
    async def test_the_repeat_count_is_reported_when_it_resurfaces(
        self, config, db, demo_dirs
    ):
        """Suppressed occurrences are counted, not forgotten -- otherwise a long
        outage looks like a single blip in the activity log."""
        from cloud_drive_sync.sync import engine as engine_mod

        _local, remote = demo_dirs
        engine = SyncEngine(
            config, db, MockDriveClient(remote),
            file_ops=MockFileOperations(MockDriveClient(remote)),
            change_poller=MockChangePoller(MockDriveClient(remote)),
        )
        try:
            await engine.start()
            await asyncio.sleep(0.5)
            ps = engine.pairs["pair_0"]
            boom = RuntimeError("persistent trouble")
            await engine._report_failure(ps, "Remote poll", boom)
            for _ in range(3):
                await engine._report_failure(ps, "Remote poll", boom)

            # Expire the window rather than sleeping through it.
            key = ("pair_0", "Remote poll failed: persistent trouble")
            engine._last_failure_report[key] -= (
                engine_mod.FAILURE_REPORT_INTERVAL_SECONDS + 1
            )
            await engine._report_failure(ps, "Remote poll", boom)

            rows = await db.get_recent_log(limit=50)
            details = [r.detail for r in rows if r.status == "error"]
            assert any("repeated 4 times" in d for d in details), (
                f"the suppressed count was not surfaced; saw {details}"
            )
        finally:
            await engine.stop()
