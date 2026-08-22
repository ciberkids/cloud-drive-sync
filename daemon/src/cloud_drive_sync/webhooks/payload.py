"""The public event vocabulary, and the JSON envelope sent to receivers.

Two rules govern everything here.

**The public names are not the internal names.** Internal notify names are coupled to
the UI and will be refactored; exporting them would make every internal rename a
breaking change for third parties. The mapping is one table in one place, so an
internal rename is a one-line change here rather than a wire-contract break.

**The identifier is never positional.** ``pair_N`` renumbers when a pair is removed.
Inside the process that is survivable because the stored rows renumber with it; in a
payload it is not, because a receiver keys its own state on whatever we send and we
cannot migrate it. ``scope.pair_id`` carries the pair's stable ``uid``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cloud_drive_sync.webhooks.resolver import ResolvedTarget

#: Bumped only on a breaking change; additive fields do not bump it. Receivers are
#: documented as having to ignore unknown fields, which is what makes that possible.
SCHEMA_VERSION = 1

#: Hard ceiling on a serialised body. Truncating is always better than failing to
#: deliver, so exceeding this after path truncation drops the file lists entirely
#: rather than abandoning the event.
MAX_BODY_BYTES = 1024 * 1024

# Internal notify name -> public event name.
_EVENT_NAMES = {
    "sync_complete": "sync.completed",
    "sync_failed": "sync.failed",
    "delete_blocked": "deletion.blocked",
    "conflict_detected": "conflict.detected",
    "conflict_resolved": "conflict.resolved",
    "activity_stopped": "activity.stopped",
    "activity_resumed": "activity.resumed",
    "transfer_progress": "transfer.progress",
    "daemon_started": "daemon.started",
    "daemon_stopping": "daemon.stopping",
    "webhook_test": "webhook.test",
}

#: Deliberately not exported. ``status_changed`` only ever carries ``"idle"`` -- the
#: interesting transitions are not instrumented -- so a public event would be a
#: constant. Revisit when the engine reports real statuses.
_NOT_EXPORTED = frozenset({"status_changed"})

#: Events a human is waiting on. Never dropped from the delivery queue, because
#: discarding one because a chatty ``file.uploaded`` stream filled the queue would be
#: the worst available behaviour.
PRIORITY_EVENTS = frozenset({
    "deletion.blocked",
    "sync.failed",
    "account.auth_failed",
})

#: Events that are not pair-scoped. Their ``scope`` omits the pair fields rather than
#: inventing a placeholder a receiver would have to special-case.
NON_PAIR_EVENTS = frozenset({
    "daemon.started",
    "daemon.stopping",
    "activity.stopped",
    "activity.resumed",
    "account.added",
    "account.removed",
    "account.auth_failed",
})

#: Keys inside ``params`` that name a path list needing truncation.
_FILE_LIST_KEYS = ("uploaded", "downloaded", "deleted", "conflicted")


def public_name(internal: str) -> str | None:
    """Translate an internal notify name, or ``None`` if it is not exported."""
    if internal in _NOT_EXPORTED:
        return None
    return _EVENT_NAMES.get(internal)


@dataclass(frozen=True)
class RawEvent:
    """An event as it leaves the emitter: no resolution, no serialisation.

    Queued by reference, one instance shared by every target. Building the body is
    the worker's job -- doing it here would put unbounded synchronous CPU on the
    event loop, and for ``sync.completed`` on a large initial sync that is tens of
    thousands of paths serialised once per target, on the thread that is also running
    every other pair's transfers.
    """

    event: str
    params: dict
    event_id: str
    occurred_at: str

    @property
    def is_priority(self) -> bool:
        return self.event in PRIORITY_EVENTS


def make_event(event: str, params: dict) -> RawEvent:
    """Stamp an event with its identity and time. Cheap by design."""
    return RawEvent(
        event=event,
        params=params,
        # Generated once and kept across retries: delivery is at-least-once, so this
        # is the receiver's dedup key. Regenerating it per attempt makes it useless.
        event_id=str(uuid.uuid4()),
        occurred_at=_now(),
    )


def _now() -> str:
    """RFC 3339, UTC, milliseconds. ``Z`` rather than ``+00:00`` for readability."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{datetime.now(UTC).microsecond // 1000:03d}Z"
    )


