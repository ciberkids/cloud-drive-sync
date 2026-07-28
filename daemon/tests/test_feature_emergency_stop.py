"""Tests for the emergency stop (#54).

The requirement is that activity stops *immediately*, not that new work stops
being queued — `pause_sync` already did the latter. So these tests care about
in-flight cancellation, about the stop surviving a restart, and about a stopped
pair never being handed work.

Known limit, asserted rather than papered over: a provider call already inside
`asyncio.to_thread` cannot be cancelled, because threads have no cancellation
mechanism. At most one transfer per worker keeps writing until it returns, and its
result is discarded. Everything queued behind it stops at once.
"""

from __future__ import annotations

import asyncio

import pytest

from cloud_drive_sync.config import Account, Config, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.sync.engine import PairStatus, SyncEngine
from cloud_drive_sync.sync.executor import SyncExecutor
from cloud_drive_sync.sync.planner import ActionType, SyncAction


class SpyExecutor:
    """Minimal executor double that records work and honours stop()."""

    def __init__(self) -> None:
        self.batches: list[list[SyncAction]] = []
        self._stopped = False
        self.cancelled = 0

    async def execute_all(self, actions):
        if self._stopped:
            return []
        self.batches.append(list(actions))
        return []

    def stop(self) -> int:
        self._stopped = True
        self.cancelled += 1
        return 1

    def resume(self) -> None:
        self._stopped = False

    @property
    def stopped(self) -> bool:
        return self._stopped


def _uploads(n: int) -> list[SyncAction]:
    return [SyncAction(action=ActionType.UPLOAD, path=f"f{i}.txt") for i in range(n)]


@pytest.fixture
async def engine(tmp_path):
    db = Database(tmp_path / "state.db")
    await db.open()

    cfg = Config()
    cfg.save = lambda *a, **k: None  # don't touch the real config file
    cfg.accounts = [Account(email="a@x.com"), Account(email="b@x.com")]
    pair_a = SyncPair(local_path=str(tmp_path / "a"), account_id="a@x.com")
    pair_b = SyncPair(local_path=str(tmp_path / "b"), account_id="b@x.com")
    cfg.sync.pairs = [pair_a, pair_b]

    eng = SyncEngine(cfg, db)
    eng._pairs["pair_0"] = PairStatus(pair=pair_a, pair_id="pair_0", executor=SpyExecutor())
    eng._pairs["pair_1"] = PairStatus(pair=pair_b, pair_id="pair_1", executor=SpyExecutor())
    yield eng, cfg
    await db.close()


# ── Scope ───────────────────────────────────────────────────────────


async def test_global_stop_halts_every_pair(engine):
    eng, cfg = engine

    result = await eng.emergency_stop()

    assert result["pairs_stopped"] == 2
    assert cfg.sync.stopped is True
    assert all(ps.paused for ps in eng._pairs.values())
    assert all(ps.executor.stopped for ps in eng._pairs.values())


async def test_account_stop_leaves_other_accounts_running(engine):
    eng, cfg = engine

    result = await eng.emergency_stop("a@x.com")

    assert result["pairs_stopped"] == 1
    assert cfg.sync.stopped is False, "an account stop must not become a global stop"
    assert eng._pairs["pair_0"].executor.stopped is True
    assert eng._pairs["pair_1"].executor.stopped is False
    assert eng._pairs["pair_1"].paused is False


async def test_a_stopped_pair_is_never_handed_work(engine):
    eng, _ = engine
    ps = eng._pairs["pair_0"]

    await eng.emergency_stop()

    assert await eng._may_execute(ps, _uploads(50)) is False
    assert ps.executor.batches == []


async def test_a_running_pair_still_works_during_an_account_stop(engine):
    eng, _ = engine
    await eng.emergency_stop("a@x.com")

    assert await eng._may_execute(eng._pairs["pair_1"], _uploads(5)) is True


# ── Resume ──────────────────────────────────────────────────────────


