"""Tests for the web UI sign-in primitives and the account row.

The gap this closes: the web UI's "login page" asked for the shared access token
and stored it verbatim in a cookie, so everyone who signed in signed in as the
same anonymous somebody. This adds one named account with a password.

The tests that matter most are the two ends: that the token keeps working exactly
as before (deployed systemd units, compose files, the Bruno collection and every
curl example depend on it), and that the expensive parts cannot be turned into a
denial of service by an unauthenticated caller.

See docs/Proposal-Web-UI-Login.md for the decisions behind the shape.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile

import pytest

from cloud_drive_sync.db.database import SCHEMA_VERSION, Database
from cloud_drive_sync.http import auth

PASSWORD = "correct-horse-battery"  # test-only
WEAK = "short"  # test-only


# ── Password hashing ────────────────────────────────────────────────


def test_a_password_round_trips():
    encoded = auth.hash_password(PASSWORD)
    assert auth.verify_password(encoded, PASSWORD) is True


def test_the_wrong_password_is_refused():
    """The negative control. Without it, a verify that always returns True passes."""
    encoded = auth.hash_password(PASSWORD)
    assert auth.verify_password(encoded, PASSWORD + "x") is False
    assert auth.verify_password(encoded, "") is False


def test_the_same_password_hashes_differently_every_time():
    """A per-password salt, so identical passwords do not look identical."""
    assert auth.hash_password(PASSWORD) != auth.hash_password(PASSWORD)


def test_the_hash_carries_its_parameters():
    """Self-describing, which is what makes raising the cost later possible."""
    encoded = auth.hash_password(PASSWORD)
    scheme, params, salt, digest = encoded.split("$")
    assert scheme == "scrypt"
    assert params == f"n={auth.SCRYPT_N},r={auth.SCRYPT_R},p={auth.SCRYPT_P}"
    assert salt and digest


def test_the_stored_hash_is_not_the_password():
    assert PASSWORD not in auth.hash_password(PASSWORD)


@pytest.mark.parametrize(
    "encoded",
    ["", "not-a-hash", "scrypt$n=1$x$y", "bcrypt$n=1,r=8,p=1$AAAA$AAAA", "scrypt$$$"],
    ids=["empty", "garbage", "missing-params", "wrong-scheme", "blank-fields"],
)
def test_a_malformed_hash_is_a_failed_verification_not_a_traceback(encoded):
    """A hand-edited or truncated value must not crash the sign-in path."""
    assert auth.verify_password(encoded, PASSWORD) is False


def test_absurd_stored_parameters_do_not_take_the_process_down():
    """An n that will not allocate is a rejected password, not a MemoryError."""
    assert auth.verify_password("scrypt$n=1073741824,r=64,p=64$AAAA$AAAA", PASSWORD) is False


def test_a_hash_at_the_current_cost_needs_no_rehash():
    assert auth.needs_rehash(auth.hash_password(PASSWORD)) is False


def test_a_cheaper_hash_is_flagged_for_rehash():
    """How a raised cost reaches existing accounts: on their next sign-in."""
    cheap = auth.hash_password(PASSWORD, n=1024)
    assert auth.verify_password(cheap, PASSWORD) is True, "old hashes must still verify"
    assert auth.needs_rehash(cheap) is True


def test_an_unreadable_hash_asks_to_be_replaced():
    assert auth.needs_rehash("garbage") is True


# ── What counts as a usable password ────────────────────────────────


def test_a_good_password_has_no_problem():
    assert auth.password_problem(PASSWORD, username="matteo") is None


def test_a_short_password_is_refused():
    assert "at least" in (auth.password_problem(WEAK) or "")


def test_an_absurdly_long_password_is_refused():
    """Unbounded input into a memory-hard KDF is the caller's DoS, not ours."""
    assert "at most" in (auth.password_problem("x" * 5000) or "")


def test_the_username_is_not_a_password():
    assert auth.password_problem("matteo-matteo", username="matteo-matteo") is not None


def test_the_access_token_is_not_a_password():
    """Otherwise you could "set a password" that is the credential you replaced."""
    token = auth.generate_token()
    assert auth.password_problem(token, token=token) is not None


@pytest.mark.parametrize(
    "name", ["", "   ", "a b", "a\tb", "with\nnewline", "x" * 65],
    ids=["empty", "spaces", "space", "tab", "newline", "too-long"],
)
def test_bad_usernames_are_refused(name):
    assert auth.username_problem(name) is not None


