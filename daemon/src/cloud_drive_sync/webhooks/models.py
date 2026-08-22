"""Configuration shapes for outbound event webhooks.

Every overridable field is ``X | None`` and defaults to ``None``, meaning *inherit
from the level above*. This is not stylistic. The existing precedent in
:mod:`cloud_drive_sync.config` is deliberately asymmetric -- ``conflict_strategy``
uses ``""`` and is saved on truthiness, while ``max_deletions_per_sync`` uses ``None``
and is saved on ``is not None`` -- because ``0`` there is a *meaningful* value (it
disables the guard) and a truthiness test would drop it and silently re-inherit the
global limit.

Webhook config is full of meaningful falsy values: ``enabled = false``,
``verify_tls = false``, ``include_paths = false``, ``max_files_per_event = 0``. So
every one of them must be ``None``-sentinelled and tested with ``is not None``.
Getting this wrong produces the worst bug this feature can have: a user switches a
webhook off, the setting is discarded as falsy, and it keeps firing.

Three fields are **level-local operations** rather than inheritable values --
``define``, ``events_add``/``events_remove`` and ``headers_remove``. They are applied
at their own level's turn during resolution and then discarded. If they inherited like
ordinary fields, an account-level ``events_add`` would survive a pair-level ``events``
replace and defeat the narrowing that replace exists to provide.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Authorization modes understood by the delivery layer. ``jwt`` is deliberately
#: absent: minting is phase 3, and a mode accepted by config but unimplemented at
#: delivery time would fail once per event, indistinguishable from a dead endpoint.
AUTH_MODES = ("none", "basic", "bearer", "custom")

#: Which fields each mode requires. Checked during resolution, not at delivery --
#: a target missing its credential must be a startup error naming the level that
#: broke it, not a per-event 401. Each entry is a list of alternatives: at least one
#: of each inner tuple must be set.
AUTH_REQUIRED_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "none": (),
    "basic": (("username",), ("password", "password_env")),
    "bearer": (("token", "token_env"),),
    "custom": (("header",), ("value", "value_env")),
}


@dataclass
class WebhookAuth:
    """How to authenticate to a target.

    ``mode`` is mandatory whenever this block is present. There is deliberately no
    default: ``auth`` is replaced atomically during the merge, so a lower level
    writing ``auth = {token_env = "OTHER"}`` discards the inherited ``mode``. If a
    missing mode defaulted to ``none`` that would be a silent downgrade to
    unauthenticated POSTs -- so it is a resolution error instead.
    """

    mode: str = ""
    username: str = ""
    password: str = ""
    password_env: str = ""
    token: str = ""
    token_env: str = ""
    header: str = ""
    value: str = ""
    value_env: str = ""

    def secret_fields(self) -> list[str]:
        """Names of the fields holding a literal secret, for redaction and masking."""
        return ["password", "token", "value"]


@dataclass
class WebhookSignature:
    """HMAC signature over the request body.

    Composes with any ``auth`` mode rather than replacing it: every auth mode
    authenticates the *sender*, while this lets the receiver verify the *payload* --
    that it came from us, unaltered, and is not a replay.
    """

    secret: str = ""
    secret_env: str = ""
    algorithm: str = "sha256"
    header: str = "X-CDS-Signature"
    timestamp_header: str = "X-CDS-Timestamp"

    def secret_fields(self) -> list[str]:
        return ["secret"]


@dataclass
class WebhookTarget:
    """One named callback, as written at a single configuration level.

    An instance is a *fragment*, not a complete target: outside the level that defines
    a name, most fields are ``None`` and inherited. :func:`~.resolver.resolve_targets`
    turns a stack of fragments into a :class:`~.resolver.ResolvedTarget`.
    """

    name: str = ""

    #: Level-local. Declares that this entry introduces a new name rather than
    #: overriding an inherited one. Required for a new name, rejected for an existing
    #: one -- without it, a name collision with a higher level is indistinguishable
    #: from a deliberate override, and the failure is silent in the worst direction:
    #: the entry merges onto a disabled target, inherits ``enabled = false``, and a
    #: webhook the user just switched on never fires.
    define: bool = False

    url: str | None = None
    events: list[str] | None = None

    #: Level-local delta operations. See the module docstring.
    events_add: list[str] | None = None
    events_remove: list[str] | None = None

    headers: dict[str, str] | None = None
    #: Level-local. TOML has no ``null``, so removing an inherited header needs its
    #: own list.
    headers_remove: list[str] | None = None

    auth: WebhookAuth | None = None
    signature: WebhookSignature | None = None

    enabled: bool | None = None
    timeout_seconds: int | None = None
    max_attempts: int | None = None
    verify_tls: bool | None = None
    include_paths: bool | None = None
    max_files_per_event: int | None = None


#: Fields that are applied at their own level and never inherited downward.
LEVEL_LOCAL_FIELDS = ("define", "events_add", "events_remove", "headers_remove")

#: Fields a ``defaults`` block may supply. Deliberately excludes identity and routing
#: (``name``, ``url``, ``events``): a default URL would silently give a half-written
#: target somewhere to post to, which is exactly the accident resolution should catch.
DEFAULTABLE_FIELDS = (
    "timeout_seconds",
    "max_attempts",
    "verify_tls",
    "include_paths",
    "max_files_per_event",
    "enabled",
)


@dataclass
class WebhooksConfig:
    """The ``[webhooks]`` block at one configuration level."""

    #: Tri-state. ``None`` inherits; ``False`` switches every target at this level and
    #: below off; ``True`` opts back in.
    enabled: bool | None = None

    #: Target-shaped block applied to fields no target set for itself.
    defaults: WebhookTarget | None = None

    targets: list[WebhookTarget] = field(default_factory=list)

    #: Global level only. A hardening switch for shared deployments; it is not
    #: overridable, so a pair cannot re-enable what an administrator turned off.
    allow_private_addresses: bool | None = None

    def is_empty(self) -> bool:
        """Whether this block says nothing, so ``save()`` can omit it entirely."""
        return (
            self.enabled is None
            and self.defaults is None
            and not self.targets
            and self.allow_private_addresses is None
        )


#: Defaults for a target that no level specified. Chosen for the common case of a
#: self-hosted receiver on the same network.
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_VERIFY_TLS = True
DEFAULT_INCLUDE_PATHS = True
DEFAULT_MAX_FILES_PER_EVENT = 100
