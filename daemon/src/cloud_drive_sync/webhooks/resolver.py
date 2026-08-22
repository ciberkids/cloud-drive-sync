"""Merge webhook configuration across levels into a list of deliverable targets.

The hierarchy is *named targets merged by name*. A lower level may override fields of
an inherited target, switch it off, or introduce one that exists nowhere above it --
which is what "override, or set only at the lower level" requires and what plain
scalar override cannot express.

Resolution never raises on bad configuration. It returns the targets it could build
plus a list of problems, so a single malformed target drops out with an explanation
instead of taking the daemon down or -- worse -- silently becoming a no-op.

The one non-obvious rule is that ``define``, ``events_add``, ``events_remove`` and
``headers_remove`` are *operations applied at their own level's turn*, not inheritable
fields. :mod:`.models` explains why; the short version is that an inheritable
``events_add`` would survive a lower level's ``events`` replace and defeat the
narrowing that replace exists to provide.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, replace
from typing import Any

from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.webhooks.models import (
    AUTH_MODES,
    AUTH_REQUIRED_FIELDS,
    DEFAULT_INCLUDE_PATHS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_FILES_PER_EVENT,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VERIFY_TLS,
    DEFAULTABLE_FIELDS,
    WebhookAuth,
    WebhooksConfig,
    WebhookSignature,
    WebhookTarget,
)

log = get_logger("webhooks.resolver")

#: Scope labels. Also the first component of ``target_key``.
SCOPE_GLOBAL = "global"


@dataclass(frozen=True)
class ResolvedTarget:
    """A fully merged, validated target, ready to deliver to."""

    #: Identity for all *delivery* state -- queue, worker, circuit breaker, counters.
    #:
    #: Deliberately not ``name``. A name is unique only within one pair's resolution:
    #: two pairs may each define ``photo-indexer`` pointing at different hosts, and
    #: the same name resolves to different auth and headers per pair. Keying a circuit
    #: breaker on the name alone would let one dead endpoint open another's.
    target_key: str

    #: Display name, and the merge key within a resolution.
    name: str

    #: The level that introduced this name: ``global``, ``provider:email``, or a pair
    #: uid. Combined with ``name`` it forms ``target_key``.
    defining_scope: str

    url: str
    events: tuple[str, ...]
    headers: dict[str, str]
    auth: WebhookAuth
    signature: WebhookSignature | None
    timeout_seconds: int
    max_attempts: int
    verify_tls: bool
    include_paths: bool
    max_files_per_event: int

    def matches(self, event: str) -> bool:
        """Whether this target wants ``event``.

        One glob segment is supported, so ``conflict.*`` covers both conflict events
        and ``*`` covers everything.
        """
        return any(fnmatch.fnmatchcase(event, pattern) for pattern in self.events)


@dataclass
class _Accumulator:
    """A target being merged, plus which fields were set explicitly.

    The explicit set is what lets a ``defaults`` block fill only the gaps: a target's
    own value must always beat any level's defaults, whichever order they are applied
    in.
    """

    defining_scope: str
    values: dict[str, Any]
    explicit: set[str]
    #: Which level last set ``enabled``, and which last touched the target at all.
    #: Both are needed to tell "this level switched it off" (deliberate) from "a lower
    #: level overrode something already off" (almost always a mistake). ``explicit``
    #: cannot answer that: it accumulates across levels, so it says only that *some*
    #: level set the field.
    enabled_scope: str | None = None
    last_touched_scope: str = ""


_MERGEABLE_FIELDS = (
    "url",
    "events",
    "headers",
    "auth",
    "signature",
    "enabled",
    "timeout_seconds",
    "max_attempts",
    "verify_tls",
    "include_paths",
    "max_files_per_event",
)


def resolve_targets(
    levels: list[tuple[str, WebhooksConfig | None]],
) -> tuple[list[ResolvedTarget], list[str]]:
    """Merge an ordered stack of levels into deliverable targets.

    ``levels`` runs outermost first -- ``[("global", cfg), ("pair:<uid>", cfg)]`` --
    so later entries override earlier ones. Passing a single level is legitimate and
    is how non-pair-scoped events resolve.

    Returns ``(targets, problems)``. ``problems`` are human-readable strings naming
    the level at fault; callers log them and surface them in ``--explain``.
    """
    problems: list[str] = []

    # ── Step 6 first: a disabled block short-circuits everything below it. ──
    enabled = True
    for _scope, cfg in levels:
        if cfg is not None and cfg.enabled is not None:
            enabled = cfg.enabled
    if not enabled:
        return [], problems

    # ── Steps 1-3: accumulate by name, in definition order. ──
    acc: dict[str, _Accumulator] = {}
    for scope, cfg in levels:
        if cfg is None:
            continue
        for entry in cfg.targets:
            _apply_entry(scope, entry, acc, problems)

    # ── Step 4: defaults fill only what no target set for itself. ──
    for scope, cfg in levels:
        if cfg is None or cfg.defaults is None:
            continue
        _apply_defaults(cfg.defaults, acc)

    # ── Steps 5 and 7: drop disabled, validate the rest. ──
    resolved: list[ResolvedTarget] = []
    for name, item in acc.items():
        if item.values.get("enabled") is False:
            if item.last_touched_scope != item.enabled_scope:
                # Inherited rather than chosen here. Worth saying out loud: this is
                # the silent-misfire case -- a lower level merged onto a disabled
                # target and its new URL will never be used.
                problems.append(
                    f"target {name!r} was overridden at {item.last_touched_scope} but "
                    f"is disabled by an inherited 'enabled = false' from "
                    f"{item.enabled_scope}; it will not fire. Set 'enabled = true' "
                    f"there, or use a different name with 'define = true' if a new "
                    f"target was intended"
                )
            continue
        target = _build(name, item, problems)
        if target is not None:
            resolved.append(target)

    return resolved, problems


def _apply_entry(
    scope: str,
    entry: WebhookTarget,
    acc: dict[str, _Accumulator],
    problems: list[str],
) -> None:
    """Merge one level's entry for one name into the accumulator."""
    name = (entry.name or "").strip()
    if not name:
        problems.append(f"a webhook target at {scope} has no 'name'")
        return

    existing = acc.get(name)

    # Intent must be explicit, in both directions.
    if existing is None:
        if not entry.define:
            problems.append(
                f"target {name!r} at {scope} introduces a new name without "
                f"'define = true'; add it, or correct the name to match an "
                f"inherited target"
            )
            return
        acc[name] = _Accumulator(
            defining_scope=scope, values={}, explicit=set(), last_touched_scope=scope
        )
        existing = acc[name]
    elif entry.define:
        problems.append(
            f"target {name!r} at {scope} sets 'define = true' but that name is "
            f"already defined at {existing.defining_scope}; drop 'define' to "
            f"override it, or choose a different name"
        )
        return

    # Ordinary fields: present overwrites, absent inherits.
    for field_name in _MERGEABLE_FIELDS:
        value = getattr(entry, field_name)
        if value is None:
            continue
        if field_name == "headers":
            merged = dict(existing.values.get("headers") or {})
            merged.update(value)
            existing.values["headers"] = merged
        else:
            existing.values[field_name] = value
        existing.explicit.add(field_name)
        if field_name == "enabled":
            existing.enabled_scope = scope

    # Level-local operations, applied now and not carried forward.
    if entry.events_add:
        current = list(existing.values.get("events") or [])
        current.extend(e for e in entry.events_add if e not in current)
        existing.values["events"] = current
        existing.explicit.add("events")
    if entry.events_remove:
        current = list(existing.values.get("events") or [])
        existing.values["events"] = [e for e in current if e not in entry.events_remove]
        existing.explicit.add("events")
    if entry.headers_remove:
        current = dict(existing.values.get("headers") or {})
        for key in entry.headers_remove:
            current.pop(key, None)
        existing.values["headers"] = current
        existing.explicit.add("headers")

    existing.last_touched_scope = scope


