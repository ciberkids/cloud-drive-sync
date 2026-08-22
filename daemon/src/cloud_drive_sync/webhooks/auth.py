"""Build the request headers for a target, including the HMAC body signature.

Secrets are read from the environment *here*, at request time, and never stored on a
resolved target. Two reasons: the resolver stays pure and testable without a fixture
environment, and a secret that only ever lives in a local variable cannot be
accidentally serialised into a config read, a status payload or a log line.

``jwt`` is deliberately absent. Minting is phase 3, and the resolver rejects the mode,
so nothing can reach here expecting it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from cloud_drive_sync.webhooks.models import WebhookAuth, WebhookSignature
from cloud_drive_sync.webhooks.resolver import ResolvedTarget


class MissingSecret(Exception):
    """A configured secret source produced nothing.

    Distinct from a delivery failure on purpose: the endpoint is fine, the deployment
    is misconfigured, and retrying cannot help. The message never contains the value --
    only the name of the variable that was empty.
    """


def _from_env(name: str, *, what: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise MissingSecret(
            f"environment variable {name!r} (the {what}) is unset or empty"
        )
    return value


def _resolve(literal: str, env_name: str, *, what: str) -> str:
    """A literal wins over an env reference; an env reference must resolve."""
    if literal:
        return literal
    if env_name:
        return _from_env(env_name, what=what)
    raise MissingSecret(f"no {what} configured")


def auth_headers(auth: WebhookAuth) -> dict[str, str]:
    """The authorization header(s) for one target, or ``{}`` for ``mode = "none"``."""
    if auth.mode == "none":
        return {}

    if auth.mode == "basic":
        password = _resolve(auth.password, auth.password_env, what="basic password")
        raw = f"{auth.username}:{password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}

    if auth.mode == "bearer":
        token = _resolve(auth.token, auth.token_env, what="bearer token")
        return {"Authorization": f"Bearer {token}"}

    if auth.mode == "custom":
        value = _resolve(auth.value, auth.value_env, what="custom header value")
        return {auth.header: value}

    # Unreachable: the resolver rejects unknown modes before delivery. Raising rather
    # than silently sending an unauthenticated request, which is the failure mode this
    # whole module is arranged to avoid.
    raise MissingSecret(f"unsupported auth mode {auth.mode!r}")


def signature_headers(
    signature: WebhookSignature, body: bytes, *, timestamp: int | None = None
) -> dict[str, str]:
    """HMAC over ``f"{timestamp}.{body}"``.

    The timestamp is inside the signed material, so a captured body cannot be replayed
    later under a fresh timestamp. The digest is computed over the **bytes actually
    sent** -- never a re-serialisation of the dict, which would differ in key order or
    whitespace and fail verification for reasons nobody could reproduce.
    """
    secret = _resolve(signature.secret, signature.secret_env, what="signing secret")
    ts = int(time.time()) if timestamp is None else timestamp
    algo = hashlib.sha512 if signature.algorithm == "sha512" else hashlib.sha256
    mac = hmac.new(secret.encode("utf-8"), f"{ts}.".encode() + body, algo)
    return {
        signature.timestamp_header: str(ts),
        signature.header: f"{signature.algorithm}={mac.hexdigest()}",
    }


def build_headers(target: ResolvedTarget, body: bytes) -> dict[str, str]:
    """Every header for one attempt: content type, target headers, auth, signature.

    Order matters only in that the signature is computed last, over the final body.
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "cloud-drive-sync",
        **target.headers,
    }
    headers.update(auth_headers(target.auth))
    if target.signature is not None:
        headers.update(signature_headers(target.signature, body))
    return headers


def secret_values(target: ResolvedTarget) -> list[str]:
    """Every literal secret this target could put on the wire.

    Used to seed the log scrubber. Env-sourced values are included by *reading* them,
    because the point is to catch them if they ever reach a log line -- a value that
    is only scrubbed when written literally in the config is not scrubbed at all for
    the deployments that do it properly.
    """
    values: list[str] = []
    auth = target.auth
    for literal, env_name in (
        (auth.password, auth.password_env),
        (auth.token, auth.token_env),
        (auth.value, auth.value_env),
    ):
        if literal:
            values.append(literal)
        elif env_name:
            from_env = os.environ.get(env_name, "")
            if from_env:
                values.append(from_env)
    if target.signature is not None:
        sig = target.signature
        if sig.secret:
            values.append(sig.secret)
        elif sig.secret_env:
            from_env = os.environ.get(sig.secret_env, "")
            if from_env:
                values.append(from_env)
    # A very short secret would scrub harmless substrings out of unrelated log lines.
    return [v for v in values if len(v) >= 8]
