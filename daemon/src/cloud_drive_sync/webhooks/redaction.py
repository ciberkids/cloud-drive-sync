"""Keep webhook secrets out of logs, activity-log rows and error responses.

Call-site redaction is not enough, and it is worth being precise about why. Measured
against this project's own aiohttp:

    str(exc)  -> 500, message='Internal Server Error', url='http://h/?t=QUERYSECRET'
    repr(exc) -> ClientResponseError(RequestInfo(url=URL('...?t=QUERYSECRET'),
                 headers=<CIMultiDictProxy('Authorization': 'Bearer HEADERSECRET' ...

``str`` leaks the query string; ``repr`` leaks the ``Authorization`` header verbatim.
A token in a query parameter is a common receiver design, so both matter.

And three idioms already in this codebase put exception text somewhere a secret must
never go: ``detail=f"Sync failed: {exc}"`` into ``sync_log`` (which an MCP read tool
returns), ``log.debug("... (%s)", exc)`` in the Nextcloud push client, and
``JsonRpcResponse.fail(..., str(exc))`` which reaches every front-end. On top of that,
``setup_logging`` attaches its filters to the ``cloud_drive_sync`` logger only, so a
library or asyncio-level record bypasses anything installed there.

Hence two layers:

* :func:`describe_failure` -- the only thing delivery code is allowed to log. Built
  from allowlisted fields, never from the exception.
* :class:`SecretScrubber` -- a filter on every log **handler**, so library and
  asyncio-level records are covered too. A backstop, not the primary defence.
  Handlers, not loggers: a logger's filters run only for records emitted on that
  exact logger, so a root-*logger* filter would see none of our records at all.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

#: What replaces a redacted value. Distinctive enough to grep for in a bug report.
PLACEHOLDER = "[redacted]"

#: Any query string is dropped wholesale rather than matched key by key: guessing
#: which parameter names carry credentials is a losing game.
_QUERY_RE = re.compile(r"\?[^\s\"'>]+")

#: `scheme://user:pass@host` -- aiohttp turns userinfo into a Basic header, so it is
#: a credential even though it looks like part of an address.
_USERINFO_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+:[^/@\s]+@")

_AUTH_HEADER_RE = re.compile(
    r"(?i)('?(?:Authorization|Proxy-Authorization|X-API-Key|X-CDS-Signature)'?\s*[:=]\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^,\s)}\]]+)"
)


def safe_endpoint(url: str) -> str:
    """Scheme, host and port only. Never the path, never the query.

    The path can itself be a secret -- a Home Assistant webhook id or a Slack hook is
    the credential -- so only the authority survives.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return PLACEHOLDER
    if not parts.scheme or not parts.hostname:
        return PLACEHOLDER
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def scrub(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove known secrets and anything credential-shaped from ``text``."""
    if not text:
        return text
    out = text
    for secret in secrets:
        if secret and len(secret) >= 8:
            out = out.replace(secret, PLACEHOLDER)
    out = _USERINFO_RE.sub(lambda m: m.group("scheme") + PLACEHOLDER + "@", out)
    out = _AUTH_HEADER_RE.sub(lambda m: m.group(1) + PLACEHOLDER, out)
    out = _QUERY_RE.sub("?" + PLACEHOLDER, out)
    return out


def describe_failure(
    *,
    target_key: str,
    url: str,
    attempt: int,
    status: int | None = None,
    reason: str = "",
) -> str:
    """The only description of a delivery failure that may be logged or stored.

    Assembled from allowlisted fields. Note what is *not* a parameter: the exception.
    Delivery code catches at the boundary and passes a short reason string it chose
    itself, so no library object is ever handed to a formatter.
    """
    parts = [f"target {target_key}", safe_endpoint(url), f"attempt {attempt}"]
    if status is not None:
        parts.append(f"HTTP {status}")
    if reason:
        # Bounded: an unbounded reason invites someone to pass str(exc) one day.
        parts.append(scrub(reason)[:200])
    return " — ".join(parts)


def classify_error(exc: BaseException) -> str:
    """A short, safe reason for an exception, using only its *type*.

    Deliberately does not read ``str(exc)`` or ``repr(exc)``: for aiohttp those embed
    the URL query and the Authorization header. The type is enough to tell a timeout
    from a DNS failure from a TLS problem, which is what an operator needs.
    """
    name = type(exc).__name__
    mapping = {
        "TimeoutError": "timed out",
        "ClientConnectorError": "could not connect",
        "ClientConnectorSSLError": "TLS verification failed",
        "ClientConnectorCertificateError": "TLS certificate rejected",
        "ClientSSLError": "TLS error",
        "ServerDisconnectedError": "server disconnected",
        "ClientPayloadError": "malformed response body",
        "ClientOSError": "network error",
        "ClientResponseError": "error response",
        "InvalidURL": "invalid url",
    }
    return mapping.get(name, f"{name}")


class SecretScrubber(logging.Filter):
    """Backstop that scrubs records before a handler formats them.

    **Attached to handlers, never to a logger.** A logger's filters run only for
    records emitted on that exact logger, so a filter on the root *logger* sees
    nothing from ``cloud_drive_sync.webhooks.delivery`` -- verified, and the same
    lesson :class:`~cloud_drive_sync.util.logging.TruncatingFilter` already records.
    Handler filters do see propagated records, which is what makes this work for
    library and asyncio-level output.

    Mutates ``record.msg`` and ``record.args`` -- the last point at which the text is
    still assembled from parts, so one filter per handler covers every format.
    """

    def __init__(self) -> None:
        super().__init__()
        self._secrets: set[str] = set()

    def add_secrets(self, values: list[str]) -> None:
        self._secrets.update(v for v in values if v and len(v) >= 8)

    def clear_secrets(self) -> None:
        self._secrets.clear()

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = tuple(self._secrets)
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg, secrets)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: scrub(v, secrets) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    # `%s` of an exception is where the aiohttp leak lands, so an
                    # exception argument is replaced by its safe classification
                    # rather than scrubbed after the fact.
                    classify_error(a) if isinstance(a, BaseException)
                    else scrub(a, secrets) if isinstance(a, str)
                    else a
                    for a in record.args
                )
        # `exc_info` is left alone: a traceback's value is its frames, and the message
        # line has already been scrubbed. Delivery code never logs with exc_info.
        return True


#: Process-wide: one instance shared by every handler, so secrets registered once
#: are scrubbed everywhere.
_scrubber: SecretScrubber | None = None


def install_scrubber() -> SecretScrubber:
    """Attach the scrubber to every current handler that could carry a secret.

    Idempotent, and safe to call again after logging is reconfigured -- which it must
    be, because ``setup_logging`` clears and rebuilds the ``cloud_drive_sync``
    handlers and would otherwise drop the filter.

    Both handler sets are needed: the ``cloud_drive_sync`` logger's own handlers for
    our records, and the root's for library and asyncio-level ones (an unhandled
    exception inside a delivery task is logged by asyncio, not by us).
    """
    global _scrubber
    if _scrubber is None:
        _scrubber = SecretScrubber()
    for logger in (logging.getLogger(), logging.getLogger("cloud_drive_sync")):
        for handler in logger.handlers:
            if _scrubber not in handler.filters:
                handler.addFilter(_scrubber)
    return _scrubber


def scrubber() -> SecretScrubber | None:
    return _scrubber
