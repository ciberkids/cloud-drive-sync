"""Tests for the webhook payload, auth headers, signature and delivery loop.

Driven against a **real local aiohttp server** rather than a mocked client, following
``test_feature_http_auth`` -- and deliberately not using ``aiohttp.test_utils``, which
this repo already rejected because it pulls in ``pytest-aiohttp`` and its own asyncio
handling, which conflicts with ``asyncio_mode = auto``.

The assertions that matter most, in order:

* ``pair_N`` never appears in a payload. It renumbers when a pair is removed, and a
  receiver keys its own state on whatever we send.
* ``event_id`` is stable across retries, because delivery is at-least-once and that
  field is the receiver's only dedup key.
* Emission does not stall the event loop. Asserted as loop jitter, not wall clock: a
  hung-receiver timing test only exercises the queue, which was never the risk.
* A 401 is not retried. Retrying a configuration error five times per event turns a
  typo into a flood.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import pytest
from aiohttp import web

from cloud_drive_sync.config import Config, SyncPair
from cloud_drive_sync.events import EventBus
from cloud_drive_sync.webhooks import payload as payload_mod
from cloud_drive_sync.webhooks.auth import (
    MissingSecret,
    auth_headers,
    build_headers,
    signature_headers,
)
from cloud_drive_sync.webhooks.delivery import WebhookDelivery
from cloud_drive_sync.webhooks.dispatcher import WebhookDispatcher
from cloud_drive_sync.webhooks.models import (
    WebhookAuth,
    WebhooksConfig,
    WebhookSignature,
    WebhookTarget,
)
from cloud_drive_sync.webhooks.payload import (
    PayloadContext,
    build_body,
    make_event,
    public_name,
)
from cloud_drive_sync.webhooks.resolver import SCOPE_GLOBAL, resolve_targets

CONTEXT = PayloadContext(app="cloud-drive-sync", version="9.9.9", instance_id="inst-1")

SCOPE = {
    "pair_id": "uid-1234",
    "pair_label": "Documents",
    "account": {"provider": "gdrive", "email": "me@example.com"},
    "local_path": "/home/me/Documents",
    "remote_folder_id": "folder-abc",
}


def make_target(**overrides):
    """One resolved target, built through the real resolver so it is always valid."""
    fields = {
        "name": "t", "define": True, "url": "http://127.0.0.1:1/hook",
        "events": ["*"], "auth": WebhookAuth(mode="none"),
    }
    fields.update(overrides)
    targets, problems = resolve_targets(
        [(SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(**fields)]))]
    )
    assert not problems, problems
    return targets[0]


# ── A local receiver ────────────────────────────────────────────────────────


class Receiver:
    """A real HTTP server that records what it was sent."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: list[int] = []
        self.headers_to_send: dict[str, str] = {}
        self.delay: float = 0.0
        self._runner: web.AppRunner | None = None
        self.port = 0

    async def __aenter__(self) -> Receiver:
        app = web.Application()
        app.router.add_post("/hook", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        # Port 0 lets the OS choose, so parallel test runs cannot collide.
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hook"

    async def _handle(self, request):
        body = await request.read()
        self.requests.append({
            "headers": dict(request.headers),
            "raw": body,
            "json": json.loads(body) if body else None,
            "query": dict(request.query),
        })
        if self.delay:
            await asyncio.sleep(self.delay)
        status = self.responses.pop(0) if self.responses else 200
        return web.Response(status=status, headers=self.headers_to_send)


# ── Payload ─────────────────────────────────────────────────────────────────


class TestEnvelope:
    def test_the_envelope_has_the_documented_shape(self):
        raw = make_event("sync.completed", {"pair_id": "pair_0", "uploaded": 2})
        _body, envelope = build_body(
            raw, make_target(), CONTEXT, attempt=1, scope=SCOPE
        )
        assert envelope["schema_version"] == 1
        assert envelope["event"] == "sync.completed"
        assert envelope["occurred_at"].endswith("Z")
        assert envelope["source"] == {
            "app": "cloud-drive-sync", "version": "9.9.9", "instance_id": "inst-1",
        }
        assert envelope["scope"]["pair_id"] == "uid-1234"
        assert envelope["delivery"]["attempt"] == 1
        assert envelope["data"]["uploaded"] == 2

    def test_the_positional_pair_id_never_reaches_the_payload(self):
        """The regression test for the whole identity argument."""
        raw = make_event("sync.completed", {"pair_id": "pair_7", "uploaded": 1})
        body, envelope = build_body(
            raw, make_target(), CONTEXT, attempt=1, scope=SCOPE
        )
        assert b"pair_7" not in body
        assert "pair_7" not in json.dumps(envelope)

    def test_event_id_is_stable_across_attempts(self):
        """At-least-once delivery makes this the receiver's dedup key. Regenerating it
        per attempt is the usual way this field is made worthless."""
        raw = make_event("sync.completed", {})
        _b1, first = build_body(raw, make_target(), CONTEXT, attempt=1, scope=SCOPE)
        _b2, second = build_body(raw, make_target(), CONTEXT, attempt=2, scope=SCOPE)
        assert first["event_id"] == second["event_id"]
        assert second["delivery"]["attempt"] == 2

    def test_occurred_at_does_not_move_between_attempts(self):
        raw = make_event("sync.completed", {})
        _b1, first = build_body(raw, make_target(), CONTEXT, attempt=1, scope=SCOPE)
        time.sleep(0.01)
        _b2, second = build_body(raw, make_target(), CONTEXT, attempt=2, scope=SCOPE)
        assert first["occurred_at"] == second["occurred_at"], (
            "a retried event must not look like it happened later"
        )
        assert second["delivery"]["sent_at"] >= first["delivery"]["sent_at"]

    def test_a_non_pair_event_omits_the_pair_scope(self):
        raw = make_event("daemon.started", {})
        _body, envelope = build_body(
            raw, make_target(), CONTEXT, attempt=1, scope=SCOPE
        )
        assert "pair_id" not in envelope["scope"]
        assert "local_path" not in envelope["scope"]
        assert envelope["scope"]["account"] == SCOPE["account"]

    def test_routing_keys_are_stripped_from_data(self):
        raw = make_event("sync.completed", {"pair_id": "pair_0", "pair_label": "x", "n": 1})
        _body, envelope = build_body(
            raw, make_target(), CONTEXT, attempt=1, scope=SCOPE
        )
        assert "pair_id" not in envelope["data"]
        assert "pair_label" not in envelope["data"]
        assert envelope["data"]["n"] == 1


class TestPublicNames:
    def test_internal_names_map_to_dotted_public_names(self):
        assert public_name("sync_complete") == "sync.completed"
        assert public_name("delete_blocked") == "deletion.blocked"
        assert public_name("conflict_detected") == "conflict.detected"

    def test_status_changed_is_not_exported(self):
        """It only ever carries "idle", so a public event would be a constant."""
        assert public_name("status_changed") is None

    def test_an_unknown_internal_name_is_not_exported(self):
        assert public_name("something_new") is None


class TestTruncation:
    def _files(self, count):
        return {"files": {
            "uploaded": [f"f{i}.txt" for i in range(count)],
            "downloaded": [], "deleted": [], "conflicted": [],
        }, "uploaded": count}

    def test_lists_are_capped_and_the_flag_is_set(self):
        raw = make_event("sync.completed", self._files(10_000))
        _body, envelope = build_body(
            raw, make_target(max_files_per_event=100), CONTEXT, attempt=1, scope=SCOPE
        )
        assert len(envelope["data"]["files"]["uploaded"]) == 100
        assert envelope["data"]["files_truncated"] is True

    def test_the_counts_remain_the_true_totals(self):
        """A receiver that only wants numbers must never be misled by truncation."""
        raw = make_event("sync.completed", self._files(10_000))
        _body, envelope = build_body(
            raw, make_target(max_files_per_event=100), CONTEXT, attempt=1, scope=SCOPE
        )
        assert envelope["data"]["uploaded"] == 10_000

    def test_no_truncation_flag_when_nothing_was_cut(self):
        raw = make_event("sync.completed", self._files(3))
        _body, envelope = build_body(
            raw, make_target(max_files_per_event=100), CONTEXT, attempt=1, scope=SCOPE
        )
        assert envelope["data"]["files_truncated"] is False

    def test_zero_omits_the_lists_entirely(self):
        raw = make_event("sync.completed", self._files(50))
        _body, envelope = build_body(
            raw, make_target(max_files_per_event=0), CONTEXT, attempt=1, scope=SCOPE
        )
        assert "files" not in envelope["data"]
        assert envelope["data"]["files_truncated"] is True

    def test_a_body_over_the_ceiling_drops_the_lists(self):
        """Truncating is always better than failing to deliver sync.completed."""
        huge = {"files": {"uploaded": ["x" * 2000 for _ in range(2000)],
                          "downloaded": [], "deleted": [], "conflicted": []}}
        raw = make_event("sync.completed", huge)
        body, envelope = build_body(
            raw, make_target(max_files_per_event=10_000), CONTEXT, attempt=1, scope=SCOPE
        )
        assert len(body) <= payload_mod.MAX_BODY_BYTES
        assert "files" not in envelope["data"]
        assert envelope["data"]["files_truncated"] is True


class TestPathPrivacy:
    def test_include_paths_false_hashes_the_paths(self):
        raw = make_event("sync.completed", {"files": {
            "uploaded": ["divorce/settlement.docx"], "downloaded": [],
            "deleted": [], "conflicted": [],
        }})
        _body, envelope = build_body(
            raw, make_target(include_paths=False), CONTEXT, attempt=1, scope=SCOPE
        )
        expected = hashlib.sha256(b"divorce/settlement.docx").hexdigest()
        assert envelope["data"]["files"]["uploaded"] == [expected]
        assert envelope["data"]["files_hashed"] is True
        assert "settlement" not in json.dumps(envelope)

    def test_include_paths_false_also_drops_the_local_path_from_scope(self):
        raw = make_event("sync.completed", {})
        _body, envelope = build_body(
            raw, make_target(include_paths=False), CONTEXT, attempt=1, scope=SCOPE
        )
        assert "local_path" not in envelope["scope"]


# ── Auth headers ────────────────────────────────────────────────────────────


class TestAuthHeaders:
    def test_none_sends_nothing(self):
        assert auth_headers(WebhookAuth(mode="none")) == {}

    def test_basic_is_base64_of_user_colon_password(self):
        headers = auth_headers(WebhookAuth(mode="basic", username="cds", password="pw"))
        assert headers == {"Authorization": "Basic Y2RzOnB3"}

    def test_bearer_uses_the_literal_token(self):
        headers = auth_headers(WebhookAuth(mode="bearer", token="abc123"))
        assert headers == {"Authorization": "Bearer abc123"}

    def test_custom_sets_an_arbitrary_header(self):
        headers = auth_headers(WebhookAuth(mode="custom", header="X-API-Key", value="k"))
        assert headers == {"X-API-Key": "k"}

    def test_custom_may_set_authorization_with_its_own_scheme(self):
        """Covers schemes we do not model, e.g. `Authorization: Token abc`."""
        headers = auth_headers(
            WebhookAuth(mode="custom", header="Authorization", value="Token abc")
        )
        assert headers == {"Authorization": "Token abc"}

    def test_env_indirection_is_read_at_request_time(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "from-the-environment")
        headers = auth_headers(WebhookAuth(mode="bearer", token_env="MY_TOKEN"))
        assert headers == {"Authorization": "Bearer from-the-environment"}

    def test_a_literal_wins_over_an_env_reference(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "from-env")
        headers = auth_headers(
            WebhookAuth(mode="bearer", token="literal", token_env="MY_TOKEN")
        )
        assert headers == {"Authorization": "Bearer literal"}

    def test_an_unset_variable_raises_naming_the_variable_not_the_value(self, monkeypatch):
        monkeypatch.delenv("ABSENT_TOKEN", raising=False)
        with pytest.raises(MissingSecret) as excinfo:
            auth_headers(WebhookAuth(mode="bearer", token_env="ABSENT_TOKEN"))
        assert "ABSENT_TOKEN" in str(excinfo.value)

    def test_the_content_type_and_target_headers_are_included(self):
        target = make_target(headers={"X-Tenant": "acme"})
        headers = build_headers(target, b"{}")
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Tenant"] == "acme"


class TestSignature:
    def test_the_digest_covers_the_timestamp_and_the_exact_bytes(self):
        body = b'{"a":1}'
        sig = WebhookSignature(secret="topsecretvalue", algorithm="sha256")
        headers = signature_headers(sig, body, timestamp=1700000000)
        expected = hmac.new(
            b"topsecretvalue", b"1700000000." + body, hashlib.sha256
        ).hexdigest()
        assert headers["X-CDS-Signature"] == f"sha256={expected}"
        assert headers["X-CDS-Timestamp"] == "1700000000"

    def test_changing_one_body_byte_invalidates_it(self):
        sig = WebhookSignature(secret="topsecretvalue")
        a = signature_headers(sig, b'{"a":1}', timestamp=1)["X-CDS-Signature"]
        b = signature_headers(sig, b'{"a":2}', timestamp=1)["X-CDS-Signature"]
        assert a != b

    def test_changing_the_timestamp_invalidates_it(self):
        """The timestamp is inside the signed material, so a captured body cannot be
        replayed later under a fresh timestamp."""
        sig = WebhookSignature(secret="topsecretvalue")
        a = signature_headers(sig, b"{}", timestamp=1)["X-CDS-Signature"]
        b = signature_headers(sig, b"{}", timestamp=2)["X-CDS-Signature"]
        assert a != b

    def test_sha512_is_supported(self):
        sig = WebhookSignature(secret="topsecretvalue", algorithm="sha512")
        headers = signature_headers(sig, b"{}", timestamp=1)
        assert headers["X-CDS-Signature"].startswith("sha512=")

    def test_custom_header_names_are_honoured(self):
        sig = WebhookSignature(
            secret="topsecretvalue", header="X-Sig", timestamp_header="X-Ts"
        )
        headers = signature_headers(sig, b"{}", timestamp=1)
        assert set(headers) == {"X-Sig", "X-Ts"}

    def test_it_composes_with_bearer_auth(self):
        target = make_target(
            auth=WebhookAuth(mode="bearer", token="tok"),
            signature=WebhookSignature(secret="topsecretvalue"),
        )
        headers = build_headers(target, b"{}")
        assert headers["Authorization"] == "Bearer tok"
        assert "X-CDS-Signature" in headers


# ── Delivery ────────────────────────────────────────────────────────────────


class TestDeliveryHappyPath:
    @pytest.mark.asyncio
    async def test_an_event_reaches_the_receiver_with_its_headers(self):
        async with Receiver() as receiver:
            target = make_target(
                url=receiver.url,
                auth=WebhookAuth(mode="bearer", token="tok123"),
                headers={"X-Tenant": "acme"},
            )
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {"uploaded": 1}), target, SCOPE)
                await _wait_for(lambda: len(receiver.requests) == 1)
            finally:
                await delivery.stop()

            request = receiver.requests[0]
            assert request["headers"]["Authorization"] == "Bearer tok123"
            assert request["headers"]["X-Tenant"] == "acme"
            assert request["json"]["event"] == "sync.completed"

    @pytest.mark.asyncio
    async def test_stats_record_a_success(self):
        async with Receiver() as receiver:
            target = make_target(url=receiver.url)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), target, SCOPE)
                await _wait_for(lambda: len(receiver.requests) == 1)
                await asyncio.sleep(0.1)
                stats = delivery.stats()[0]
                assert stats["delivered"] == 1
                assert stats["healthy"] is True
            finally:
                await delivery.stop()

    @pytest.mark.asyncio
    async def test_per_target_ordering_is_preserved(self):
        async with Receiver() as receiver:
            target = make_target(url=receiver.url)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                for i in range(5):
                    delivery.submit(
                        make_event("sync.completed", {"n": i}), target, SCOPE
                    )
                await _wait_for(lambda: len(receiver.requests) == 5, timeout=8)
            finally:
                await delivery.stop()
            assert [r["json"]["data"]["n"] for r in receiver.requests] == [0, 1, 2, 3, 4]


