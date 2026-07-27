"""Runtime corrections for nc-py-api bugs that make the client unsafe for servers.

nc-py-api builds every PROPFIND body from module-level property lists that it
extends *in place*, so the requested-property list grows on every call::

    def get_propfind_properties(capabilities: dict) -> list:
        r = PROPFIND_PROPERTIES                 # no copy
        if not check_capabilities("files.locking", capabilities):
            r += PROPFIND_LOCKING_PROPERTIES    # mutates PROPFIND_PROPERTIES
        return r

Every ``listdir()`` therefore appends the seven ``nc:lock-*`` properties
permanently. After a few thousand operations the PROPFIND body reaches tens of
megabytes, and the server's cost is ``O(properties x resources)`` — so a single
client pins every PHP-FPM worker at 100% CPU and the Nextcloud web UI stops
answering (issue #47).

Upstream still carries the bug on ``main`` as of 0.30.2 and ``pyproject.toml``
pins only ``nc-py-api>=0.17.0``, so the correction is applied here at import
time. Everything below degrades to a no-op if a future release fixes it.
"""

from __future__ import annotations

import functools
import importlib
from typing import Any

from cloud_drive_sync.util.logging import get_logger

log = get_logger("providers.nextcloud.patch")

# nc-py-api only ever defines ~27 distinct properties, so a PROPFIND asking for
# more than this is a bug rather than a request. The server pays
# O(properties x resources) for it, so refuse to send it at all.
MAX_PROPFIND_PROPERTIES = 128

_applied = False


class PropfindPropertyOverflow(RuntimeError):
    """A PROPFIND requested an implausible number of properties."""


def _dedup(properties: list[str]) -> list[str]:
    """Return ``properties`` without duplicates, preserving first-seen order."""
    return list(dict.fromkeys(properties))


def _sanitize_properties(properties: list[str], path: str) -> list[str]:
    """Drop duplicate properties and refuse implausibly large property sets.

    Duplicates always mean a shared property list is being extended in place,
    so log at error level: it signals that the correction below has stopped
    covering some upstream code path.
    """
    unique = _dedup(properties)
    if len(unique) != len(properties):
        log.error(
            "PROPFIND for %s requested %d properties but only %d are distinct — a "
            "shared property list is being mutated in place (see issue #47)",
            path or "/",
            len(properties),
            len(unique),
        )
    if len(unique) > MAX_PROPFIND_PROPERTIES:
        raise PropfindPropertyOverflow(
            f"refusing PROPFIND for {path or '/'}: {len(unique)} distinct properties "
            f"exceeds the {MAX_PROPFIND_PROPERTIES} limit"
        )
    return unique


def _get_propfind_properties(capabilities: dict) -> list[str]:
    """Non-mutating replacement for nc-py-api's ``get_propfind_properties``.

    Semantics are identical; the only change is that the base list is copied
    before the locking properties are appended.
    """
    from nc_py_api._misc import check_capabilities
    from nc_py_api.files import _files

    props = list(_files.PROPFIND_PROPERTIES)
    if not check_capabilities("files.locking", capabilities):
        props += _files.PROPFIND_LOCKING_PROPERTIES
    return props


def _sanitize_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Replace the ``properties`` argument of a ``_listdir`` call with a sanitized copy.

    ``args`` excludes ``self``, so the parameter order is
    ``(user, path, properties, depth, exclude_self, prop_type)``. nc-py-api
    passes ``properties`` as a keyword, our own change poller passes it
    positionally, so both forms are handled.
    """
    path = kwargs.get("path", args[1] if len(args) > 1 else "")
    if "properties" in kwargs:
        kwargs = {**kwargs, "properties": _sanitize_properties(kwargs["properties"], path)}
    elif len(args) > 2:
        args = (*args[:2], _sanitize_properties(args[2], path), *args[3:])
    return args, kwargs


def _guard_listdir(files_api: type) -> None:
    """Sanitize the property list of every PROPFIND the sync files API issues."""
    original = files_api._listdir
    if getattr(original, "_cds_guarded", False):
        return

    @functools.wraps(original)
    def _listdir(self: Any, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = _sanitize_call(args, kwargs)
        return original(self, *args, **kwargs)

    _listdir._cds_guarded = True  # type: ignore[attr-defined]
    files_api._listdir = _listdir


def _guard_async_listdir(files_api: type) -> None:
    """Sanitize the property list of every PROPFIND the async files API issues."""
    original = files_api._listdir
    if getattr(original, "_cds_guarded", False):
        return

    @functools.wraps(original)
    async def _listdir(self: Any, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = _sanitize_call(args, kwargs)
        return await original(self, *args, **kwargs)

    _listdir._cds_guarded = True  # type: ignore[attr-defined]
    files_api._listdir = _listdir


def apply() -> None:
    """Correct the nc-py-api PROPFIND property handling. Idempotent.

    ``pyproject.toml`` pins ``nc-py-api>=0.17.0`` with no upper bound, and this
    reaches into private upstream shape, so every step degrades independently:
    a rename in some future release must cost us that one step, never the whole
    Nextcloud provider.
    """
    global _applied
    if _applied:
        return

    modules = {}
    for name in ("_files", "files", "files_async"):
        try:
            modules[name] = importlib.import_module(f"nc_py_api.files.{name}")
        except Exception:  # nc-py-api absent, or this module no longer exists
            log.debug("nc_py_api.files.%s unavailable; skipping its PROPFIND patch", name)

    _files = modules.get("_files")
    if _files is None:
        # Without the constants there is nothing to correct and nothing to guard.
        return

    # Heal any accumulation that happened before this ran. Every module aliases
    # the *same* list object, so mutate it in place — rebinding the name would
    # only fix one namespace.
    base = getattr(_files, "PROPFIND_PROPERTIES", None)
    if isinstance(base, list):
        deduped = _dedup(base)
        if len(deduped) != len(base):
            log.warning(
                "Removed %d duplicate PROPFIND properties accumulated before patching",
                len(base) - len(deduped),
            )
            base[:] = deduped

    # ``files`` and ``files_async`` each did ``from ._files import
    # get_propfind_properties``, so they hold their own name bindings. Patching
    # ``_files`` alone would leave every listdir() call on the buggy version,
    # and patching only the two public modules would miss find()/by_id(), which
    # resolve the name in ``_files``' own globals.
    for module in modules.values():
        if hasattr(module, "get_propfind_properties"):
            module.get_propfind_properties = _get_propfind_properties

    for name, class_name, guard in (
        ("files", "FilesAPI", _guard_listdir),
        ("files_async", "AsyncFilesAPI", _guard_async_listdir),
    ):
        module = modules.get(name)
        if module is None:
            continue
        try:
            guard(getattr(module, class_name))
        except AttributeError as exc:
            log.warning(
                "Could not guard %s.%s._listdir (%s) — the property-list fix is still "
                "active, but oversized PROPFIND bodies will not be refused locally",
                module.__name__,
                class_name,
                exc,
            )

    _applied = True