def test_a_normal_username_is_accepted():
    assert auth.username_problem("matteo") is None


# ── Sessions ────────────────────────────────────────────────────────


def test_a_session_resolves_to_its_user():
    store = auth.SessionStore()
    assert store.resolve(store.issue("matteo")) == "matteo"


def test_an_unknown_session_resolves_to_nothing():
    store = auth.SessionStore()
    store.issue("matteo")
    assert store.resolve("not-a-session") is None
    assert store.resolve(None) is None


def test_only_the_digest_is_kept():
    """A heap dump — or a future decision to persist this — is not credentials."""
    store = auth.SessionStore()
    session_id = store.issue("matteo")
    assert session_id not in str(store._sessions)
    assert auth.session_digest(session_id) in store._sessions


def test_signing_out_drops_the_session():
    store = auth.SessionStore()
    session_id = store.issue("matteo")
    store.drop(session_id)
    assert store.resolve(session_id) is None


def test_changing_the_password_drops_every_session():
    """A password change that leaves old sessions alive has changed nothing."""
    store = auth.SessionStore()
    ids = [store.issue("matteo") for _ in range(3)]
    store.drop_all()
    assert all(store.resolve(i) is None for i in ids)
    assert len(store) == 0


def test_a_session_expires_absolutely():
    now = [1000.0]
    store = auth.SessionStore(absolute_seconds=100, idle_seconds=100, clock=lambda: now[0])
    session_id = store.issue("matteo")
    now[0] += 99
    assert store.resolve(session_id) == "matteo"
    now[0] += 2
    assert store.resolve(session_id) is None


def test_the_idle_window_slides_but_the_absolute_one_does_not():
    """Activity extends the idle deadline; nothing extends the absolute one."""
    now = [1000.0]
    store = auth.SessionStore(absolute_seconds=100, idle_seconds=30, clock=lambda: now[0])
    session_id = store.issue("matteo")
    for _ in range(4):
        now[0] += 20
        assert store.resolve(session_id) == "matteo", "activity should keep it alive"
    now[0] += 25  # 105s total: past the absolute deadline despite being active
    assert store.resolve(session_id) is None


def test_an_idle_session_expires():
    now = [1000.0]
    store = auth.SessionStore(absolute_seconds=1000, idle_seconds=30, clock=lambda: now[0])
    session_id = store.issue("matteo")
    now[0] += 31
    assert store.resolve(session_id) is None


def test_expired_sessions_are_pruned_rather_than_accumulating():
    now = [1000.0]
    store = auth.SessionStore(absolute_seconds=10, idle_seconds=10, clock=lambda: now[0])
    for _ in range(5):
        store.issue("matteo")
    now[0] += 20
    store.issue("matteo")  # issuing prunes
    assert len(store) == 1


def test_session_ids_are_long_and_unique():
    store = auth.SessionStore()
    ids = {store.issue("matteo") for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) >= 32 for i in ids)


# ── The throttle ────────────────────────────────────────────────────


def test_the_first_attempts_are_not_delayed():
    """A user who mistypes once must not be punished for it."""
    throttle = auth.LoginThrottle()
    for _ in range(auth.THROTTLE_FREE_ATTEMPTS):
        assert throttle.delay_for("user:matteo") == 0.0
        throttle.record_failure("user:matteo")


def test_the_delay_grows_and_is_capped():
    throttle = auth.LoginThrottle()
    for _ in range(auth.THROTTLE_FREE_ATTEMPTS):
        throttle.record_failure("user:matteo")
    seen = []
    for _ in range(12):
        seen.append(throttle.delay_for("user:matteo"))
        throttle.record_failure("user:matteo")
    assert seen[0] == auth.THROTTLE_BASE_DELAY
    assert seen[1] > seen[0]
    assert max(seen) == auth.THROTTLE_MAX_DELAY, "unbounded delay is a self-inflicted outage"


def test_success_clears_the_delay():
    """No hard lockout: with one account a lockout is the outage an attacker wants."""
    throttle = auth.LoginThrottle()
    for _ in range(10):
        throttle.record_failure("user:matteo")
    assert throttle.delay_for("user:matteo") > 0
    throttle.record_success("user:matteo")
    assert throttle.delay_for("user:matteo") == 0.0


def test_the_window_expires_so_yesterdays_typos_are_forgiven():
    now = [0.0]
    throttle = auth.LoginThrottle(window_seconds=60, clock=lambda: now[0])
    for _ in range(10):
        throttle.record_failure("user:matteo")
    assert throttle.delay_for("user:matteo") > 0
    now[0] += 61
    assert throttle.delay_for("user:matteo") == 0.0