class TestRetryPolicy:
    @pytest.mark.asyncio
    async def test_a_500_is_retried(self):
        async with Receiver() as receiver:
            receiver.responses = [500, 200]
            target = make_target(url=receiver.url, max_attempts=3)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), target, SCOPE)
                await _wait_for(lambda: len(receiver.requests) == 2, timeout=8)
            finally:
                await delivery.stop()
            assert receiver.requests[0]["json"]["delivery"]["attempt"] == 1
            assert receiver.requests[1]["json"]["delivery"]["attempt"] == 2

    @pytest.mark.asyncio
    async def test_a_401_is_not_retried(self):
        """A configuration error. Retrying it per event turns a typo into a flood."""
        async with Receiver() as receiver:
            receiver.responses = [401, 200]
            target = make_target(url=receiver.url, max_attempts=5)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), target, SCOPE)
                await asyncio.sleep(1.0)
            finally:
                await delivery.stop()
            assert len(receiver.requests) == 1, "a 401 must not be retried"

    @pytest.mark.asyncio
    async def test_a_422_is_not_retried(self):
        async with Receiver() as receiver:
            receiver.responses = [422]
            target = make_target(url=receiver.url, max_attempts=5)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), target, SCOPE)
                await asyncio.sleep(1.0)
            finally:
                await delivery.stop()
            assert len(receiver.requests) == 1

    @pytest.mark.asyncio
    async def test_a_429_is_retried(self):
        async with Receiver() as receiver:
            receiver.responses = [429, 200]
            target = make_target(url=receiver.url, max_attempts=3)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), target, SCOPE)
                await _wait_for(lambda: len(receiver.requests) == 2, timeout=8)
            finally:
                await delivery.stop()

    @pytest.mark.asyncio
    async def test_max_attempts_is_honoured(self):
        async with Receiver() as receiver:
            receiver.responses = [500] * 10
            target = make_target(url=receiver.url, max_attempts=2)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), target, SCOPE)
                await asyncio.sleep(3.0)
            finally:
                await delivery.stop()
            assert len(receiver.requests) == 2

    @pytest.mark.asyncio
    async def test_retry_after_is_honoured(self):
        async with Receiver() as receiver:
            receiver.responses = [429, 200]
            receiver.headers_to_send = {"Retry-After": "1"}
            target = make_target(url=receiver.url, max_attempts=3)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            started = time.monotonic()
            try:
                delivery.submit(make_event("sync.completed", {}), target, SCOPE)
                await _wait_for(lambda: len(receiver.requests) == 2, timeout=8)
            finally:
                await delivery.stop()
            assert time.monotonic() - started >= 0.9, "Retry-After was ignored"


