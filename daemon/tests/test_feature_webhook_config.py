"""Round-trip tests for webhook configuration in ``config.toml``.

``Config.load`` reads key by key and ``Config.save`` rebuilds its dict from scratch,
so a key not enumerated in *both* is dropped on load and **erased from the file** on
the next save -- and every settings handler ends in ``save()``. Webhook config is three
levels of nesting, which is exactly where a hand-written marshaller loses something.

The falsy cases are the ones that matter. ``enabled = false``,
``include_paths = false`` and ``max_files_per_event = 0`` are all meaningful values
whose loss is silent and whose consequence is a webhook that keeps firing, or keeps
shipping filenames, after the user switched it off.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cloud_drive_sync.config import Config
from cloud_drive_sync.webhooks.models import (
    WebhookAuth,
    WebhooksConfig,
    WebhookSignature,
    WebhookTarget,
)
from cloud_drive_sync.webhooks.resolver import SCOPE_GLOBAL, resolve_targets
from cloud_drive_sync.webhooks.serialise import webhooks_from_toml, webhooks_to_toml

FULL_CONFIG = """
[webhooks]
enabled = true
allow_private_addresses = false

[webhooks.defaults]
timeout_seconds = 20
max_attempts = 2
verify_tls = false
include_paths = false
max_files_per_event = 0

[[webhooks.targets]]
define = true
name = "ops-bus"
url = "https://ops.example.com/hooks/cds"
events = ["sync.completed", "deletion.blocked"]
headers = { X-Tenant = "acme" }
auth = { mode = "bearer", token_env = "CDS_OPS_TOKEN" }

  [webhooks.targets.signature]
  secret_env = "CDS_SIGNING"
  algorithm = "sha512"
  header = "X-Custom-Sig"

[[sync.pairs]]
uid = "aaaa-bbbb"
local_path = "/data"

  [sync.pairs.webhooks]
  [[sync.pairs.webhooks.targets]]
  name = "ops-bus"
  events = ["deletion.blocked"]
  enabled = false

  [[sync.pairs.webhooks.targets]]
  define = true
  name = "nas"
  url = "http://nas.lan:9000/reindex"
  events = ["file.uploaded"]
  auth = { mode = "custom", header = "X-API-Key", value_env = "NAS_KEY" }
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(FULL_CONFIG)
    return path


class TestLoading:
    def test_the_global_block_is_read(self, config_file: Path):
        cfg = Config.load(config_file)
        assert cfg.webhooks.enabled is True
        assert cfg.webhooks.allow_private_addresses is False
        assert [t.name for t in cfg.webhooks.targets] == ["ops-bus"]

    def test_falsy_defaults_survive_loading(self, config_file: Path):
        defaults = Config.load(config_file).webhooks.defaults
        assert defaults.verify_tls is False
        assert defaults.include_paths is False
        assert defaults.max_files_per_event == 0, "an explicit 0 must not become None"

    def test_nested_auth_and_signature_are_read(self, config_file: Path):
        target = Config.load(config_file).webhooks.targets[0]
        assert target.auth.mode == "bearer"
        assert target.auth.token_env == "CDS_OPS_TOKEN"
        assert target.signature.secret_env == "CDS_SIGNING"
        assert target.signature.algorithm == "sha512"
        assert target.signature.header == "X-Custom-Sig"

    def test_headers_are_read(self, config_file: Path):
        assert Config.load(config_file).webhooks.targets[0].headers == {"X-Tenant": "acme"}

    def test_the_per_pair_block_is_read(self, config_file: Path):
        targets = Config.load(config_file).sync.pairs[0].webhooks.targets
        assert [t.name for t in targets] == ["ops-bus", "nas"]
        assert targets[0].enabled is False, "enabled = false must not be read as unset"
        assert targets[1].define is True

    def test_a_pair_with_no_block_gets_an_empty_one(self, tmp_path: Path):
        path = tmp_path / "c.toml"
        path.write_text('[[sync.pairs]]\nlocal_path = "/x"\n')
        assert Config.load(path).sync.pairs[0].webhooks.is_empty()

    def test_a_config_with_no_webhooks_at_all_is_fine(self, tmp_path: Path):
        path = tmp_path / "c.toml"
        path.write_text("[sync]\npoll_interval = 30\n")
        assert Config.load(path).webhooks.is_empty()


class TestRoundTrip:
    def test_everything_survives_save_and_reload(self, config_file: Path):
        before = Config.load(config_file)
        before.save()
        after = Config.load(config_file)

        assert after.webhooks.enabled == before.webhooks.enabled
        assert after.webhooks.allow_private_addresses is False
        assert after.webhooks.defaults.max_files_per_event == 0
        assert after.webhooks.defaults.verify_tls is False
        assert after.webhooks.defaults.include_paths is False

        b, a = before.webhooks.targets[0], after.webhooks.targets[0]
        assert (a.name, a.define, a.url, a.events) == (b.name, b.define, b.url, b.events)
        assert a.headers == b.headers
        assert a.auth.mode == b.auth.mode
        assert a.auth.token_env == b.auth.token_env
        assert a.signature.algorithm == b.signature.algorithm
        assert a.signature.header == b.signature.header

    def test_the_per_pair_block_survives(self, config_file: Path):
        before = Config.load(config_file)
        before.save()
        after = Config.load(config_file)

        b = before.sync.pairs[0].webhooks.targets
        a = after.sync.pairs[0].webhooks.targets
        assert [(t.name, t.enabled, t.events, t.define) for t in a] == [
            (t.name, t.enabled, t.events, t.define) for t in b
        ]

    def test_resolution_is_identical_before_and_after(self, config_file: Path):
        """The property that actually matters: whatever the round trip does to the
        representation, the *effect* must not change."""
        before = Config.load(config_file)
        levels_before = [
            (SCOPE_GLOBAL, before.webhooks),
            ("pair:aaaa-bbbb", before.sync.pairs[0].webhooks),
        ]
        resolved_before, problems_before = resolve_targets(levels_before)

        before.save()
        after = Config.load(config_file)
        resolved_after, problems_after = resolve_targets([
            (SCOPE_GLOBAL, after.webhooks),
            ("pair:aaaa-bbbb", after.sync.pairs[0].webhooks),
        ])

        assert [t.target_key for t in resolved_after] == [
            t.target_key for t in resolved_before
        ]
        assert [t.events for t in resolved_after] == [t.events for t in resolved_before]
        assert problems_after == problems_before

    def test_a_second_round_trip_is_stable(self, config_file: Path):
        """Guards against a marshaller that keeps adding or dropping a key each pass."""
        Config.load(config_file).save()
        once = config_file.read_text()
        Config.load(config_file).save()
        assert config_file.read_text() == once


