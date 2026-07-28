"""Tests for the delete fail-safe (#53).

The threat: sync is symmetric, so a wiped local filesystem propagates and empties
the cloud copy, turning the backup into a mirror of the disaster. These tests pin
the decision logic — that it refuses implausible batches, that it does *not* fire
on routine work, and that it fails closed.
"""

from __future__ import annotations

import pytest

from cloud_drive_sync.sync import failsafe
from cloud_drive_sync.sync.failsafe import Direction, check, effective_limits
from cloud_drive_sync.sync.planner import ActionType, SyncAction


def _deletes(n: int, action: ActionType, prefix: str = "f") -> list[SyncAction]:
    return [SyncAction(action=action, path=f"{prefix}{i}.txt") for i in range(n)]


def _uploads(n: int) -> list[SyncAction]:
    return [SyncAction(action=ActionType.UPLOAD, path=f"up{i}.txt") for i in range(n)]


# ── Routine work must pass ──────────────────────────────────────────


def test_a_batch_with_no_deletions_is_allowed():
    assert not check(_uploads(5000), max_deletions=100).blocked


def test_ordinary_cleanup_is_allowed():
    """Emptying a folder is normal; the guard must not cry wolf."""
    verdict = check(_deletes(12, ActionType.DELETE_REMOTE), max_deletions=100, tracked_files=8000)

    assert not verdict.blocked


def test_a_batch_exactly_at_the_limit_is_allowed():
    """The limit is a maximum, not a threshold to cross."""
    assert not check(_deletes(100, ActionType.DELETE_REMOTE), max_deletions=100).blocked


def test_small_libraries_are_not_gated_by_ratio_alone():
    """Deleting 3 of 4 tracked files is 75% and entirely normal."""
    verdict = check(_deletes(3, ActionType.DELETE_REMOTE), max_deletions=100, tracked_files=4)

    assert not verdict.blocked


# ── The disaster cases must be refused ──────────────────────────────


def test_mass_deletion_over_the_count_is_refused():
    verdict = check(_deletes(5000, ActionType.DELETE_REMOTE), max_deletions=100)

    assert verdict.blocked
    assert len(verdict.breaches) == 1
    breach = verdict.breaches[0]
    assert breach.direction is Direction.REMOTE
    assert breach.count == 5000
    assert breach.limit == 100


def test_wiping_a_small_library_is_refused_by_ratio_even_under_the_count():
    """The 600-file library case: 90 deletions is under a 100 limit but is
    almost everything the user has."""
    verdict = check(
        _deletes(90, ActionType.DELETE_REMOTE),
        max_deletions=100,
        tracked_files=100,
    )

    assert verdict.blocked
    assert verdict.breaches[0].ratio == pytest.approx(0.9)


def test_local_and_remote_are_counted_independently():
    """A wiped remote emptying the local copy is the same threat mirrored."""
    actions = _deletes(200, ActionType.DELETE_REMOTE, "r") + _deletes(5, ActionType.DELETE_LOCAL, "l")

    verdict = check(actions, max_deletions=100)

    assert verdict.blocked
    assert [b.direction for b in verdict.breaches] == [Direction.REMOTE]
    assert verdict.breaches[0].count == 200, "remote count must not include the 5 local"


def test_both_directions_can_breach_at_once():
    actions = _deletes(300, ActionType.DELETE_REMOTE, "r") + _deletes(400, ActionType.DELETE_LOCAL, "l")

    verdict = check(actions, max_deletions=100)

    assert {b.direction for b in verdict.breaches} == {Direction.LOCAL, Direction.REMOTE}
    counts = {b.direction: b.count for b in verdict.breaches}
    assert counts[Direction.REMOTE] == 300
    assert counts[Direction.LOCAL] == 400


def test_deletions_are_counted_regardless_of_other_actions():
    """A real disaster produces deletions mixed with uploads and noops."""
    actions = _uploads(50) + _deletes(500, ActionType.DELETE_REMOTE)
    actions += [SyncAction(action=ActionType.NOOP, path="x")]

    assert check(actions, max_deletions=100).blocked


# ── The confirmation prompt needs to be informative ─────────────────


def test_breach_carries_a_sample_of_paths():
    """A user cannot decide from a bare number."""
    verdict = check(_deletes(500, ActionType.DELETE_REMOTE), max_deletions=100)

    sample = verdict.breaches[0].sample
    assert sample, "no sample paths — the prompt would be undecidable"
    assert len(sample) <= failsafe.SAMPLE_SIZE
    assert all(p.endswith(".txt") for p in sample)


def test_description_states_count_limit_and_share():
    verdict = check(_deletes(900, ActionType.DELETE_REMOTE), max_deletions=100, tracked_files=1000)

    text = verdict.describe()
    assert "900" in text
    assert "remote" in text
    assert "90%" in text
    assert "1000 tracked" in text


def test_description_omits_the_share_when_tracked_count_is_unknown():
    verdict = check(_deletes(900, ActionType.DELETE_LOCAL), max_deletions=100, tracked_files=0)

    assert "%" not in verdict.describe()