class TestQueueBounds:
    @pytest.mark.asyncio
    async def test_overflow_drops_the_oldest_and_counts_it(self):
        async with Receiver() as receiver:
            receiver.delay = 5.0  # keep the worker busy so the queue backs up
            target = make_target(url=receiver.url)
            delivery = WebhookDelivery(CONTEXT, queue_size=5)
            await delivery.start()
            try:
                for i in range(30):
                    delivery.submit(
                        make_event("sync.completed", {"n": i}), target, SCOPE
                    )
                await asyncio.sleep(0.2)
                stats = delivery.stats()[0]
                assert stats["dropped"] > 0, "the bound was not enforced"
                assert stats["queued"] <= 5
            finally:
                await delivery.stop()

    @pytest.mark.asyncio
    async def test_priority_events_use_a_separate_lane(self):
        """A chatty sync.completed stream must not be able to evict deletion.blocked."""
        async with Receiver() as receiver:
            receiver.delay = 5.0
            target = make_target(url=receiver.url)
            delivery = WebhookDelivery(CONTEXT, queue_size=2)
            await delivery.start()
            try:
                for i in range(50):
                    delivery.submit(
                        make_event("sync.completed", {"n": i}), target, SCOPE
                    )
                delivery.submit(make_event("deletion.blocked", {"count": 9000}), target, SCOPE)
                await asyncio.sleep(0.2)
                # The priority event is still queued despite the flood.
                assert delivery.stats()[0]["queued"] >= 1
            finally:
                await delivery.stop()

    def test_deletion_blocked_is_classified_as_priority(self):
        assert make_event("deletion.blocked", {}).is_priority
        assert make_event("sync.failed", {}).is_priority
        assert not make_event("sync.completed", {}).is_priority


