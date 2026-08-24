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

import asyncio
import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

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

    Compared as **UTF-8 bytes**, not as ``str``. ``compare_digest`` accepts str
    only for ASCII and raises ``TypeError`` otherwise — so before this, anyone who
    pasted a non-ASCII character into the token field got an unhandled exception
    rather than "that token was not accepted". Encoding cannot change any verdict:
    UTF-8 is injective, so equal bytes mean equal strings.
    """
    if not presented:
        return False
    return secrets.compare_digest(expected.encode("utf-8"), presented.encode("utf-8"))


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


def warn_if_exposed(
    *, name: str, host: str, port: int, token: str | None, account: bool = False
) -> None:
    """Log a warning when a port is reachable off-box without a credential.

    Loud on purpose. This is the case where the documented risk is real, and the
    only signal a user gets that their sync configuration — including the delete
    fail-safe — is anonymously writable.

    ``account`` counts as authentication in its own right: a daemon whose web UI
    asks for a username and password is not unprotected just because no token is
    set, and warning as though it were would train people to ignore the warning.
    """
    if token is not None or account:
        log.info(
            "%s on %s:%d requires %s",
            name,
            host,
            port,
            "a token or a sign-in" if token is not None and account
            else "a sign-in" if account
            else "a token",
        )
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
        "syncs, and switch off delete protection. Set a token, create a web UI "
        "account ('cloud-drive-sync user set <name>'), or bind it to 127.0.0.1 "
        "and reach it over an SSH tunnel.",
        name,
        host,
        port,
    )


def generate_token() -> str:
    """A token suitable for the CLI to suggest. 32 bytes, URL-safe."""
    return secrets.token_urlsafe(32)


# ─────────────────────────────────────────────────────────────────────────────
# Web UI sign-in: one account, a password, and a session cookie.
#
# The token above stays exactly what it was — the *machine* credential for
# /api/* — and everything below is the *human* credential for the browser. They
# are layered, not alternatives: replacing the token would break CDS_HTTP_TOKEN
# in deployed systemd units and compose files, the Bruno collection (which sets
# no token at all), the MCP front-end and every curl example in the docs.
#
# See docs/Proposal-Web-UI-Login.md for the decisions and the rejected options.
# ─────────────────────────────────────────────────────────────────────────────

#: Cookie the browser carries a *session* in. Deliberately a different name from
#: COOKIE_NAME above, and that is what keeps the two credentials from being
#: interchangeable: a session id presented as ``Authorization: Bearer`` is
#: compared against the token and fails, and the token presented in this cookie
#: is looked up in the session store and is not there. No signature juggling
#: required — the names do the work.
SESSION_COOKIE = "cds_session"

#: scrypt cost. 2**14 (≈16 MB per verification) rather than 2**15, because
#: ``POST /api/auth/login`` is unauthenticated: every attempt is an
#: attacker-controlled allocation on hardware that might be a 512 MB NAS. The
#: cost is only safe in combination with VERIFY_CONCURRENCY below and the
#: throttle further down — raising one without the others is how a login form
#: becomes a memory-amplification DoS.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

#: How many password verifications may run at once. Anything beyond this waits,
#: and the caller is expected to refuse rather than queue without bound.
VERIFY_CONCURRENCY = 4

#: NIST 800-63B rather than folklore: length is the only rule that helps, and
#: composition rules push people towards predictable substitutions.
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024

#: Session lifetimes. Absolute expiry bounds a stolen cookie; idle expiry
#: reclaims an abandoned one.
SESSION_ABSOLUTE_SECONDS = 30 * 24 * 60 * 60
SESSION_IDLE_SECONDS = 7 * 24 * 60 * 60

#: Sign-in throttle. Five free attempts, then an exponential delay — no hard
#: lockout, because with a single account a lockout *is* the outage an attacker
#: wants, and it is trivially triggered by anyone who knows the username.
THROTTLE_FREE_ATTEMPTS = 5
THROTTLE_BASE_DELAY = 1.0
THROTTLE_MAX_DELAY = 30.0
THROTTLE_WINDOW_SECONDS = 15 * 60


def hash_password(
    password: str,
    *,
    n: int = SCRYPT_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> str:
    """Encode ``password`` as ``scrypt$n=…,r=…,p=…$salt$hash``.

    The parameters travel *inside* the value so the cost can be raised later
    without a migration: :func:`needs_rehash` spots an old encoding and the next
    successful sign-in replaces it. A bare hash with the parameters implied by
    whatever the code happens to define today cannot be upgraded at all.
    """
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=SCRYPT_DKLEN
    )
    return (
        f"scrypt$n={n},r={r},p={p}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"
    )


def _decode_hash(encoded: str) -> tuple[dict[str, int], bytes, bytes] | None:
    """Split an encoded hash, or ``None`` if it is not one we can read."""
    try:
        scheme, params, salt_b64, digest_b64 = encoded.split("$")
        if scheme != "scrypt":
            return None
        values = {}
        for part in params.split(","):
            key, _, raw = part.partition("=")
            values[key] = int(raw)
        if not {"n", "r", "p"} <= values.keys():
            return None
        return values, base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except Exception:
        # A truncated or hand-edited value is a failed verification, never a
        # traceback on the login path.
        return None


def verify_password(encoded: str, presented: str) -> bool:
    """Whether ``presented`` matches the encoded hash. Constant-time compare."""
    decoded = _decode_hash(encoded)
    if decoded is None:
        return False
    params, salt, expected = decoded
    try:
        candidate = hashlib.scrypt(
            presented.encode("utf-8"),
            salt=salt,
            n=params["n"],
            r=params["r"],
            p=params["p"],
            dklen=len(expected),
        )
    except (ValueError, OverflowError, MemoryError):
        # Absurd stored parameters must not take the process down: an n that
        # will not allocate is a rejected password, not a crash.
        return False
    return secrets.compare_digest(candidate, expected)


def needs_rehash(encoded: str) -> bool:
    """Whether this hash was made with parameters we no longer use."""
    decoded = _decode_hash(encoded)
    if decoded is None:
        return True
    params, _, digest = decoded
    return (
        params["n"] != SCRYPT_N
        or params["r"] != SCRYPT_R
        or params["p"] != SCRYPT_P
        or len(digest) != SCRYPT_DKLEN
    )


def password_problem(
    password: str, *, username: str = "", token: str | None = None
) -> str | None:
    """A human-readable reason to refuse this password, or ``None`` if it is fine.

    Deliberately short. The two rejections beyond length are the ones that make a
    password *not a password*: the username, and the access token — which would
    otherwise let someone "set a password" that is the credential they were
    trying to stop using.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    if username and password.strip().lower() == username.strip().lower():
        return "Password must not be the username."
    if token and secrets.compare_digest(password.encode("utf-8"), token.encode("utf-8")):
        return "Password must not be the access token."
    return None


