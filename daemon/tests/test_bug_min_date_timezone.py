"""Regression tests for the min_date sync rule and timezone-aware input.

``apply_sync_rules`` parsed ``min_date`` with ``datetime.fromisoformat``, which
returns a **timezone-aware** datetime whenever the string carries an offset, and
compared it against ``datetime.fromtimestamp(mtime)``, which is always naive.
Comparing the two raises ``TypeError: can't compare offset-naive and
offset-aware datetimes``, which propagated out and aborted the whole sync pass
for that pair.

``"2026-01-01T00:00:00Z"`` is the most natural way to write an ISO 8601 instant,
and ISO 8601 permits offsets everywhere, so this was reachable from ordinary
configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from cloud_drive_sync.local.scanner import LocalFileInfo
from cloud_drive_sync.sync.planner import ActionType, SyncAction, apply_sync_rules


@dataclass
class Rules:
    """Mirrors config.SyncRules, so the test does not depend on config loading."""

    max_file_size_mb: float = 0
    include_regex: list[str] = field(default_factory=list)
    exclude_regex: list[str] = field(default_factory=list)
    min_date: str = ""


def _upload(path: str, mtime: float | None) -> SyncAction:
    """An UPLOAD action, since NOOP/MKDIR/DELETE_* bypass filtering entirely."""
    info = LocalFileInfo(md5="d41d8", mtime=mtime, size=10) if mtime is not None else None
    return SyncAction(action=ActionType.UPLOAD, path=path, local_info=info)


def _at(dt: datetime) -> SyncAction:
    return _upload("notes.txt", dt.timestamp())


# Reference instants well clear of any DST boundary.
CUTOFF = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
OLDER = CUTOFF - timedelta(days=30)
NEWER = CUTOFF + timedelta(days=30)


@pytest.mark.parametrize(
    "min_date",
    [
        "2026-01-15T12:00:00+00:00",
        "2026-01-15T12:00:00Z",
        "2026-01-15T13:00:00+01:00",  # same instant, different offset
    ],
    ids=["explicit-utc-offset", "z-suffix", "non-utc-offset"],
)
def test_timezone_aware_min_date_does_not_raise(min_date):
    """The crash: an offset in min_date aborted the sync pass with TypeError."""
    actions = [_at(OLDER), _at(NEWER)]

    kept = apply_sync_rules(actions, Rules(min_date=min_date))

    # And it still filters correctly rather than merely not crashing.
    assert len(kept) == 1
    assert kept[0].local_info.mtime == NEWER.timestamp()


def test_naive_min_date_still_behaves_as_before():
    """Naive input was already working; the fix must not change its meaning.

    A naive min_date is read as local time, which is what comparing against a
    naive fromtimestamp(mtime) already did.
    """
    local_cutoff = datetime(2026, 1, 15, 12, 0, 0)  # noqa: DTZ001 — naive is the case under test
    older = _upload("a", (local_cutoff - timedelta(days=1)).timestamp())
    newer = _upload("b", (local_cutoff + timedelta(days=1)).timestamp())

    kept = apply_sync_rules([older, newer], Rules(min_date="2026-01-15T12:00:00"))

    assert [a.path for a in kept] == ["b"]


def test_date_only_min_date_is_accepted():
    older = _upload("a", datetime(2025, 6, 1, 12, 0).timestamp())  # noqa: DTZ001
    newer = _upload("b", datetime(2026, 6, 1, 12, 0).timestamp())  # noqa: DTZ001

    kept = apply_sync_rules([older, newer], Rules(min_date="2026-01-01"))

    assert [a.path for a in kept] == ["b"]


def test_invalid_min_date_is_ignored_not_fatal():
    """A typo in configuration must not drop every file or crash the pass."""
    actions = [_at(OLDER), _at(NEWER)]

    kept = apply_sync_rules(actions, Rules(min_date="not-a-date"))

    assert len(kept) == 2


def test_actions_without_local_info_are_kept():
    """Remote-only actions have no mtime; min_date must not silently drop them."""
    kept = apply_sync_rules([_upload("remote-only", None)], Rules(min_date="2026-01-15T12:00:00Z"))

    assert len(kept) == 1


def test_no_rules_configured_returns_actions_untouched():
    actions = [_at(OLDER), _at(NEWER)]

    kept = apply_sync_rules(actions, Rules())

    assert kept is actions
