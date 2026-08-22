"""TOML marshalling for webhook configuration.

Kept out of :mod:`cloud_drive_sync.config` for one blunt reason: ``Config.save``
rebuilds its dict from scratch and ``Config.load`` reads key by key, so **any key not
enumerated in both is dropped on load and erased from the file on the next save** --
and every settings handler ends in ``save()``. A hand-edited ``[webhooks]`` block
would vanish the first time the user changed an unrelated setting in the UI.

Three levels of nesting written twice by hand is where that goes wrong, so the round
trip lives here, in one place, with a test that asserts it survives.

The unmarshal side is deliberately lenient: an unknown key is ignored rather than
fatal, because a config written by a newer version must not stop an older daemon
starting. The marshal side omits every ``None``, so an inherited value is never
frozen into the file as a literal -- otherwise editing a global default would stop
affecting any pair whose config had been round-tripped through the UI.
"""

from __future__ import annotations

from typing import Any

from cloud_drive_sync.webhooks.models import (
    WebhookAuth,
    WebhooksConfig,
    WebhookSignature,
    WebhookTarget,
)

# Fields carrying a plain scalar, unmarshalled with no conversion. Split by expected
# type so a string where a bool belongs is corrected rather than propagated into the
# resolver, where `"false"` would be truthy and silently enable something.
_BOOL_FIELDS = ("enabled", "verify_tls", "include_paths")
_INT_FIELDS = ("timeout_seconds", "max_attempts", "max_files_per_event")
_STR_LIST_FIELDS = ("events", "events_add", "events_remove", "headers_remove")


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):  # bool is an int subclass; not what was meant
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_str_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        return [value]  # a bare string is an obvious single-entry list
    if isinstance(value, list):
        return [str(v) for v in value]
    return None


def auth_from_toml(data: dict) -> WebhookAuth:
    return WebhookAuth(
        mode=str(data.get("mode", "")),
        username=str(data.get("username", "")),
        password=str(data.get("password", "")),
        password_env=str(data.get("password_env", "")),
        token=str(data.get("token", "")),
        token_env=str(data.get("token_env", "")),
        header=str(data.get("header", "")),
        value=str(data.get("value", "")),
        value_env=str(data.get("value_env", "")),
    )


def auth_to_toml(auth: WebhookAuth) -> dict:
    out: dict = {"mode": auth.mode}
    for name in (
        "username", "password", "password_env",
        "token", "token_env", "header", "value", "value_env",
    ):
        value = getattr(auth, name)
        if value:
            out[name] = value
    return out


def signature_from_toml(data: dict) -> WebhookSignature:
    return WebhookSignature(
        secret=str(data.get("secret", "")),
        secret_env=str(data.get("secret_env", "")),
        algorithm=str(data.get("algorithm", "sha256")),
        header=str(data.get("header", "X-CDS-Signature")),
        timestamp_header=str(data.get("timestamp_header", "X-CDS-Timestamp")),
    )


def signature_to_toml(sig: WebhookSignature) -> dict:
    out: dict = {}
    for name in ("secret", "secret_env"):
        value = getattr(sig, name)
        if value:
            out[name] = value
    # Written unconditionally: they have non-empty defaults, and a receiver's
    # verification breaks if the header name silently changes under it.
    out["algorithm"] = sig.algorithm
    out["header"] = sig.header
    out["timestamp_header"] = sig.timestamp_header
    return out


def target_from_toml(data: dict) -> WebhookTarget:
    target = WebhookTarget(
        name=str(data.get("name", "")),
        define=bool(data.get("define", False)),
        url=str(data["url"]) if data.get("url") is not None else None,
    )
    for name in _BOOL_FIELDS:
        if name in data:
            setattr(target, name, _as_bool(data[name]))
    for name in _INT_FIELDS:
        if name in data:
            setattr(target, name, _as_int(data[name]))
    for name in _STR_LIST_FIELDS:
        if name in data:
            setattr(target, name, _as_str_list(data[name]))
    if isinstance(data.get("headers"), dict):
        target.headers = {str(k): str(v) for k, v in data["headers"].items()}
    if isinstance(data.get("auth"), dict):
        target.auth = auth_from_toml(data["auth"])
    if isinstance(data.get("signature"), dict):
        target.signature = signature_from_toml(data["signature"])
    return target


def target_to_toml(target: WebhookTarget, *, include_name: bool = True) -> dict:
    """Marshal a target fragment, omitting everything unset.

    Omission is what preserves inheritance: writing a resolved value would freeze it,
    and the user's later edit to the level above would appear to do nothing.
    """
    out: dict = {}
    if include_name:
        out["name"] = target.name
    if target.define:
        out["define"] = True
    if target.url is not None:
        out["url"] = target.url
    for name in (*_STR_LIST_FIELDS, *_BOOL_FIELDS, *_INT_FIELDS):
        value = getattr(target, name)
        # `is not None`, never truthiness: `enabled = false`, `include_paths = false`
        # and `max_files_per_event = 0` are all meaningful values, and dropping them
        # here silently re-inherits whatever the level above says.
        if value is not None:
            out[name] = value
    if target.headers is not None:
        out["headers"] = dict(target.headers)
    if target.auth is not None:
        out["auth"] = auth_to_toml(target.auth)
    if target.signature is not None:
        out["signature"] = signature_to_toml(target.signature)
    return out


def webhooks_from_toml(data: dict | None) -> WebhooksConfig:
    """Read a ``[webhooks]`` block. A missing or malformed block yields an empty one."""
    if not isinstance(data, dict):
        return WebhooksConfig()
    cfg = WebhooksConfig()
    if "enabled" in data:
        cfg.enabled = _as_bool(data["enabled"])
    if "allow_private_addresses" in data:
        cfg.allow_private_addresses = _as_bool(data["allow_private_addresses"])
    if isinstance(data.get("defaults"), dict):
        cfg.defaults = target_from_toml(data["defaults"])
    for entry in data.get("targets", []) or []:
        if isinstance(entry, dict):
            cfg.targets.append(target_from_toml(entry))
    return cfg


def webhooks_to_toml(cfg: WebhooksConfig) -> dict:
    """Marshal a ``[webhooks]`` block, or ``{}`` when it says nothing."""
    if cfg.is_empty():
        return {}
    out: dict = {}
    if cfg.enabled is not None:
        out["enabled"] = cfg.enabled
    if cfg.allow_private_addresses is not None:
        out["allow_private_addresses"] = cfg.allow_private_addresses
    if cfg.defaults is not None:
        defaults = target_to_toml(cfg.defaults, include_name=False)
        if defaults:
            out["defaults"] = defaults
    if cfg.targets:
        out["targets"] = [target_to_toml(t) for t in cfg.targets]
    return out