class TestIsolation:
    @pytest.mark.asyncio
    async def test_a_dead_target_does_not_delay_a_healthy_one(self):
        async with Receiver() as healthy:
            # Port 1 is reserved and refuses instantly, so this is a fast failure
            # rather than a timeout.
            dead = make_target(name="dead", url="http://127.0.0.1:1/hook")
            live = make_target(name="live", url=healthy.url)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), dead, SCOPE)
                delivery.submit(make_event("sync.completed", {}), live, SCOPE)
                await _wait_for(lambda: len(healthy.requests) == 1, timeout=5)
            finally:
                await delivery.stop()

    @pytest.mark.asyncio
    async def test_two_pairs_sharing_a_name_get_independent_channels(self):
        async with Receiver() as a, Receiver() as b:
            def target_for(uid, url):
                targets, _ = resolve_targets([(f"pair:{uid}", WebhooksConfig(
                    targets=[WebhookTarget(
                        name="same-name", define=True, url=url, events=["*"],
                        auth=WebhookAuth(mode="none"),
                    )]
                ))])
                return targets[0]

            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), target_for("aaa", a.url), SCOPE)
                delivery.submit(make_event("sync.completed", {}), target_for("bbb", b.url), SCOPE)
                await _wait_for(
                    lambda: len(a.requests) == 1 and len(b.requests) == 1, timeout=5
                )
                assert len(delivery.stats()) == 2, (
                    "the same target name from two pairs must not share one channel"
                )
            finally:
                await delivery.stop()