class TestErasure:
    def test_changing_an_unrelated_setting_does_not_erase_webhook_config(
        self, config_file: Path
    ):
        """The failure this whole module exists to prevent. `save()` rebuilds its dict
        from scratch, so a key it does not enumerate is silently erased -- and every
        settings handler calls `save()`."""
        cfg = Config.load(config_file)
        cfg.sync.poll_interval = 99  # an unrelated change, as the UI would make
        cfg.save()

        reloaded = Config.load(config_file)
        assert reloaded.sync.poll_interval == 99
        assert [t.name for t in reloaded.webhooks.targets] == ["ops-bus"], (
            "the global webhook config was erased by an unrelated settings change"
        )
        assert [t.name for t in reloaded.sync.pairs[0].webhooks.targets] == [
            "ops-bus", "nas",
        ], "the per-pair webhook config was erased"

    def test_an_empty_block_is_omitted_rather_than_written(self, tmp_path: Path):
        """An upgraded config should not sprout empty tables that read like a setting
        someone cleared -- the same reasoning as [http] being omitted when unset."""
        path = tmp_path / "c.toml"
        cfg = Config()
        cfg._source_path = path
        cfg.save()
        assert "[webhooks]" not in path.read_text()


class TestLenientUnmarshalling:
    def test_unknown_keys_are_ignored_rather_than_fatal(self):
        """A config written by a newer version must not stop an older daemon."""
        cfg = webhooks_from_toml({
            "enabled": True,
            "some_future_key": "whatever",
            "targets": [{"name": "t", "define": True, "url": "https://x/y",
                         "events": ["*"], "future_field": 1}],
        })
        assert cfg.enabled is True
        assert cfg.targets[0].name == "t"

    def test_a_malformed_block_yields_an_empty_config(self):
        assert webhooks_from_toml("not a table").is_empty()
        assert webhooks_from_toml(None).is_empty()

    def test_string_booleans_are_coerced(self):
        """`enabled = "false"` in hand-edited TOML would otherwise be truthy and
        silently enable what the user meant to switch off."""
        assert webhooks_from_toml({"enabled": "false"}).enabled is False
        assert webhooks_from_toml({"enabled": "true"}).enabled is True

    def test_a_bare_string_event_becomes_a_single_entry_list(self):
        target = webhooks_from_toml({
            "targets": [{"name": "t", "define": True, "url": "https://x/y",
                         "events": "sync.completed"}]
        }).targets[0]
        assert target.events == ["sync.completed"]

    def test_a_non_integer_timeout_is_discarded_rather_than_propagated(self):
        target = webhooks_from_toml({
            "targets": [{"name": "t", "timeout_seconds": "not a number"}]
        }).targets[0]
        assert target.timeout_seconds is None, (
            "a junk value must fall back to inheritance, not reach the delivery layer"
        )


class TestMarshalOmitsUnsetFields:
    def test_none_fields_are_omitted_so_inheritance_is_preserved(self):
        """Writing a resolved value would freeze it: the user's later edit to the level
        above would appear to do nothing."""
        out = webhooks_to_toml(WebhooksConfig(targets=[
            WebhookTarget(name="t", define=True, url="https://x/y", events=["*"])
        ]))
        entry = out["targets"][0]
        assert "timeout_seconds" not in entry
        assert "verify_tls" not in entry
        assert "enabled" not in entry

    def test_falsy_fields_are_written(self):
        out = webhooks_to_toml(WebhooksConfig(targets=[
            WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                enabled=False, verify_tls=False, include_paths=False,
                max_files_per_event=0,
            )
        ]))
        entry = out["targets"][0]
        assert entry["enabled"] is False
        assert entry["verify_tls"] is False
        assert entry["include_paths"] is False
        assert entry["max_files_per_event"] == 0

    def test_define_false_is_omitted(self):
        out = webhooks_to_toml(WebhooksConfig(targets=[WebhookTarget(name="t")]))
        assert "define" not in out["targets"][0]

    def test_auth_and_signature_omit_their_empty_fields(self):
        out = webhooks_to_toml(WebhooksConfig(targets=[
            WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                auth=WebhookAuth(mode="bearer", token_env="TOK"),
                signature=WebhookSignature(secret_env="SIG"),
            )
        ]))
        entry = out["targets"][0]
        assert entry["auth"] == {"mode": "bearer", "token_env": "TOK"}
        assert "password" not in entry["auth"]
        assert entry["signature"]["secret_env"] == "SIG"
        assert "secret" not in entry["signature"]