async def test_resume_restores_activity(engine):
    eng, cfg = engine
    await eng.emergency_stop()

    result = await eng.emergency_resume()

    assert result["pairs_resumed"] == 2
    assert cfg.sync.stopped is False
    assert not any(ps.paused for ps in eng._pairs.values())
    assert await eng._may_execute(eng._pairs["pair_0"], _uploads(5)) is True


async def test_account_resume_cannot_override_a_global_stop(engine):
    """Otherwise the button would appear to work while nothing moved."""
    eng, _ = engine
    await eng.emergency_stop()  # global

    result = await eng.emergency_resume("a@x.com")

    assert result["pairs_resumed"] == 0
    assert eng._pairs["pair_0"].paused is True
    assert await eng._may_execute(eng._pairs["pair_0"], _uploads(5)) is False


async def test_global_resume_clears_account_stops_too(engine):
    eng, cfg = engine
    await eng.emergency_stop("a@x.com")
    await eng.emergency_stop()

    await eng.emergency_resume()

    assert cfg.sync.stopped is False
    assert all(not a.stopped for a in cfg.accounts)
    assert await eng._may_execute(eng._pairs["pair_0"], _uploads(5)) is True


# ── Persistence ─────────────────────────────────────────────────────


async def test_stop_is_persisted_to_config(engine):
    eng, cfg = engine

    await eng.emergency_stop("a@x.com")

    assert next(a for a in cfg.accounts if a.email == "a@x.com").stopped is True


async def test_a_persisted_stop_is_re_applied_at_startup(engine):
    """The flag surviving a restart is useless if the daemon syncs anyway."""
    eng, cfg = engine
    cfg.sync.stopped = True
    for ps in eng._pairs.values():
        ps.paused = False
        ps.executor.resume()

    await eng.restore_stop_state()

    assert all(ps.paused for ps in eng._pairs.values())
    assert all(ps.executor.stopped for ps in eng._pairs.values())


async def test_stop_state_reports_both_scopes(engine):
    eng, _ = engine
    await eng.emergency_stop("b@x.com")

    state = eng.stop_state()

    assert state["stopped"] is False
    assert state["accounts"]["b@x.com"] is True
    assert state["accounts"]["a@x.com"] is False


# ── Real executor cancellation ──────────────────────────────────────


async def _real_executor(tmp_path, db) -> SyncExecutor:
    return SyncExecutor(
        ops=None,
        db=db,
        local_root=tmp_path,
        pair_id="pair_0",
        remote_folder_id="root",
    )


async def test_real_executor_cancels_in_flight_awaitables(tmp_path):
    """The property the whole feature rests on: awaited work is cancelled.

    A transfer already blocked inside asyncio.to_thread cannot be reached, but
    anything awaiting — which is every queued action — stops at once.
    """
    db = Database(tmp_path / "state.db")
    await db.open()
    try:
        ex = await _real_executor(tmp_path, db)
        started = asyncio.Event()

        async def _never_finishes(action):
            started.set()
            await asyncio.sleep(3600)

        ex._execute_one = _never_finishes

        task = asyncio.create_task(ex.execute_all(_uploads(20)))
        await asyncio.wait_for(started.wait(), timeout=5)

        cancelled = ex.stop()

        assert cancelled == 20, f"only {cancelled} of 20 in-flight tasks were cancelled"
        result = await asyncio.wait_for(task, timeout=5)
        # Cancelled actions are reported as not-completed, not as errors.
        assert len(result) == 20
    finally:
        await db.close()


async def test_real_executor_refuses_new_work_while_stopped(tmp_path):
    db = Database(tmp_path / "state.db")
    await db.open()
    try:
        ex = await _real_executor(tmp_path, db)
        ex.stop()

        failed = await ex.execute_all(_uploads(10))

        # Empty rather than "all failed": one deliberate stop must not fill the
        # activity log and ps.errors with hundreds of false errors.
        assert failed == []
    finally:
        await db.close()


async def test_real_executor_resumes(tmp_path):
    db = Database(tmp_path / "state.db")
    await db.open()
    try:
        ex = await _real_executor(tmp_path, db)
        ex.stop()
        ex.resume()

        assert ex.stopped is False
    finally:
        await db.close()