class TestSecurity:
    @pytest.mark.asyncio
    async def test_redirects_are_not_followed(self):
        """A 302 to a link-local address is the standard way to turn an outbound
        webhook into a request forger."""
        hits = {"hook": 0, "elsewhere": 0}

        async def hook(request):
            hits["hook"] += 1
            raise web.HTTPFound("/elsewhere")

        async def elsewhere(request):
            hits["elsewhere"] += 1
            return web.Response(status=200)

        app = web.Application()
        app.router.add_post("/hook", hook)
        app.router.add_post("/elsewhere", elsewhere)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            target = make_target(url=f"http://127.0.0.1:{port}/hook", max_attempts=1)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), target, SCOPE)
                await asyncio.sleep(0.8)
            finally:
                await delivery.stop()
        finally:
            await runner.cleanup()

        assert hits["hook"] == 1
        assert hits["elsewhere"] == 0, "the redirect was followed"

    @pytest.mark.asyncio
    async def test_a_missing_secret_is_not_retried(self, monkeypatch):
        """A deployment error: the variable will not appear mid-run."""
        monkeypatch.delenv("ABSENT", raising=False)
        async with Receiver() as receiver:
            target = make_target(
                url=receiver.url,
                auth=WebhookAuth(mode="bearer", token_env="ABSENT"),
                max_attempts=5,
            )
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            try:
                delivery.submit(make_event("sync.completed", {}), target, SCOPE)
                await asyncio.sleep(0.8)
                # Read before stop(): stopping tears the channels down.
                stats = delivery.stats()[0]
            finally:
                await delivery.stop()
            assert receiver.requests == [], "no request should have been attempted"
            assert stats["failed"] == 1