def _apply_defaults(defaults: WebhookTarget, acc: dict[str, _Accumulator]) -> None:
    """Fill fields no target set for itself.

    A level's ``defaults`` reaches targets inherited from *above* it as well as ones
    it defines. That is the useful reading -- "these are this pair's timeouts" -- and
    it is what makes a ``defaults`` block worth having anywhere but the global level.
    A later level's defaults beat an earlier level's, but never an explicit value.
    """
    for item in acc.values():
        for field_name in DEFAULTABLE_FIELDS:
            if field_name in item.explicit:
                continue
            value = getattr(defaults, field_name)
            if value is not None:
                item.values[field_name] = value


def _build(name: str, item: _Accumulator, problems: list[str]) -> ResolvedTarget | None:
    """Validate a merged target and freeze it, or explain why it cannot be used."""
    scope = item.defining_scope
    values = item.values

    url = values.get("url")
    if not url:
        problems.append(f"target {name!r} (from {scope}) has no 'url'; dropped")
        return None

    events = tuple(values.get("events") or ())
    if not events:
        problems.append(
            f"target {name!r} (from {scope}) has an empty event list; dropped "
            f"(a target that matches nothing is never what was meant)"
        )
        return None

    auth = values.get("auth") or WebhookAuth(mode="none")
    if not _validate_auth(name, scope, auth, problems):
        return None

    signature = values.get("signature")
    if signature is not None and not _validate_signature(name, scope, signature, problems):
        return None

    return ResolvedTarget(
        target_key=f"{scope}|{name}",
        name=name,
        defining_scope=scope,
        url=url,
        events=events,
        headers=dict(values.get("headers") or {}),
        auth=auth,
        signature=signature,
        timeout_seconds=_or_default(values, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        max_attempts=_or_default(values, "max_attempts", DEFAULT_MAX_ATTEMPTS),
        verify_tls=_or_default(values, "verify_tls", DEFAULT_VERIFY_TLS),
        include_paths=_or_default(values, "include_paths", DEFAULT_INCLUDE_PATHS),
        max_files_per_event=_or_default(
            values, "max_files_per_event", DEFAULT_MAX_FILES_PER_EVENT
        ),
    )


def _or_default(values: dict, key: str, default: Any) -> Any:
    """``is not None``, never truthiness -- ``0`` and ``False`` are real values here."""
    value = values.get(key)
    return default if value is None else value


def _validate_auth(
    name: str, scope: str, auth: WebhookAuth, problems: list[str]
) -> bool:
    if not auth.mode:
        problems.append(
            f"target {name!r} (from {scope}) has an 'auth' block with no 'mode'. "
            f"'auth' is replaced as a whole rather than merged, so a lower level "
            f"overriding just a token loses the inherited mode -- state it explicitly. "
            f"One of: {', '.join(AUTH_MODES)}"
        )
        return False
    if auth.mode not in AUTH_MODES:
        problems.append(
            f"target {name!r} (from {scope}) has unknown auth mode {auth.mode!r}; "
            f"expected one of: {', '.join(AUTH_MODES)}"
        )
        return False
    for alternatives in AUTH_REQUIRED_FIELDS[auth.mode]:
        if not any(getattr(auth, f, "") for f in alternatives):
            problems.append(
                f"target {name!r} (from {scope}) uses auth mode {auth.mode!r} but "
                f"sets none of: {', '.join(alternatives)}"
            )
            return False
    return True


def _validate_signature(
    name: str, scope: str, signature: WebhookSignature, problems: list[str]
) -> bool:
    if not (signature.secret or signature.secret_env):
        problems.append(
            f"target {name!r} (from {scope}) has a 'signature' block with neither "
            f"'secret' nor 'secret_env'"
        )
        return False
    if signature.algorithm not in ("sha256", "sha512"):
        problems.append(
            f"target {name!r} (from {scope}) has unsupported signature algorithm "
            f"{signature.algorithm!r}; expected 'sha256' or 'sha512'"
        )
        return False
    return True


def explain(
    levels: list[tuple[str, WebhooksConfig | None]],
) -> list[dict]:
    """Per-level trace of how each target was built, for ``webhook list --explain``.

    A three-level merge is not debuggable by reading the config -- this is what makes
    "which webhooks will actually fire for this folder, and why" answerable.
    """
    rows: list[dict] = []
    acc: dict[str, _Accumulator] = {}
    for scope, cfg in levels:
        if cfg is None:
            continue
        for entry in cfg.targets:
            before = dict(acc.get(entry.name).values) if entry.name in acc else None
            _apply_entry(scope, entry, acc, [])
            after = dict(acc[entry.name].values) if entry.name in acc else None
            rows.append(
                {
                    "scope": scope,
                    "name": entry.name,
                    "action": "define" if before is None else "override",
                    "changed": sorted(
                        k for k in (after or {})
                        if before is None or before.get(k) != (after or {}).get(k)
                    ),
                }
            )
    return rows


def target_for_display(target: ResolvedTarget) -> dict:
    """A target reduced to what is safe to show or log.

    No secrets, and the URL is stripped to scheme, host and port -- a token in a query
    parameter is a common receiver design, so the path and query never leave here.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(target.url)
    return {
        "target_key": target.target_key,
        "name": target.name,
        "defining_scope": target.defining_scope,
        "endpoint": f"{parts.scheme}://{parts.netloc}",
        "events": list(target.events),
        "auth_mode": target.auth.mode,
        "signed": target.signature is not None,
        "verify_tls": target.verify_tls,
        "include_paths": target.include_paths,
    }


def with_scope(target: ResolvedTarget, scope: str) -> ResolvedTarget:
    """Re-key a target to a different defining scope (used by tests and tooling)."""
    return replace(target, defining_scope=scope, target_key=f"{scope}|{target.name}")