def username_problem(username: str) -> str | None:
    """A reason to refuse this username, or ``None``.

    No character class rules — this is a label, not a shell argument — but it is
    stripped, bounded, and must not be blank or contain control characters that
    would corrupt a log line. Non-ASCII is deliberately allowed: NIST 800-63B,
    which the password rules follow, requires accepting all Unicode, and a sync
    client for people with names is the wrong place to invent an exception.

    That permissiveness is exactly why every comparison of these values encodes to
    bytes first — see :func:`matches`.
    """
    name = username.strip()
    if not name:
        return "Username is required."
    if len(name) > 64:
        return "Username must be at most 64 characters."
    if any(ch.isspace() or ord(ch) < 0x20 for ch in name):
        return "Username must not contain spaces or control characters."
    return None


def new_session_id() -> str:
    """A session id. 32 bytes, URL-safe, unguessable."""
    return secrets.token_urlsafe(32)


def session_digest(session_id: str) -> str:
    """The stored form of a session id.

    Only the digest is kept, so a heap dump — or a future decision to persist the
    store — is not a set of usable credentials.
    """
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


@dataclass
class _Session:
    username: str
    issued_at: float
    last_seen: float


class SessionStore:
    """In-memory sessions for the web UI.

    In memory on purpose (see the proposal): the alternative was a second table
    and a migration to hold state that is disposable by definition. The cost is
    that **restarting the daemon signs you out**, which for a self-hosted daemon
    that restarts on upgrades is a login page, not a hardship.

    ``clock`` is injectable so expiry is tested by moving time rather than by
    sleeping.
    """

    def __init__(
        self,
        *,
        absolute_seconds: int = SESSION_ABSOLUTE_SECONDS,
        idle_seconds: int = SESSION_IDLE_SECONDS,
        clock=time.time,
    ) -> None:
        self._sessions: dict[str, _Session] = {}
        self._absolute = absolute_seconds
        self._idle = idle_seconds
        self._clock = clock

    def issue(self, username: str) -> str:
        now = self._clock()
        session_id = new_session_id()
        self._sessions[session_digest(session_id)] = _Session(username, now, now)
        self.prune()
        return session_id

    def resolve(self, session_id: str | None) -> str | None:
        """The username this session belongs to, or ``None``.

        Touches ``last_seen`` on success, which is what makes the idle window a
        sliding one rather than a second absolute deadline.
        """
        if not session_id:
            return None
        record = self._sessions.get(session_digest(session_id))
        if record is None:
            return None
        now = self._clock()
        if self._expired(record, now):
            self._sessions.pop(session_digest(session_id), None)
            return None
        record.last_seen = now
        return record.username

    def drop(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_digest(session_id), None)

    def drop_all(self) -> None:
        """Used by a password change: the old password must stop granting access."""
        self._sessions.clear()

    def prune(self) -> int:
        now = self._clock()
        stale = [k for k, v in self._sessions.items() if self._expired(v, now)]
        for key in stale:
            del self._sessions[key]
        return len(stale)

    def _expired(self, record: _Session, now: float) -> bool:
        return (
            now - record.issued_at >= self._absolute
            or now - record.last_seen >= self._idle
        )

    def __len__(self) -> int:
        return len(self._sessions)


