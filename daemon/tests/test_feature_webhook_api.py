"""Tests for the webhook read/write API: secret masking and the auth guard.

The masking round trip is the one that matters. A read API returning secrets is
unacceptable -- the web UI would put them in a browser and in every screenshot. But an
API that returns a placeholder and a UI that saves back what it read **overwrites the
real secret with the placeholder**, and the user finds out when the webhook starts
returning 401. So a GET followed by an unmodified PUT must leave the stored credential
byte-identical, and that is asserted here rather than hoped for.

The auth guard is the other half. Webhook writes require the HTTP token to be
configured, which is deliberately *not* the posture of the other endpoints: everything
they can do moves the user's own data between the user's own accounts, whereas a
webhook write sends the user's live filename stream to a host the caller picked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cloud_drive_sync.config import Config, SyncPair
from cloud_drive_sync.ipc.handlers import RequestHandler
from cloud_drive_sync.webhooks.api import (
    WebhookAuthRequired,
    apply_update,
    masked,
    require_http_token,
)
from cloud_drive_sync.webhooks.models import (
    WebhookAuth,
    WebhooksConfig,
    WebhookSignature,
    WebhookTarget,
)


def _config_with_secrets(tmp_path: Path) -> Config:
    cfg = Config()
    cfg._source_path = tmp_path / "config.toml"
    cfg.http.token = "a-configured-http-token"
    cfg.webhooks = WebhooksConfig(targets=[
        WebhookTarget(
            name="ops", define=True, url="https://ops.example.com/hook",
            events=["sync.completed"],
            auth=WebhookAuth(mode="bearer", token="the-real-bearer-token"),
            signature=WebhookSignature(secret="the-real-signing-secret"),
        ),
        WebhookTarget(
            name="legacy", define=True, url="https://old.example.com/hook",
            events=["sync.failed"],
            auth=WebhookAuth(mode="basic", username="cds", password="the-real-password"),
        ),
    ])
    cfg.sync.pairs = [SyncPair(local_path="/data", uid="uid-1")]
    return cfg


class TestMasking:
    def test_secrets_are_replaced_with_none(self, tmp_path: Path):
        view = masked(_config_with_secrets(tmp_path).webhooks)
        ops = view["targets"][0]
        assert ops["auth"]["token"] is None
        assert ops["signature"]["secret"] is None
        assert view["targets"][1]["auth"]["password"] is None

    def test_a_set_flag_says_a_secret_exists(self, tmp_path: Path):
        """So a UI can render "unchanged — type to replace" instead of an empty box
        that looks like nothing is configured."""
        view = masked(_config_with_secrets(tmp_path).webhooks)
        assert view["targets"][0]["auth"]["token_set"] is True
        assert view["targets"][0]["signature"]["secret_set"] is True

    def test_the_flag_is_false_when_no_secret_is_stored(self):
        cfg = WebhooksConfig(targets=[WebhookTarget(
            name="t", define=True, url="https://x/y", events=["*"],
            auth=WebhookAuth(mode="bearer", token_env="SOME_VAR"),
        )])
        view = masked(cfg)
        assert view["targets"][0]["auth"]["token_set"] is False
        assert view["targets"][0]["auth"]["token_env"] == "SOME_VAR", (
            "an env *reference* is not a secret and must stay visible"
        )

    def test_no_literal_secret_appears_anywhere_in_the_view(self, tmp_path: Path):
        import json
        rendered = json.dumps(masked(_config_with_secrets(tmp_path).webhooks))
        for secret in (
            "the-real-bearer-token", "the-real-signing-secret", "the-real-password",
        ):
            assert secret not in rendered

    def test_non_secret_fields_are_untouched(self, tmp_path: Path):
        view = masked(_config_with_secrets(tmp_path).webhooks)
        ops = view["targets"][0]
        assert ops["url"] == "https://ops.example.com/hook"
        assert ops["events"] == ["sync.completed"]
        assert ops["auth"]["mode"] == "bearer"
        assert view["targets"][1]["auth"]["username"] == "cds"


class TestTheMaskingRoundTrip:
    def test_saving_back_an_unmodified_read_preserves_the_secrets(self, tmp_path: Path):
        """The trap, stated as a test. This is the failure users would report as
        "the webhook stopped working after I opened settings"."""
        cfg = _config_with_secrets(tmp_path)
        view = masked(cfg.webhooks)          # what a GET returns
        updated = apply_update(cfg.webhooks, view)   # what an unmodified PUT sends

        ops = next(t for t in updated.targets if t.name == "ops")
        legacy = next(t for t in updated.targets if t.name == "legacy")
        assert ops.auth.token == "the-real-bearer-token"
        assert ops.signature.secret == "the-real-signing-secret"
        assert legacy.auth.password == "the-real-password"

    def test_a_new_secret_replaces_the_old_one(self, tmp_path: Path):
        cfg = _config_with_secrets(tmp_path)
        view = masked(cfg.webhooks)
        view["targets"][0]["auth"]["token"] = "a-brand-new-token"
        updated = apply_update(cfg.webhooks, view)
        assert updated.targets[0].auth.token == "a-brand-new-token"

    def test_the_round_trip_survives_a_real_config_save(self, tmp_path: Path):
        cfg = _config_with_secrets(tmp_path)
        cfg.webhooks = apply_update(cfg.webhooks, masked(cfg.webhooks))
        cfg.save()
        reloaded = Config.load(cfg._source_path)
        assert reloaded.webhooks.targets[0].auth.token == "the-real-bearer-token"

    def test_unmentioned_top_level_fields_are_unchanged(self):
        existing = WebhooksConfig(enabled=False, allow_private_addresses=False)
        updated = apply_update(existing, {})
        assert updated.enabled is False
        assert updated.allow_private_addresses is False

    def test_an_explicit_value_overrides(self):
        existing = WebhooksConfig(enabled=False)
        assert apply_update(existing, {"enabled": True}).enabled is True

    def test_omitting_targets_leaves_them_alone(self, tmp_path: Path):
        cfg = _config_with_secrets(tmp_path)
        updated = apply_update(cfg.webhooks, {"enabled": True})
        assert [t.name for t in updated.targets] == ["ops", "legacy"]

    def test_supplying_targets_replaces_the_whole_list(self, tmp_path: Path):
        """A partial list would make deletion impossible."""
        cfg = _config_with_secrets(tmp_path)
        updated = apply_update(cfg.webhooks, {"targets": [
            {"name": "ops", "url": "https://ops.example.com/hook", "events": ["*"]}
        ]})
        assert [t.name for t in updated.targets] == ["ops"]

    def test_a_removed_target_takes_its_secret_with_it(self, tmp_path: Path):
        cfg = _config_with_secrets(tmp_path)
        updated = apply_update(cfg.webhooks, {"targets": []})
        assert updated.targets == []


class TestAuthGuard:
    def test_a_configured_token_permits_the_write(self):
        require_http_token("some-token")  # must not raise

    def test_no_token_refuses_the_write(self):
        with pytest.raises(WebhookAuthRequired):
            require_http_token("")

    def test_the_message_explains_why_rather_than_just_refusing(self):
        with pytest.raises(WebhookAuthRequired) as excinfo:
            require_http_token(None)
        message = str(excinfo.value)
        assert "gen-token" in message, "the fix should be named"
        assert "any host" in message, "the reason should be stated"


class TestHandlers:
    def _handler(self, tmp_path: Path, *, token: str = "tok"):
        cfg = _config_with_secrets(tmp_path)
        cfg.http.token = token
        return cfg, RequestHandler(None, cfg)

    @pytest.mark.asyncio
    async def test_get_webhooks_masks_secrets(self, tmp_path: Path):
        _cfg, handler = self._handler(tmp_path)
        result = await handler._get_webhooks({"scope": "global"})
        assert result["scope"] == "global"
        assert result["webhooks"]["targets"][0]["auth"]["token"] is None

    @pytest.mark.asyncio
    async def test_get_webhooks_for_a_pair_scope(self, tmp_path: Path):
        _cfg, handler = self._handler(tmp_path)
        result = await handler._get_webhooks({"scope": "pair:uid-1"})
        assert result["scope"] == "pair:uid-1"

    @pytest.mark.asyncio
    async def test_an_unknown_pair_uid_is_rejected(self, tmp_path: Path):
        _cfg, handler = self._handler(tmp_path)
        with pytest.raises(TypeError, match="No sync pair"):
            await handler._get_webhooks({"scope": "pair:does-not-exist"})

    @pytest.mark.asyncio
    async def test_an_unknown_scope_is_rejected(self, tmp_path: Path):
        _cfg, handler = self._handler(tmp_path)
        with pytest.raises(TypeError, match="Unknown webhook scope"):
            await handler._get_webhooks({"scope": "account:whatever"})

    @pytest.mark.asyncio
    async def test_set_webhooks_is_refused_without_a_token(self, tmp_path: Path):
        _cfg, handler = self._handler(tmp_path, token="")
        with pytest.raises(TypeError, match="requires authentication"):
            await handler._set_webhooks({"scope": "global", "webhooks": {}})

    @pytest.mark.asyncio
    async def test_test_webhook_is_refused_without_a_token(self, tmp_path: Path):
        _cfg, handler = self._handler(tmp_path, token="")
        with pytest.raises(TypeError, match="requires authentication"):
            await handler._test_webhook({"scope": "global"})

    @pytest.mark.asyncio
    async def test_set_webhooks_persists_and_reports_problems(self, tmp_path: Path):
        cfg, handler = self._handler(tmp_path)
        result = await handler._set_webhooks({
            "scope": "pair:uid-1",
            "webhooks": {"targets": [
                # No `define`, and the name is not inherited: should be reported.
                {"name": "brand-new", "url": "https://x/y", "events": ["*"]}
            ]},
        })
        assert result["problems"], "a new name without define must be reported"
        assert any("define" in p for p in result["problems"])
        # And it was still persisted, so the user can see and fix what they wrote.
        assert cfg._source_path.exists()
        assert Config.load(cfg._source_path).sync.pairs[0].webhooks.targets

    @pytest.mark.asyncio
    async def test_resolved_view_strips_the_url_path_and_query(self, tmp_path: Path):
        cfg, handler = self._handler(tmp_path)
        cfg.webhooks.targets[0].url = "https://ops.example.com/hooks/secret-path?t=tok"
        result = await handler._resolve_webhooks({"scope": "global"})
        endpoints = [t["endpoint"] for t in result["targets"]]
        assert "https://ops.example.com" in endpoints
        assert not any("secret-path" in e for e in endpoints)
        assert not any("tok" in e for e in endpoints)

    @pytest.mark.asyncio
    async def test_webhook_status_when_delivery_is_not_running(self, tmp_path: Path):
        _cfg, handler = self._handler(tmp_path)
        assert await handler._webhook_status({}) == {"running": False, "targets": []}

    @pytest.mark.asyncio
    async def test_test_webhook_needs_delivery_running(self, tmp_path: Path):
        _cfg, handler = self._handler(tmp_path)
        with pytest.raises(TypeError, match="not running"):
            await handler._test_webhook({"scope": "global"})