# ── Limit resolution ────────────────────────────────────────────────


def test_pair_limit_overrides_the_global_default():
    assert effective_limits(global_max=100, pair_max=5) == 5


def test_none_inherits_the_global_default():
    assert effective_limits(global_max=250, pair_max=None) == 250


def test_zero_is_a_deliberate_opt_out():
    assert effective_limits(global_max=100, pair_max=0) == 0
    assert not check(_deletes(100_000, ActionType.DELETE_REMOTE), max_deletions=0).blocked


def test_a_negative_limit_falls_back_to_the_default_rather_than_disabling():
    """A negative limit is a mistake, not an opt-out — it must not silently
    disable the guard."""
    assert effective_limits(global_max=-1, pair_max=None) == failsafe.DEFAULT_MAX_DELETIONS


# ── Batch splitting ─────────────────────────────────────────────────


def test_without_deletions_keeps_everything_else():
    actions = _uploads(3) + _deletes(4, ActionType.DELETE_REMOTE) + _deletes(2, ActionType.DELETE_LOCAL)

    kept = failsafe.without_deletions(actions)

    assert len(kept) == 3
    assert all(a.action is ActionType.UPLOAD for a in kept)


def test_only_deletions_keeps_both_directions():
    actions = _uploads(3) + _deletes(4, ActionType.DELETE_REMOTE) + _deletes(2, ActionType.DELETE_LOCAL)

    dels = failsafe.only_deletions(actions)

    assert len(dels) == 6
    assert all(a.action in failsafe.DELETE_ACTIONS for a in dels)


# ── Time window: the slow-drip hole ─────────────────────────────────


def test_a_drip_under_the_per_pass_limit_is_caught_by_the_window():
    """The hole a per-pass cap alone leaves.

    99 deletions in one pass never trips a limit of 100. Repeated, it empties the
    library. Counting what was already deleted inside the window closes it.
    """
    batch = _deletes(99, ActionType.DELETE_REMOTE)

    # First pass: nothing recent, so 99 is allowed.
    assert not check(batch, max_deletions=100, recent_deletions={"remote": 0}).blocked

    # Second pass: 99 already gone in the window, so 99 more is refused.
    verdict = check(batch, max_deletions=100, recent_deletions={"remote": 99})

    assert verdict.blocked
    breach = verdict.breaches[0]
    assert breach.count == 99
    assert breach.recent == 99
    assert breach.total_in_window == 198


def test_the_window_counts_directions_separately():
    """Remote deletions must not consume the local allowance."""
    verdict = check(
        _deletes(50, ActionType.DELETE_LOCAL),
        max_deletions=100,
        recent_deletions={"remote": 500, "local": 0},
    )

    assert not verdict.blocked, "remote history must not block local deletions"


def test_recent_deletions_alone_do_not_block_an_empty_batch():
    """No deletions proposed means nothing to refuse, whatever the history."""
    assert not check(_uploads(10), max_deletions=100, recent_deletions={"remote": 9999}).blocked


def test_omitting_recent_deletions_keeps_the_per_pass_behaviour():
    """Callers that cannot supply history still get the batch check."""
    assert not check(_deletes(50, ActionType.DELETE_REMOTE), max_deletions=100).blocked
    assert check(_deletes(150, ActionType.DELETE_REMOTE), max_deletions=100).blocked


def test_the_description_mentions_the_window_history():
    verdict = check(
        _deletes(5, ActionType.DELETE_REMOTE),
        max_deletions=10,
        recent_deletions={"remote": 8},
        window_seconds=30,
    )

    text = verdict.describe()
    assert "8 already deleted" in text
    assert "30s" in text
    assert "13 total" in text


# ── A very low limit, as a user might set ───────────────────────────


def test_a_limit_of_two_blocks_a_third_deletion():
    """The tightest sensible setting: no more than 2 deletions in the window."""
    assert not check(_deletes(2, ActionType.DELETE_REMOTE), max_deletions=2).blocked
    assert check(_deletes(3, ActionType.DELETE_REMOTE), max_deletions=2).blocked


def test_a_limit_of_two_blocks_a_mass_delete_on_the_first_pass():
    """A wiped directory is refused before anything is deleted."""
    verdict = check(_deletes(5000, ActionType.DELETE_REMOTE), max_deletions=2)

    assert verdict.blocked
    assert verdict.breaches[0].count == 5000


def test_a_limit_of_two_accumulates_across_the_window():
    """Two now and two more a moment later is four in the window — refused."""
    assert not check(
        _deletes(2, ActionType.DELETE_REMOTE), max_deletions=2, recent_deletions={"remote": 0}
    ).blocked
    assert check(
        _deletes(2, ActionType.DELETE_REMOTE), max_deletions=2, recent_deletions={"remote": 2}
    ).blocked


def test_a_limit_of_one_is_usable():
    assert not check(_deletes(1, ActionType.DELETE_REMOTE), max_deletions=1).blocked
    assert check(_deletes(2, ActionType.DELETE_REMOTE), max_deletions=1).blocked
