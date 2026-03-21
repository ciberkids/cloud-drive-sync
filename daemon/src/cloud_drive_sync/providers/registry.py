"""Provider registry: maps provider names to their implementation classes."""

from __future__ import annotations

from dataclasses import dataclass

from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.registry")


@dataclass
class ProviderEntry:
    """Registration entry for a cloud storage provider."""

    name: str
    client_cls: type
    ops_cls: type
    poller_cls: type
    auth_cls: type
    available: bool = True
    display_name: str = ""
    description: str = ""


_PROVIDERS: dict[str, ProviderEntry] = {}


def register(
    name: str,
    *,
    client_cls: type,
    ops_cls: type,
    poller_cls: type,
    auth_cls: type,
    available: bool = True,
    display_name: str = "",
    description: str = "",
) -> None:
    """Register a cloud storage provider."""
    _PROVIDERS[name] = ProviderEntry(
        name=name,
        client_cls=client_cls,
        ops_cls=ops_cls,
        poller_cls=poller_cls,
        auth_cls=auth_cls,
        available=available,
        display_name=display_name or name,
        description=description,
    )
    log.debug("Registered provider: %s (available=%s)", name, available)


def get(name: str) -> ProviderEntry:
    """Get a registered provider by name.

    Raises KeyError if not found.
    """
    if name not in _PROVIDERS:
        raise KeyError(
            f"Unknown provider: {name!r}. "
            f"Available: {', '.join(sorted(_PROVIDERS.keys()))}"
        )
    return _PROVIDERS[name]


def available_providers() -> list[ProviderEntry]:
    """Return all providers marked as available."""
    return [p for p in _PROVIDERS.values() if p.available]


def all_providers() -> list[ProviderEntry]:
    """Return all registered providers (including unavailable ones)."""
    return list(_PROVIDERS.values())
