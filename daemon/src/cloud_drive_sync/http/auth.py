"""Shared-token authentication for the HTTP and MCP front-ends.

Both ports were reachable by anyone who could connect. That was already true of
account management, but it became sharper once the delete fail-safe and emergency
stop landed: an unauthenticated caller could

* ``PUT /api/settings/max-deletions {"max_deletions_per_sync": 0}`` to switch off
  delete protection, then trigger a sync, or
* call ``emergency_stop`` repeatedly as a denial of service.

A safety guard an anonymous caller can disable is a weaker guarantee than the
documentation implies, so this closes it.

Deliberately **opt-in**. Enabling it by default would lock every existing
deployment out of its own web UI on upgrade — people bookmark
``http://nas:8080`` — so no token means the previous behaviour exactly, plus a
loud warning when the port is exposed beyond loopback. That is a compromise, and
the warning is what makes it an informed one rather than a silent one.

Kept free of aiohttp and Starlette imports so the same checks serve both
front-ends and stay testable on their own.
"""

from __future__ import annotations

import secrets

from cloud_drive_sync.util.logging import get_logger

log = get_logger("http.auth")

#: Cookie the web UI stores its token in. HttpOnly and SameSite=Strict, so a page
#: on another origin cannot read it or ride on it.
COOKIE_NAME = "cds_token"

BEARER_PREFIX = "Bearer "

#: Loopback addresses. Binding here means only this machine can connect, which is
#: why it is the one case where running without a token is unremarkable.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def is_loopback(host: str) -> bool:
    """Whether binding ``host`` restricts access to this machine."""
    return host.strip("[]") in LOOPBACK_HOSTS


def token_from_headers(authorization: str | None) -> str | None:
    """Extract a bearer token from an ``Authorization`` header value."""
    if not authorization or not authorization.startswith(BEARER_PREFIX):
        return None
    return authorization[len(BEARER_PREFIX):].strip() or None


def matches(expected: str, presented: str | None) -> bool:
    """Constant-time token comparison.

    ``compare_digest`` rather than ``==`` so response timing does not leak how much
    of the token was correct.
    """
    if not presented:
        return False
    return secrets.compare_digest(expected, presented)


def is_authorised(
    expected: str | None,
    *,
    authorization: str | None = None,
    cookie: str | None = None,
) -> bool:
    """Whether a request carrying these credentials may proceed.

    ``expected`` of ``None`` means authentication is disabled, so everything is
    allowed — the pre-existing behaviour.
    """
    if expected is None:
        return True
    return matches(expected, token_from_headers(authorization)) or matches(expected, cookie)


def warn_if_exposed(*, name: str, host: str, port: int, token: str | None) -> None:
    """Log a warning when a port is reachable off-box without a token.

    Loud on purpose. This is the case where the documented risk is real, and the
    only signal a user gets that their sync configuration — including the delete
    fail-safe — is anonymously writable.
    """
    if token is not None:
        log.info("%s on %s:%d requires a token", name, host, port)
        return
    if is_loopback(host):
        log.info(
            "%s on %s:%d has no token, but is bound to loopback so only this "
            "machine can reach it",
            name,
            host,
            port,
        )
        return
    log.warning(
        "%s on %s:%d has NO AUTHENTICATION and is reachable from the network. "
        "Anyone who can connect can add or remove cloud accounts, change where data "
        "syncs, and switch off delete protection. Set a token, or bind it to "
        "127.0.0.1 and reach it over an SSH tunnel.",
        name,
        host,
        port,
    )


def generate_token() -> str:
    """A token suitable for the CLI to suggest. 32 bytes, URL-safe."""
    return secrets.token_urlsafe(32)