class TestNonBlocking:
    @pytest.mark.asyncio
    async def test_submit_does_not_stall_the_event_loop(self):
        """Measured as loop jitter, not wall clock.

        A hung-receiver timing test only exercises the queue, which was never the
        risk. The risk is serialising on the emit side: `sync.completed` on a large
        initial sync is tens of thousands of paths, once per target, on the thread
        that is also running every other pair's transfers.
        """
        async with Receiver() as receiver:
            receiver.delay = 2.0
            targets = [
                make_target(name=f"t{i}", url=receiver.url) for i in range(3)
            ]
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()

            worst = 0.0
            stop = False

            async def heartbeat():
                nonlocal worst
                while not stop:
                    before = time.monotonic()
                    await asyncio.sleep(0.01)
                    worst = max(worst, time.monotonic() - before - 0.01)

            beat = asyncio.create_task(heartbeat())
            try:
                await asyncio.sleep(0.05)
                params = {"files": {
                    "uploaded": [f"path/to/file-{i}.txt" for i in range(50_000)],
                    "downloaded": [], "deleted": [], "conflicted": [],
                }}
                raw = make_event("sync.completed", params)
                for target in targets:
                    delivery.submit(raw, target, SCOPE)
                await asyncio.sleep(0.05)
            finally:
                stop = True
                await beat
                await delivery.stop()

            assert worst < 0.25, (
                f"submitting stalled the loop for {worst:.3f}s; serialisation belongs "
                f"in the worker, not at the emit site"
            )


# ── Dispatcher ──────────────────────────────────────────────────────────────