def test_the_worst_key_wins():
    """Keyed by username *and* address; the caller takes the larger delay."""
    throttle = auth.LoginThrottle()
    for _ in range(10):
        throttle.record_failure("addr:10.0.0.1")
    assert throttle.delay_for("user:quiet", "addr:10.0.0.1") > 0
    assert throttle.delay_for("user:quiet") == 0.0


def test_keys_are_independent():
    throttle = auth.LoginThrottle()
    for _ in range(10):
        throttle.record_failure("addr:10.0.0.1")
    assert throttle.delay_for("addr:10.0.0.2") == 0.0


# ── The concurrency cap ─────────────────────────────────────────────


def test_hashing_is_capped_and_reports_when_it_is_full():
    """Measured: ~34 ms and ~16 MB per operation, and scrypt releases the GIL.

    The cap is what makes the cost affordable on an unauthenticated endpoint —
    without it, concurrent attempts are an attacker-controlled multiple of 16 MB.
    """
    async def main():
        assert auth.waiting_for_slot() is False
        held = asyncio.Event()

        async def hog():
            async with auth._slots():
                held.set()
                await asyncio.sleep(0.05)

        tasks = [asyncio.create_task(hog()) for _ in range(auth.VERIFY_CONCURRENCY)]
        await held.wait()
        await asyncio.sleep(0)
        assert auth.waiting_for_slot() is True, "a full cap must be visible to the caller"
        await asyncio.gather(*tasks)
        assert auth.waiting_for_slot() is False

    asyncio.run(main())


def test_the_async_wrappers_agree_with_the_sync_ones():
    async def main():
        encoded = await auth.hash_password_async(PASSWORD)
        assert await auth.verify_password_async(encoded, PASSWORD) is True
        assert await auth.verify_password_async(encoded, "wrong") is False

    asyncio.run(main())


# ── Cookie flags ────────────────────────────────────────────────────


def test_plain_http_gets_no_secure_flag():
    """A Secure cookie on http://nas:8080 is accepted and never sent back."""
    assert auth.wants_secure_cookie(
        scheme="http", forwarded_proto=None, trust_proxy=False
    ) is False


def test_https_gets_the_secure_flag():
    assert auth.wants_secure_cookie(
        scheme="https", forwarded_proto=None, trust_proxy=False
    ) is True


def test_a_forwarded_header_is_ignored_unless_the_proxy_is_trusted():
    """Otherwise anyone who can reach the port can assert it."""
    assert auth.wants_secure_cookie(
        scheme="http", forwarded_proto="https", trust_proxy=False
    ) is False
    assert auth.wants_secure_cookie(
        scheme="http", forwarded_proto="https", trust_proxy=True
    ) is True


def test_a_trusted_proxy_reporting_http_still_gets_no_secure_flag():
    assert auth.wants_secure_cookie(
        scheme="http", forwarded_proto="http, https", trust_proxy=True
    ) is False


# ── CSRF helpers ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "content_type",
    [
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        "text/plain",
        "TEXT/PLAIN; charset=utf-8",
    ],
)
def test_form_content_types_are_recognised(content_type):
    """The three a plain HTML form can send — the no-JavaScript CSRF vector."""
    assert auth.is_form_content_type(content_type) is True


@pytest.mark.parametrize("content_type", ["application/json", None, "", "application/xml"])
def test_non_form_content_types_are_allowed(content_type):
    assert auth.is_form_content_type(content_type) is False


def test_a_matching_origin_is_same_origin():
    assert auth.origin_is_same("http://nas:8080", None, "nas:8080") is True


def test_a_foreign_origin_is_refused():
    assert auth.origin_is_same("http://evil.example", None, "nas:8080") is False


def test_the_referer_is_used_when_there_is_no_origin():
    assert auth.origin_is_same(None, "http://nas:8080/settings", "nas:8080") is True
    assert auth.origin_is_same(None, "http://evil.example/x", "nas:8080") is False


def test_neither_header_is_treated_as_same_origin():
    """Same-origin GET navigations legitimately send neither."""
    assert auth.origin_is_same(None, None, "nas:8080") is True


def test_no_host_is_never_same_origin():
    assert auth.origin_is_same("http://nas:8080", None, None) is False


# ── The account row ─────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def test_the_schema_version_advanced_for_the_account_table():
    assert SCHEMA_VERSION >= 6