@dataclass(frozen=True)
class PayloadContext:
    """Everything about *this daemon* that a payload needs.

    Assembled once at startup. ``instance_id`` matters more than it looks: two daemons
    (a laptop and a NAS) syncing the same account are otherwise indistinguishable at
    the receiver, which is exactly what a monitoring dashboard needs to tell apart.
    """

    app: str
    version: str
    instance_id: str


def build_body(
    raw: RawEvent,
    target: ResolvedTarget,
    context: PayloadContext,
    *,
    attempt: int,
    scope: dict,
) -> tuple[bytes, dict]:
    """Serialise one event for one target.

    Returns ``(body_bytes, envelope)``. The envelope is returned too so callers can
    log what was sent without re-serialising, and so tests can assert on structure
    rather than parsing bytes.
    """
    data = _prepare_data(raw, target)
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "event": raw.event,
        "event_id": raw.event_id,
        "occurred_at": raw.occurred_at,
        "source": {
            "app": context.app,
            "version": context.version,
            "instance_id": context.instance_id,
        },
        "scope": _prepare_scope(raw, scope, target),
        "delivery": {
            "target": target.name,
            "target_key": target.target_key,
            "attempt": attempt,
            "sent_at": _now(),
        },
        "data": data,
    }

    body = _dump(envelope)
    if len(body) > MAX_BODY_BYTES:
        # Last resort: drop the path lists and say so. Failing to deliver
        # `sync.completed` at all is worse than delivering it without the samples.
        envelope["data"] = _strip_file_lists(envelope["data"])
        body = _dump(envelope)
    return body, envelope


def _dump(envelope: dict) -> bytes:
    # `default=str` so an unexpected type degrades to its string form rather than
    # raising: a serialisation error at this point would be indistinguishable from a
    # dead endpoint, and the event would be lost for a reason nobody could see.
    return json.dumps(envelope, ensure_ascii=False, default=str).encode("utf-8")


def _prepare_scope(raw: RawEvent, scope: dict, target: ResolvedTarget) -> dict:
    out = dict(scope)
    if raw.event in NON_PAIR_EVENTS:
        for key in ("pair_id", "pair_label", "local_path", "remote_folder_id"):
            out.pop(key, None)
    if not target.include_paths:
        out.pop("local_path", None)
    return out


def _prepare_data(raw: RawEvent, target: ResolvedTarget) -> dict:
    """Copy the params, dropping routing keys and applying the target's path policy."""
    data: dict[str, Any] = {
        k: v for k, v in raw.params.items() if k not in ("pair_id", "pair_label")
    }

    files = data.get("files")
    if not isinstance(files, dict):
        return data

    if not target.include_paths:
        # Correlation without disclosure: enough to count and match, not to read. A
        # webhook ships the user's filenames to a third party, and the destination may
        # be a SaaS log aggregator.
        data["files"] = {
            key: [_hash_path(p) for p in files.get(key, [])[: target.max_files_per_event]]
            for key in _FILE_LIST_KEYS
            if key in files
        }
        data["files_hashed"] = True
        data["files_truncated"] = any(
            len(files.get(key, [])) > target.max_files_per_event
            for key in _FILE_LIST_KEYS
        )
        return data

    limit = target.max_files_per_event
    if limit <= 0:
        data.pop("files", None)
        data["files_truncated"] = any(files.get(key) for key in _FILE_LIST_KEYS)
        return data

    truncated = False
    trimmed: dict[str, list[str]] = {}
    for key in _FILE_LIST_KEYS:
        if key not in files:
            continue
        values = list(files.get(key) or [])
        if len(values) > limit:
            truncated = True
        trimmed[key] = values[:limit]
    data["files"] = trimmed
    # The *counts* elsewhere in the payload are always the true totals, so a receiver
    # that only wants numbers is never misled by truncation.
    data["files_truncated"] = truncated
    return data


def _strip_file_lists(data: dict) -> dict:
    out = dict(data)
    out.pop("files", None)
    out["files_truncated"] = True
    return out


def _hash_path(path: str) -> str:
    import hashlib

    return hashlib.sha256(path.encode("utf-8")).hexdigest()
