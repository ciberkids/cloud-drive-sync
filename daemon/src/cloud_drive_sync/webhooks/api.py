"""Front-end-facing views of webhook configuration.

Two concerns live here rather than in the IPC handlers, because both are easy to get
subtly wrong and both need testing without a running daemon.

**Masking.** A read API that returns secrets is unacceptable -- the web UI would put
them in a browser and in every screenshot. But an API that returns ``"***"`` and a UI
that saves back what it read will overwrite the real secret with three asterisks, and
the user finds out when the webhook starts returning 401. So reads return the secret
as ``None`` plus a ``*_set`` boolean, and writes treat an **absent** field as "leave
unchanged" and an explicit ``None`` as "clear it".

**The authentication guard.** Webhook writes require the HTTP token to be configured.
This is not the posture of the existing endpoints, and the difference is deliberate:
everything they can do moves the user's own data between the user's own accounts,
whereas a webhook write exfiltrates the user's live filename stream to a host of the
caller's choosing and turns the daemon into an HTTP client inside the LAN. On an
upgraded install with no token set, ``/api/*`` is anonymously writable by design
(roadmap item 7 deliberately left upgrades alone), so inheriting that posture here
would be a real hole rather than an accepted one.
"""

from __future__ import annotations

from cloud_drive_sync.webhooks.models import (
    WebhookAuth,
    WebhooksConfig,
    WebhookSignature,
    WebhookTarget,
)
from cloud_drive_sync.webhooks.serialise import target_to_toml, webhooks_to_toml

#: Sentinel meaning "the caller did not mention this field". Distinct from ``None``,
#: which means "clear it".
UNCHANGED = object()

#: Secret fields, by the block they live in. A read replaces the value with ``None``
#: and adds ``"<field>_set": bool``.
_AUTH_SECRETS = ("password", "token", "value")
_SIGNATURE_SECRETS = ("secret",)


class WebhookAuthRequired(Exception):
    """Raised when a webhook write is attempted with no HTTP token configured."""


def masked(cfg: WebhooksConfig) -> dict:
    """A read view with every literal secret removed.

    The ``*_set`` flags are what let a UI render "unchanged — type to replace" instead
    of an empty box that looks like nothing is configured.
    """
    out = webhooks_to_toml(cfg)
    if "defaults" in out:
        out["defaults"] = _mask_target(out["defaults"])
    if "targets" in out:
        out["targets"] = [_mask_target(t) for t in out["targets"]]
    return out


def _mask_target(entry: dict) -> dict:
    out = dict(entry)
    if isinstance(out.get("auth"), dict):
        out["auth"] = _mask_block(out["auth"], _AUTH_SECRETS)
    if isinstance(out.get("signature"), dict):
        out["signature"] = _mask_block(out["signature"], _SIGNATURE_SECRETS)
    return out


def _mask_block(block: dict, secret_fields: tuple[str, ...]) -> dict:
    out = dict(block)
    for name in secret_fields:
        present = bool(out.get(name))
        out[f"{name}_set"] = present
        if present:
            # None rather than a placeholder string: a placeholder round-tripped
            # through a form becomes the stored value.
            out[name] = None
    return out


def apply_update(existing: WebhooksConfig, update: dict) -> WebhooksConfig:
    """Merge a front-end update into a stored config, preserving unmentioned secrets.

    The whole ``targets`` list is replaced when present -- a partial list would make
    deletion impossible -- but each target's secrets are carried over from the target
    of the same name in ``existing`` unless the caller supplied a new value.
    """
    result = WebhooksConfig(
        enabled=_pick(update, "enabled", existing.enabled),
        allow_private_addresses=_pick(
            update, "allow_private_addresses", existing.allow_private_addresses
        ),
        defaults=existing.defaults,
    )
    if "defaults" in update:
        result.defaults = (
            _target_from_update(update["defaults"], existing.defaults)
            if isinstance(update["defaults"], dict)
            else None
        )

    if "targets" not in update:
        result.targets = list(existing.targets)
        return result

    by_name = {t.name: t for t in existing.targets}
    for entry in update.get("targets") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        result.targets.append(_target_from_update(entry, by_name.get(name)))
    return result


def _pick(update: dict, key: str, current):
    """Absent means unchanged; present -- including ``None`` -- means the new value."""
    return update[key] if key in update else current


def _target_from_update(entry: dict, previous: WebhookTarget | None) -> WebhookTarget:
    from cloud_drive_sync.webhooks.serialise import target_from_toml

    target = target_from_toml(entry)
    if target.auth is not None:
        target.auth = _carry_secrets(
            target.auth,
            previous.auth if previous else None,
            _AUTH_SECRETS,
            entry.get("auth") or {},
        )
    if target.signature is not None:
        target.signature = _carry_secrets(
            target.signature,
            previous.signature if previous else None,
            _SIGNATURE_SECRETS,
            entry.get("signature") or {},
        )
    return target


def _carry_secrets(new, old, secret_fields: tuple[str, ...], raw: dict):
    """Preserve a stored secret the caller did not mention.

    This is the masking trap in one function: a UI that GETs a masked config and PUTs
    it back unchanged must not wipe the credential.
    """
    if old is None:
        return new
    for name in secret_fields:
        if name not in raw or raw.get(name) in (None, ""):
            # Not mentioned, or explicitly blank from a masked read: keep the stored
            # value. Clearing a secret is done by removing the auth block or changing
            # the mode, which is unambiguous.
            setattr(new, name, getattr(old, name, ""))
    return new


def require_authentication(token: str | None, *, account: bool = False) -> None:
    """Refuse a webhook write when authentication is not configured.

    Either credential counts. Before the web UI gained a sign-in account, "a token
    is set" *was* the definition of authenticated — so this used to check only the
    token, and leaving it that way would have refused webhook configuration on a
    daemon whose UI asks for a username and password, telling the operator to set
    a token they had deliberately replaced.
    """
    if not token and not account:
        raise WebhookAuthRequired(
            "Webhook configuration requires authentication to be enabled. This "
            "daemon has no HTTP token and no web UI account, so the API is "
            "reachable without credentials — and a webhook can send your file "
            "activity to any host. Set a token ('cloud-drive-sync gen-token') or "
            "create an account ('cloud-drive-sync user set <name>'), then retry."
        )


def require_http_token(token: str | None) -> None:
    """Token-only form of :func:`require_authentication`.

    Kept because it is the narrower question and reads better at a call site that
    genuinely means "is there a token"; the webhook handlers use the wider one.
    """
    require_authentication(token)


def summarise_target(target: WebhookTarget) -> dict:
    """A compact row for ``webhook list``, with no secrets."""
    entry = target_to_toml(target)
    auth = entry.get("auth") or {}
    return {
        "name": target.name,
        "define": target.define,
        "url": target.url,
        "events": target.events,
        "enabled": target.enabled,
        "auth_mode": auth.get("mode"),
        "signed": target.signature is not None,
    }


def blank_auth(mode: str) -> WebhookAuth:
    """An auth block with only its mode set, for building config programmatically."""
    return WebhookAuth(mode=mode)


def blank_signature() -> WebhookSignature:
    return WebhookSignature()
