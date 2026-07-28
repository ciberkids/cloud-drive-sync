"""Delete fail-safe: refuse a sync pass that would delete implausibly many files.

Sync is symmetric, so a local catastrophe propagates. If the filesystem is wiped
— a bad ``rm -rf``, an external drive unmounted while its mountpoint is still a
configured sync path, a disk failure, a container recreated with an empty volume
— the daemon sees thousands of deletions as legitimate user intent and faithfully
deletes the cloud copy too. The backup becomes a mirror of the disaster, and the
user finds out afterwards (issue #53).

This module decides whether a planned batch is plausible. It holds no state and
performs no I/O, so the decision is testable in isolation from the engine.

Design notes:

* **Fails closed.** Over the limit means nothing is deleted and a human is asked.
  A daemon that cannot ask — headless, no UI attached — must wait, never assume
  consent. Waiting costs a delay; guessing costs the data.
* **Directions are independent.** A wiped *remote* emptying the local copy is the
  same threat mirrored, and download-only pairs make it reachable, so local and
  remote deletions are counted and capped separately.
* **Count and ratio, whichever trips first.** 500 deletions is unremarkable in a
  200k-file library and catastrophic in a 600-file one. An absolute count catches
  the large-library case; a ratio of what is tracked catches the small one.
* **Counted over a time window, not per pass.** A per-pass cap alone is defeated
  by a slow drip: 99 deletions per pass, repeated, never trips a limit of 100 and
  still empties the library. The window counts deletions already performed
  recently and adds the proposed batch, so the limit means "no more than N
  deletions in any W seconds" — which a mass delete breaches on its first pass
  and a drip breaches on its Nth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from cloud_drive_sync.sync.planner import ActionType, SyncAction
from cloud_drive_sync.util.logging import get_logger

log = get_logger("sync.failsafe")

# Deletions above this count in a single pass need confirmation. Chosen to sit
# well above routine cleanup (emptying a folder, a bulk rename producing
# delete+upload pairs) and well below the scale of an accident.
DEFAULT_MAX_DELETIONS = 100

# ...or above this fraction of the files currently tracked for the pair, which is
# what catches a small library being wiped entirely.
DEFAULT_MAX_DELETION_RATIO = 0.5

# Never gate batches this small, whatever the ratio says. Deleting 3 of 4 tracked
# files is 75% and entirely normal.
RATIO_FLOOR = 10

# Deletions are counted over a sliding window this many seconds wide, across sync
# passes. One minute is long enough to catch a drip that a per-pass cap misses,
# short enough that ordinary staggered cleanup is not penalised.
DEFAULT_WINDOW_SECONDS = 60

# How many paths to keep for the confirmation prompt. Enough to recognise what is
# being deleted; few enough to store and render.
SAMPLE_SIZE = 20

DELETE_ACTIONS = (ActionType.DELETE_LOCAL, ActionType.DELETE_REMOTE)


class Direction(StrEnum):
    """Which side the deletions would land on."""

    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True)
class DeletionBreach:
    """A refused group of deletions, and enough context to decide about it."""

    direction: Direction
    count: int
    limit: int
    tracked: int
    sample: list[str] = field(default_factory=list)
    #: Deletions already performed in this direction inside the window.
    recent: int = 0
    window_seconds: int = DEFAULT_WINDOW_SECONDS

    @property
    def ratio(self) -> float:
        return self.count / self.tracked if self.tracked else 0.0

    @property
    def total_in_window(self) -> int:
        """Proposed plus already-performed, which is what the limit applies to."""
        return self.count + self.recent

    def describe(self) -> str:
        """One line suitable for a log entry or a notification body."""
        where = "local files" if self.direction is Direction.LOCAL else "remote files"
        parts = [f"{self.count} {where} would be deleted"]
        if self.recent:
            parts.append(
                f"on top of {self.recent} already deleted in the last "
                f"{self.window_seconds}s ({self.total_in_window} total)"
            )
        if self.tracked:
            parts.append(f"{self.ratio:.0%} of {self.tracked} tracked")
        return ", ".join(parts) + f" — limit is {self.limit}"


@dataclass(frozen=True)
class Verdict:
    """The outcome of checking one planned batch."""

    breaches: list[DeletionBreach] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.breaches)

    def describe(self) -> str:
        return "; ".join(b.describe() for b in self.breaches)


def effective_limits(
    global_max: int,
    pair_max: int | None = None,
) -> int:
    """Resolve the per-pair override against the global default.

    ``None`` means inherit; ``0`` is a deliberate opt-out and disables the guard
    for that pair. Negative values are treated as the default rather than as an
    opt-out, since a negative limit is a mistake rather than an intent.
    """
    limit = pair_max if pair_max is not None else global_max
    return DEFAULT_MAX_DELETIONS if limit < 0 else limit


def check(
    actions: list[SyncAction],
    *,
    max_deletions: int = DEFAULT_MAX_DELETIONS,
    tracked_files: int = 0,
    max_ratio: float = DEFAULT_MAX_DELETION_RATIO,
    recent_deletions: dict[str, int] | None = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> Verdict:
    """Decide whether ``actions`` may proceed.

    Args:
        actions: the planned batch, including non-deletions.
        max_deletions: cap per direction, applied to the proposed batch *plus*
            anything already deleted inside the window. ``0`` disables the guard.
        tracked_files: how many files the database currently tracks for this
            pair, used for the ratio check. ``0`` skips the ratio check.
        max_ratio: fraction of ``tracked_files`` above which a batch is refused.
        recent_deletions: ``{"local": n, "remote": n}`` already performed inside
            the window. Omitting it makes the check per-pass only, which a slow
            drip can defeat.
        window_seconds: width of that window, for reporting.

    Returns:
        A :class:`Verdict`. ``blocked`` is false when the batch is plausible.
    """
    if max_deletions <= 0:
        return Verdict()

    recent = recent_deletions or {}

    grouped: dict[Direction, list[SyncAction]] = {Direction.LOCAL: [], Direction.REMOTE: []}
    for action in actions:
        if action.action is ActionType.DELETE_LOCAL:
            grouped[Direction.LOCAL].append(action)
        elif action.action is ActionType.DELETE_REMOTE:
            grouped[Direction.REMOTE].append(action)

    breaches: list[DeletionBreach] = []
    for direction, group in grouped.items():
        count = len(group)
        if not count:
            continue

        already = int(recent.get(direction.value, 0) or 0)
        # The limit applies to the window, not to this pass alone.
        over_count = (count + already) > max_deletions
        over_ratio = (
            tracked_files >= RATIO_FLOOR
            and count >= RATIO_FLOOR
            and (count / tracked_files) > max_ratio
        )
        if not (over_count or over_ratio):
            continue

        breaches.append(
            DeletionBreach(
                direction=direction,
                count=count,
                limit=max_deletions,
                tracked=tracked_files,
                sample=sorted(a.path for a in group)[:SAMPLE_SIZE],
                recent=already,
                window_seconds=window_seconds,
            )
        )

    if breaches:
        log.warning(
            "Delete fail-safe refused a batch: %s",
            "; ".join(b.describe() for b in breaches),
        )
    return Verdict(breaches=breaches)


def without_deletions(actions: list[SyncAction]) -> list[SyncAction]:
    """The batch with every deletion removed.

    Not used when a breach blocks the pair — nothing runs then — but kept for the
    case where a user rejects the deletions and the remaining work should still
    proceed.
    """
    return [a for a in actions if a.action not in DELETE_ACTIONS]


def only_deletions(actions: list[SyncAction]) -> list[SyncAction]:
    """Just the deletions, for replaying them once a user approves."""
    return [a for a in actions if a.action in DELETE_ACTIONS]