def test_the_account_round_trips():
    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(pathlib.Path(tmp) / "t.db")
            await db.open()
            assert await db.get_web_user() is None, "no account by default"
            await db.set_web_user("matteo", auth.hash_password(PASSWORD))
            user = await db.get_web_user()
            assert user.username == "matteo"
            assert auth.verify_password(user.password, PASSWORD)
            await db.close()

    _run(main())


def test_only_one_account_can_exist():
    """The single-account decision lives in the schema, not in a convention."""
    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(pathlib.Path(tmp) / "t.db")
            await db.open()
            await db.set_web_user("matteo", auth.hash_password(PASSWORD))
            with pytest.raises(Exception):
                await db.db.execute(
                    "INSERT INTO web_user (id, username, password, created_at, "
                    "password_changed_at) VALUES (2, 'other', 'x', 'y', 'z')"
                )
            await db.close()

    _run(main())


def test_replacing_the_account_keeps_the_creation_date():
    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(pathlib.Path(tmp) / "t.db")
            await db.open()
            await db.set_web_user("matteo", auth.hash_password(PASSWORD))
            first = await db.get_web_user()
            await db.set_web_user("matteo", auth.hash_password(PASSWORD + "2"))
            second = await db.get_web_user()
            assert second.created_at == first.created_at
            assert second.password_changed_at >= first.password_changed_at
            cursor = await db.db.execute("SELECT COUNT(*) FROM web_user")
            assert (await cursor.fetchone())[0] == 1
            await db.close()

    _run(main())


def test_clearing_the_account_leaves_no_row():
    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(pathlib.Path(tmp) / "t.db")
            await db.open()
            await db.set_web_user("matteo", auth.hash_password(PASSWORD))
            await db.clear_web_user()
            assert await db.get_web_user() is None
            await db.close()

    _run(main())


def test_an_existing_database_gains_the_table_on_upgrade():
    """The v5 -> v6 path, which is what every current install will take."""
    async def main():
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "t.db"
            db = Database(path)
            await db.open()
            await db.db.execute("DROP TABLE web_user")
            await db.db.execute("UPDATE schema_version SET version = 5")
            await db.db.commit()
            await db.close()

            db = Database(path)
            await db.open()
            cursor = await db.db.execute("SELECT version FROM schema_version")
            assert (await cursor.fetchone())[0] == SCHEMA_VERSION
            await db.set_web_user("matteo", auth.hash_password(PASSWORD))
            assert (await db.get_web_user()).username == "matteo"
            await db.close()

    _run(main())


# ── The exposure warning ────────────────────────────────────────────


def test_an_account_counts_as_authentication(caplog):
    """A daemon whose UI asks for a password is not unprotected.

    Warning as though it were would train people to ignore the warning.
    """
    with caplog.at_level("INFO"):
        auth.warn_if_exposed(
            name="HTTP API", host="0.0.0.0", port=8080, token=None, account=True
        )

    assert "NO AUTHENTICATION" not in caplog.text
    assert "sign-in" in caplog.text


def test_neither_credential_still_warns(caplog):
    with caplog.at_level("WARNING"):
        auth.warn_if_exposed(
            name="HTTP API", host="0.0.0.0", port=8080, token=None, account=False
        )

    assert "NO AUTHENTICATION" in caplog.text
    assert "user set" in caplog.text, "the warning should say how to fix it"


# ── Non-ASCII credentials ───────────────────────────────────────────
#
# `secrets.compare_digest` accepts `str` for ASCII only and raises TypeError
# otherwise. Every comparison here therefore encodes to UTF-8 first. Without
# that, an account created with a name like "matteø" could never sign in — the
# TypeError surfaced as the same opaque "Sign-in failed." the design chose so it
# would not leak which half was wrong, making it a silent permanent lockout.


def test_a_non_ascii_username_is_allowed():
    """NIST 800-63B requires accepting all Unicode, and people have names."""
    assert auth.username_problem("matteø") is None
    assert auth.username_problem("张伟") is None


def test_a_non_ascii_password_is_checked_without_raising():
    encoded = auth.hash_password("pässwörd-läng")
    assert auth.verify_password(encoded, "pässwörd-läng") is True
    assert auth.verify_password(encoded, "pässwörd-lang") is False


def test_a_non_ascii_password_is_compared_against_the_token():
    """The comparison that used to raise. A verdict, not a TypeError."""
    assert auth.password_problem("pässwörd-läng", username="alice", token="tok") is None
    token = "tökén-välue-that-is-long"
    assert auth.password_problem(token, username="alice", token=token) is not None