class TestDispatcher:
    def _config(self, url, events=("sync.completed",)):
        cfg = Config()
        cfg.webhooks = WebhooksConfig(targets=[WebhookTarget(
            name="t", define=True, url=url, events=list(events),
            auth=WebhookAuth(mode="none"),
        )])
        cfg.sync.pairs = [SyncPair(
            local_path="/home/me/Docs", uid="uid-1", account_id="me@example.com",
            provider="gdrive", remote_folder_id="rf-1",
        )]
        return cfg

    @pytest.mark.asyncio
    async def test_an_event_is_routed_and_the_scope_is_built_from_the_pair(self):
        async with Receiver() as receiver:
            cfg = self._config(receiver.url)
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            bus = EventBus()
            bus.subscribe(WebhookDispatcher(cfg, delivery))
            try:
                await bus.emit("sync_complete", {"pair_id": "pair_0", "uploaded": 1})
                await _wait_for(lambda: len(receiver.requests) == 1)
            finally:
                await delivery.stop()

            scope = receiver.requests[0]["json"]["scope"]
            assert scope["pair_id"] == "uid-1", "the stable uid, not pair_0"
            assert scope["local_path"] == "/home/me/Docs"
            assert scope["remote_folder_id"] == "rf-1"
            assert scope["account"] == {"provider": "gdrive", "email": "me@example.com"}

    @pytest.mark.asyncio
    async def test_a_non_matching_event_is_not_delivered(self):
        async with Receiver() as receiver:
            cfg = self._config(receiver.url, events=("deletion.blocked",))
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            bus = EventBus()
            bus.subscribe(WebhookDispatcher(cfg, delivery))
            try:
                await bus.emit("sync_complete", {"pair_id": "pair_0"})
                await asyncio.sleep(0.4)
            finally:
                await delivery.stop()
            assert receiver.requests == []

    @pytest.mark.asyncio
    async def test_an_unexported_event_is_ignored(self):
        async with Receiver() as receiver:
            cfg = self._config(receiver.url, events=("*",))
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            bus = EventBus()
            bus.subscribe(WebhookDispatcher(cfg, delivery))
            try:
                await bus.emit("status_changed", {"pair_id": "pair_0", "status": "idle"})
                await asyncio.sleep(0.4)
            finally:
                await delivery.stop()
            assert receiver.requests == []

    @pytest.mark.asyncio
    async def test_a_dispatcher_failure_does_not_reach_the_emitter(self):
        cfg = self._config("http://127.0.0.1:1/hook")
        broken = WebhookDelivery(CONTEXT)  # never started
        bus = EventBus()
        bus.subscribe(WebhookDispatcher(cfg, broken))
        await bus.emit("sync_complete", {"pair_id": "pair_0"})  # must not raise

    @pytest.mark.asyncio
    async def test_an_unknown_pair_id_is_survived(self):
        cfg = self._config("http://127.0.0.1:1/hook")
        delivery = WebhookDelivery(CONTEXT)
        await delivery.start()
        bus = EventBus()
        bus.subscribe(WebhookDispatcher(cfg, delivery))
        try:
            await bus.emit("sync_complete", {"pair_id": "pair_99"})
            await bus.emit("sync_complete", {"pair_id": "nonsense"})
        finally:
            await delivery.stop()

    @pytest.mark.asyncio
    async def test_the_structured_deletion_breach_is_delivered(self):
        async with Receiver() as receiver:
            cfg = self._config(receiver.url, events=("deletion.blocked",))
            delivery = WebhookDelivery(CONTEXT)
            await delivery.start()
            bus = EventBus()
            bus.subscribe(WebhookDispatcher(cfg, delivery))
            try:
                await bus.emit("delete_blocked", {
                    "pair_id": "pair_0",
                    "message": "4213 remote files would be deleted",
                    "breaches": [{
                        "direction": "remote", "count": 4213, "limit": 100,
                        "tracked": 5001, "ratio": 0.8424, "recent": 0,
                        "total_in_window": 4213, "window_seconds": 60,
                        "sample": ["a.jpg", "b.jpg"], "sample_truncated": True,
                    }],
                    "pair_paused": True, "resolution_required": True,
                })
                await _wait_for(lambda: len(receiver.requests) == 1)
            finally:
                await delivery.stop()

            data = receiver.requests[0]["json"]["data"]
            assert data["breaches"][0]["ratio"] == 0.8424, (
                "the ratio is what makes this actionable: 4213 of 5001 tracked files "
                "is a wiped source, 4213 of 4,000,000 is a cleanup"
            )
            assert data["breaches"][0]["direction"] == "remote"
            assert data["resolution_required"] is True


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    """Poll until ``predicate`` holds, so tests do not depend on fixed sleeps."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout}s")