class LoginThrottle:
    """Exponential delay after repeated failures, keyed by whatever you pass.

    The HTTP layer keys it by *both* username and source address and takes the
    larger delay: with one account, a per-username limiter alone is a global
    limiter, and a per-address one alone is defeated by any botnet.

    Delay rather than lockout — see the module comment. The delay is also what
    makes the scrypt cost affordable, so this class is part of the DoS defence
    and not only the guessing defence.
    """

    def __init__(
        self,
        *,
        free_attempts: int = THROTTLE_FREE_ATTEMPTS,
        base_delay: float = THROTTLE_BASE_DELAY,
        max_delay: float = THROTTLE_MAX_DELAY,
        window_seconds: float = THROTTLE_WINDOW_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self._failures: dict[str, tuple[int, float]] = {}
        self._free = free_attempts
        self._base = base_delay
        self._max = max_delay
        self._window = window_seconds
        self._clock = clock

    def delay_for(self, *keys: str) -> float:
        """How long to wait before answering an attempt on these keys."""
        return max((self._delay(key) for key in keys if key), default=0.0)

    def record_failure(self, *keys: str) -> None:
        now = self._clock()
        for key in keys:
            if not key:
                continue
            count, first = self._failures.get(key, (0, now))
            if now - first >= self._window:
                count, first = 0, now
            self._failures[key] = (count + 1, first)

    def record_success(self, *keys: str) -> None:
        for key in keys:
            self._failures.pop(key, None)

    def _delay(self, key: str) -> float:
        entry = self._failures.get(key)
        if entry is None:
            return 0.0
        count, first = entry
        if self._clock() - first >= self._window:
            del self._failures[key]
            return 0.0
        over = count - self._free
        if over < 0:
            return 0.0
        return min(self._base * (2**over), self._max)


def wants_secure_cookie(
    *, scheme: str, forwarded_proto: str | None, trust_proxy: bool
) -> bool:
    """Whether the session cookie should carry ``Secure``.

    Not unconditional, and that is the whole point. The daemon serves plain HTTP,
    so a ``Secure`` cookie on ``http://nas:8080`` is accepted by the browser and
    never sent back — which presents as "sign-in succeeds, then asks again", the
    least debuggable failure this feature could ship.

    ``X-Forwarded-Proto`` is honoured only when the operator has said a proxy is
    in front, because otherwise anyone who can reach the port can assert it.
    """
    if scheme == "https":
        return True
    if trust_proxy and forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower() == "https"
    return False


#: Content types a browser can produce from a plain HTML form. A mutating /api
#: call that arrives as one of these is a CSRF attempt, because the real UI sends
#: JSON — so this is the layer that survives even if SameSite is ever weakened.
FORM_CONTENT_TYPES = (
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
)


def is_form_content_type(content_type: str | None) -> bool:
    """Whether this ``Content-Type`` is one an HTML form could have sent."""
    if not content_type:
        return False
    base = content_type.split(";")[0].strip().lower()
    return base in FORM_CONTENT_TYPES


def origin_is_same(origin: str | None, referer: str | None, host: str | None) -> bool:
    """Whether the request came from the page we serve.

    Only consulted when a *cookie* is the credential; bearer-token callers send
    neither header, so scripts and the Bruno collection are unaffected. Absent
    ``Origin`` and ``Referer`` with a cookie present is treated as same-origin,
    because same-origin GET navigations legitimately omit both.
    """
    if not host:
        return False
    candidate = origin or referer
    if not candidate:
        return True
    parsed = urlsplit(candidate)
    if not parsed.netloc:
        return False
    return parsed.netloc.lower() == host.lower()


# scrypt at these parameters measures ~34 ms and ~16 MB per operation on a
# desktop CPU, and — measured, not assumed — it *releases the GIL*, so running it
# in a worker thread buys real parallelism instead of just moving the block.
# Four sequential verifications took 135 ms; four threaded took 43 ms.
#
# Both matter for the same reason: 34 ms of CPU inside the event loop would stall
# every other request, including the sync engine's own progress reporting, and an
# unauthenticated endpoint is exactly where an attacker would aim that.
_verify_slots: asyncio.Semaphore | None = None


def _slots() -> asyncio.Semaphore:
    """The verification semaphore, created on first use inside the running loop."""
    global _verify_slots
    if _verify_slots is None:
        _verify_slots = asyncio.Semaphore(VERIFY_CONCURRENCY)
    return _verify_slots


def waiting_for_slot() -> bool:
    """Whether every verification slot is busy right now.

    The caller uses this to refuse rather than queue without bound: under a flood,
    ``503`` is a better answer than an ever-growing backlog of 16 MB allocations.
    """
    return _slots().locked()


async def hash_password_async(password: str) -> str:
    async with _slots():
        return await asyncio.to_thread(hash_password, password)


async def verify_password_async(encoded: str, presented: str) -> bool:
    async with _slots():
        return await asyncio.to_thread(verify_password, encoded, presented)
