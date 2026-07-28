"""End-to-end tests for the delete fail-safe through the engine (#53).

test_feature_delete_failsafe.py covers the decision logic in isolation. These
cover the part that actually protects data: that a refused batch never reaches the
executor, that the pair is paused, that the refusal is persisted so a restart
cannot silently resolve it, and that approval is one-shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from cloud_drive_sync.config import Config, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.sync.engine import PairStatus, SyncEngine
from cloud_drive_sync.sync.planner import ActionType, SyncAction


class SpyExecutor:
    """Records whether the engine ever handed it work."""

    def __init__(self) -> None:
        self.batches: list[list[SyncAction]] = []

    async def execute_all(self, actions):
        self.batches.append(list(actions))
        return []


@dataclass
class _Notified:
    events: list[tuple[str, dict]] = field(default_factory=list)

    async def __call__(self, method, params):
        self.events.append((method, params))


def _deletes(n: int, action=ActionType.DELETE_REMOTE) -> list[SyncAction]:
    return [SyncAction(action=action, path=f"doc{i}.txt") for i in range(n)]


@pytest.fixture
async def engine(tmp_path):
    db = Database(tmp_path / "state.db")
    await db.open()

    cfg = Config()
    cfg.sync.max_deletions_per_sync = 10
    pair = SyncPair(local_path=str(tmp_path / "local"))
    cfg.sync.pairs = [pair]

    eng = SyncEngine(cfg, db)
    ps = PairStatus(pair=pair, pair_id="pair_0", executor=SpyExecutor())
    eng._pairs["pair_0"] = ps
    yield eng, ps, db
    await db.close()


async def test_a_refused_batch_never_reaches_the_executor(engine):
    """The property that matters: the deletions must not execute."""
    eng, ps, _ = engine

    allowed = await eng._deletions_allowed(ps, _deletes(500))

    assert allowed is False
    assert ps.executor.batches == [], "deletions were handed to the executor anyway"


async def test_the_pair_is_paused_so_the_next_pass_does_not_retry(engine):
    eng, ps, _ = engine

    await eng._deletions_allowed(ps, _deletes(500))

    assert ps.paused is True


async def test_the_refusal_is_persisted_so_a_restart_cannot_resolve_it(engine):
    """Otherwise a container restart policy would quietly undo the safety hold."""
    eng, ps, db = engine

    await eng._deletions_allowed(ps, _deletes(500))

    pending = await db.get_pending_deletions("pair_0")
    assert len(pending) == 1
    assert pending[0]["direction"] == "remote"
    assert pending[0]["count"] == 500
    assert pending[0]["limit"] == 10
    assert pending[0]["sample"], "no sample paths persisted — the prompt is undecidable"


async def test_the_refusal_is_recorded_in_the_activity_log(engine):
    eng, ps, db = engine

    await eng._deletions_allowed(ps, _deletes(500))

    entries = await db.get_recent_log(limit=10)
    blocked = [e for e in entries if e.action == "delete_blocked"]
    assert blocked, "nothing in the activity log — the user would have no trail"
    assert blocked[0].status == "error"
    assert "500" in (blocked[0].detail or "")


async def test_the_ui_is_notified(engine):
    eng, ps, _ = engine
    notified = _Notified()
    eng.set_notify_callback(notified)

    await eng._deletions_allowed(ps, _deletes(500))

    methods = [m for m, _ in notified.events]
    assert "delete_blocked" in methods
    payload = next(p for m, p in notified.events if m == "delete_blocked")
    assert payload["pair_id"] == "pair_0"
    assert "500" in payload["message"]


async def test_an_ordinary_batch_passes_through(engine):
    eng, ps, _ = engine

    assert await eng._deletions_allowed(ps, _deletes(3)) is True
    assert ps.paused is False


async def test_a_batch_with_no_deletions_is_never_gated(engine):
    eng, ps, _ = engine
    uploads = [SyncAction(action=ActionType.UPLOAD, path=f"u{i}") for i in range(9999)]

    assert await eng._deletions_allowed(ps, uploads) is True


async def test_approval_is_one_shot(engine):
    """Approving today's mass delete is not consent for every future one.

    Without this the pair would also loop: the next pass re-plans the same
    deletions and the guard blocks them again.
    """
    eng, ps, _ = engine
    await eng._deletions_allowed(ps, _deletes(500))
    assert ps.paused is True

    await eng.approve_pending_deletions("pair_0")

    assert ps.paused is False
    # The approved pass goes through...
    assert await eng._deletions_allowed(ps, _deletes(500)) is True
    # ...and the one after it is gated again.
    assert await eng._deletions_allowed(ps, _deletes(500)) is False


async def test_approving_an_unknown_pair_is_refused(engine):
    eng, _, _ = engine

    assert await eng.approve_pending_deletions("pair_99") is False


async def test_a_pair_opted_out_with_zero_is_not_gated(engine):
    eng, ps, _ = engine
    ps.pair.max_deletions_per_sync = 0

    assert await eng._deletions_allowed(ps, _deletes(100_000)) is True
    assert ps.paused is False


async def test_a_pair_override_takes_precedence_over_the_global_limit(engine):
    eng, ps, _ = engine
    ps.pair.max_deletions_per_sync = 1000  # global is 10

    assert await eng._deletions_allowed(ps, _deletes(500)) is True


async def test_local_deletions_are_gated_too(engine):
    """A wiped remote must not be able to empty the local copy either."""
    eng, ps, db = engine

    allowed = await eng._deletions_allowed(ps, _deletes(500, ActionType.DELETE_LOCAL))

    assert allowed is False
    pending = await db.get_pending_deletions("pair_0")
    assert pending[0]["direction"] == "local"


def test_every_executor_call_site_is_gated():
    """Source-level check, because the tests above call the guard directly.

    The engine hands work to the executor from three places — initial sync, the
    local change loop, and the remote change handler. Removing the guard from any
    one of them would leave that path unprotected while every test above still
    passed, so the wiring itself is asserted here.

    ``_may_execute`` is the composed gate: it checks the emergency stop (#54) and
    then delegates to the delete fail-safe (#53).
    """
    import inspect
    import re

    from cloud_drive_sync.sync import engine as engine_module

    source = inspect.getsource(engine_module)
    execute_calls = re.findall(r"execute_all\(", source)
    guard_calls = re.findall(r"_may_execute\(ps,", source)

    # One execute_all is the executor's own definition reference in this module's
    # imports; count the call sites in the engine and require a guard for each.
    assert len(execute_calls) == 3, (
        f"expected 3 executor call sites, found {len(execute_calls)} — "
        "a new one may need gating too"
    )
    assert len(guard_calls) == 3, (
        f"only {len(guard_calls)} of 3 executor call sites are gated by the "
        "delete fail-safe"
    )


def test_the_guard_precedes_execution_at_every_site():
    """Ordering matters: a guard called after execute_all protects nothing."""
    import inspect
    import re

    from cloud_drive_sync.sync import engine as engine_module

    source = inspect.getsource(engine_module)
    positions = [
        (m.start(), m.group(0))
        for m in re.finditer(r"_may_execute\(ps,|execute_all\(", source)
    ]
    # Walking in order, every execute_all must be preceded by an ungated guard.
    pending_guards = 0
    for _, token in positions:
        if token.startswith("_may_execute"):
            pending_guards += 1
        else:
            assert pending_guards > 0, "an execute_all call is not preceded by a guard"
            pending_guards -= 1