def test_a_non_ascii_token_is_a_refusal_not_a_crash():
    """Anyone can type a non-ASCII character into the token field.

    This one predates the sign-in work: it has been reachable on the token path
    since v2.4.0, where it produced an unhandled exception instead of a 401.
    """
    assert auth.matches("ascii-token", "pässwörd") is False
    assert auth.is_authorised("ascii-token", authorization="Bearer pässwörd") is False
    assert auth.is_authorised("ascii-token", cookie="pässwörd") is False


def test_a_non_ascii_token_still_matches_itself():
    assert auth.matches("tökén-välue", "tökén-välue") is True
    assert auth.matches("tökén-välue", "tokén-välue") is False


# ── The real handler, not a fake ─────────────────────────────────────
#
# The endpoint tests in test_feature_web_login_http.py drive a fake handler, so
# they cannot catch a bug in the real one — a first attempt to pin the non-ASCII
# lockout there passed against both the fixed and the broken comparison. These go
# through RequestHandler itself.


class _FakeDB:
    """Just enough Database for the account methods."""

    def __init__(self) -> None:
        self.user = None

    async def get_web_user(self):
        return self.user

    async def set_web_user(self, username, password_hash):
        from datetime import UTC, datetime

        from cloud_drive_sync.db.models import WebUser

        created = self.user.created_at if self.user else datetime.now(UTC)
        self.user = WebUser(
            username=username,
            password=password_hash,
            created_at=created,
            password_changed_at=datetime.now(UTC),
        )

    async def clear_web_user(self):
        self.user = None


def _handler():
    from cloud_drive_sync.config import Config
    from cloud_drive_sync.ipc.handlers import RequestHandler

    handler = RequestHandler(None, Config())
    handler.set_db(_FakeDB())
    return handler


async def _call(handler, method, params=None):
    from cloud_drive_sync.ipc.protocol import JsonRpcRequest

    response = await handler.handle(JsonRpcRequest(id=1, method=method, params=params or {}))
    assert response.error is None, f"{method} failed: {response.error}"
    return response.result


def test_the_real_handler_signs_in_a_non_ascii_account():
    """The lockout, pinned where it actually lived.

    A username like this was accepted at creation and then compared with
    ``secrets.compare_digest`` on ``str`` — which raises for non-ASCII, surfacing
    as the same opaque "Sign-in failed." as a wrong password. Created fine, could
    never sign in, no diagnosable error anywhere.
    """
    async def main():
        handler = _handler()
        created = await _call(
            handler, "set_web_account", {"username": "matteø", "password": "pässwörd-läng"}
        )
        assert created["status"] == "ok"

        ok = await _call(
            handler,
            "verify_web_account",
            {"username": "matteø", "password": "pässwörd-läng"},
        )
        assert ok["ok"] is True, "the correct credentials were refused"

    asyncio.run(main())


def test_the_real_handler_still_refuses_a_wrong_non_ascii_password():
    async def main():
        handler = _handler()
        await _call(
            handler, "set_web_account", {"username": "matteø", "password": "pässwörd-läng"}
        )
        bad = await _call(
            handler, "verify_web_account", {"username": "matteø", "password": "pässwörd-lang"}
        )
        assert bad["ok"] is False

    asyncio.run(main())


def test_the_real_handler_reports_no_database_as_unavailable():
    """Distinct from "no account", so the HTTP layer can fail closed on it."""
    async def main():
        from cloud_drive_sync.config import Config
        from cloud_drive_sync.ipc.handlers import RequestHandler

        handler = RequestHandler(None, Config())  # no set_db
        state = await _call(handler, "get_web_account")
        assert state["exists"] is False
        assert state["available"] is False

        with_db = _handler()
        state = await _call(with_db, "get_web_account")
        assert state["available"] is True

    asyncio.run(main())


def test_the_real_handler_changes_a_non_ascii_password():
    async def main():
        handler = _handler()
        await _call(handler, "set_web_account", {"username": "matteø", "password": "pässwörd-läng"})
        result = await _call(
            handler,
            "change_web_password",
            {"current": "pässwörd-läng", "new": "nöuveau-mot-de-passe"},
        )
        assert result["status"] == "ok", result
        ok = await _call(
            handler,
            "verify_web_account",
            {"username": "matteø", "password": "nöuveau-mot-de-passe"},
        )
        assert ok["ok"] is True

    asyncio.run(main())
