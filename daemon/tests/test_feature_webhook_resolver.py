"""Tests for webhook configuration merging.

The proposal declares its worked example "the acceptance test for the resolver -- an
implementation that cannot reproduce it has the merge rules wrong", so that example is
:class:`TestTheWorkedExample`, reproduced literally.

The rest are the cases that broke earlier drafts of the design:

* an inheritable ``events_add`` defeats a lower level's ``events`` replace;
* an inheritable ``events_remove`` empties a lower level's explicit ``events``;
* a name collision with a disabled target silently swallows a webhook the user just
  switched on;
* atomic ``auth`` replacement loses the inherited ``mode`` and downgrades to
  unauthenticated;
* delivery state keyed on ``name`` lets one dead endpoint open another's breaker.

Each of those is a silent failure rather than an error, which is why they get tests
rather than trust.
"""

from __future__ import annotations

from cloud_drive_sync.webhooks.models import (
    WebhookAuth,
    WebhooksConfig,
    WebhookSignature,
    WebhookTarget,
)
from cloud_drive_sync.webhooks.resolver import SCOPE_GLOBAL, resolve_targets

PAIR = "pair:3f7a1c68"
ACCOUNT = "gdrive:work@example.com"


def _by_name(targets):
    return {t.name: t for t in targets}


def _global():
    """The proposal's global level."""
    return WebhooksConfig(
        targets=[
            WebhookTarget(
                name="ops-bus", define=True,
                url="https://ops.example.com/hooks/cds",
                events=["sync.completed", "sync.failed", "deletion.blocked"],
                auth=WebhookAuth(mode="bearer", token_env="CDS_OPS_TOKEN"),
            ),
            WebhookTarget(
                name="home-assistant", define=True,
                url="http://ha.lan:8123/api/webhook/cds",
                events=["sync.completed", "conflict.detected"],
                auth=WebhookAuth(mode="none"),
            ),
        ]
    )


def _account():
    """The proposal's account level."""
    return WebhooksConfig(
        targets=[
            WebhookTarget(name="ops-bus", headers={"X-CDS-Tenant": "work"}),
            WebhookTarget(
                name="compliance", define=True,
                url="https://audit.example.com/ingest",
                events=["deletion.blocked", "conflict.*", "account.auth_failed"],
                auth=WebhookAuth(mode="bearer", token_env="AUDIT_TOKEN"),
            ),
        ]
    )


def _pair():
    """The proposal's pair level."""
    return WebhooksConfig(
        targets=[
            WebhookTarget(name="home-assistant", enabled=False),
            WebhookTarget(name="ops-bus", events=["deletion.blocked"]),
            WebhookTarget(
                name="photo-indexer", define=True,
                url="http://nas.lan:9000/reindex",
                events=["file.uploaded", "file.deleted"],
                auth=WebhookAuth(mode="custom", header="X-API-Key", value_env="NAS_KEY"),
            ),
        ]
    )


class TestTheWorkedExample:
    def test_the_pair_resolves_to_three_targets(self):
        targets, problems = resolve_targets(
            [(SCOPE_GLOBAL, _global()), (ACCOUNT, _account()), (PAIR, _pair())]
        )
        assert problems == []
        assert sorted(t.name for t in targets) == [
            "compliance", "ops-bus", "photo-indexer",
        ], "home-assistant should be dropped; the other three should survive"

    def test_ops_bus_keeps_its_url_gains_a_header_and_narrows_its_events(self):
        targets, _ = resolve_targets(
            [(SCOPE_GLOBAL, _global()), (ACCOUNT, _account()), (PAIR, _pair())]
        )
        ops = _by_name(targets)["ops-bus"]
        assert ops.url == "https://ops.example.com/hooks/cds", "url inherited from global"
        assert ops.headers == {"X-CDS-Tenant": "work"}, "header added at account level"
        assert ops.events == ("deletion.blocked",), "events replaced at pair level"
        assert ops.auth.mode == "bearer", "auth inherited from global"
        assert ops.auth.token_env == "CDS_OPS_TOKEN"

    def test_compliance_is_added_by_the_account_level(self):
        targets, _ = resolve_targets(
            [(SCOPE_GLOBAL, _global()), (ACCOUNT, _account()), (PAIR, _pair())]
        )
        compliance = _by_name(targets)["compliance"]
        assert compliance.defining_scope == ACCOUNT
        assert compliance.url == "https://audit.example.com/ingest"

    def test_photo_indexer_is_added_by_the_pair(self):
        targets, _ = resolve_targets(
            [(SCOPE_GLOBAL, _global()), (ACCOUNT, _account()), (PAIR, _pair())]
        )
        nas = _by_name(targets)["photo-indexer"]
        assert nas.defining_scope == PAIR
        assert nas.auth.mode == "custom"

    def test_a_second_pair_on_the_same_account_differs_exactly_as_documented(self):
        """The contrast is the point: the account change reached both pairs, the pair
        change reached exactly one."""
        targets, problems = resolve_targets(
            [(SCOPE_GLOBAL, _global()), (ACCOUNT, _account()), ("pair:other", None)]
        )
        assert problems == []
        by_name = _by_name(targets)
        assert sorted(by_name) == ["compliance", "home-assistant", "ops-bus"]
        assert by_name["home-assistant"].events == (
            "sync.completed", "conflict.detected",
        ), "the disable was pair-scoped and must not leak"
        assert by_name["ops-bus"].events == (
            "sync.completed", "sync.failed", "deletion.blocked",
        ), "the narrowing was pair-scoped"
        assert by_name["ops-bus"].headers == {"X-CDS-Tenant": "work"}, (
            "the account-level header reaches every pair on that account"
        )


class TestEnabledSemantics:
    def test_a_pair_can_switch_an_inherited_target_off(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(name="ops-bus", enabled=False)])),
        ])
        assert "ops-bus" not in _by_name(targets)

    def test_a_pair_can_re_enable_what_an_account_switched_off(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (ACCOUNT, WebhooksConfig(targets=[WebhookTarget(name="ops-bus", enabled=False)])),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(name="ops-bus", enabled=True)])),
        ])
        assert "ops-bus" in _by_name(targets)

    def test_a_disabled_block_drops_everything(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(enabled=False)),
        ])
        assert targets == []

    def test_a_pair_can_opt_back_in_after_an_account_disabled_the_block(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (ACCOUNT, WebhooksConfig(enabled=False)),
            (PAIR, WebhooksConfig(enabled=True)),
        ])
        assert len(targets) == 2

    def test_enabled_false_is_not_mistaken_for_unset(self):
        """The falsy-sentinel trap: a truthiness test here silently keeps firing a
        webhook the user switched off."""
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(enabled=False, targets=_global().targets)),
        ])
        assert targets == []


class TestDeltaScoping:
    def test_an_account_events_add_does_not_survive_a_pair_events_replace(self):
        """If `events_add` inherited like an ordinary field, the pair's narrowing to a
        single event would silently keep the account's addition."""
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (ACCOUNT, WebhooksConfig(
                targets=[WebhookTarget(name="ops-bus", events_add=["file.uploaded"])]
            )),
            (PAIR, WebhooksConfig(
                targets=[WebhookTarget(name="ops-bus", events=["deletion.blocked"])]
            )),
        ])
        assert _by_name(targets)["ops-bus"].events == ("deletion.blocked",)

    def test_a_global_events_remove_does_not_empty_a_pair_explicit_events(self):
        """The reverse failure, and the nastier one: the target would be dropped for
        having an empty event list, with nothing naming the inherited delta."""
        targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[
                WebhookTarget(
                    name="ops-bus", define=True, url="https://ops/x",
                    events=["sync.completed", "sync.failed"],
                    events_remove=["sync.failed"],
                    auth=WebhookAuth(mode="none"),
                )
            ])),
            (PAIR, WebhooksConfig(
                targets=[WebhookTarget(name="ops-bus", events=["sync.failed"])]
            )),
        ])
        assert problems == []
        assert _by_name(targets)["ops-bus"].events == ("sync.failed",)

    def test_events_add_at_its_own_level_extends_the_inherited_list(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(
                targets=[WebhookTarget(name="ops-bus", events_add=["file.uploaded"])]
            )),
        ])
        assert set(_by_name(targets)["ops-bus"].events) == {
            "sync.completed", "sync.failed", "deletion.blocked", "file.uploaded",
        }

    def test_events_remove_at_its_own_level_narrows_the_inherited_list(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(
                targets=[WebhookTarget(name="ops-bus", events_remove=["sync.failed"])]
            )),
        ])
        assert set(_by_name(targets)["ops-bus"].events) == {
            "sync.completed", "deletion.blocked",
        }

    def test_remove_beats_add_within_one_level(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(
                name="ops-bus", events_add=["file.uploaded"], events_remove=["file.uploaded"],
            )])),
        ])
        assert "file.uploaded" not in _by_name(targets)["ops-bus"].events

    def test_headers_merge_per_key_and_headers_remove_is_level_local(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                headers={"A": "1", "B": "2"}, auth=WebhookAuth(mode="none"),
            )])),
            (ACCOUNT, WebhooksConfig(targets=[
                WebhookTarget(name="t", headers={"C": "3"}, headers_remove=["A"])
            ])),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(name="t", headers={"A": "restored"})])),
        ])
        headers = _by_name(targets)["t"].headers
        assert headers == {"B": "2", "C": "3", "A": "restored"}, (
            "a lower level must be able to set a header an upper level removed"
        )


class TestDefineIntent:
    def test_a_new_name_without_define_is_refused_with_an_explanation(self):
        targets, problems = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(
                name="typo-bus", url="https://x/y", events=["*"],
            )])),
        ])
        assert "typo-bus" not in _by_name(targets)
        assert any("define" in p and "typo-bus" in p for p in problems)

    def test_define_on_an_existing_name_is_refused(self):
        targets, problems = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(
                name="ops-bus", define=True, url="http://nas.lan/x", events=["*"],
            )])),
        ])
        assert any("already defined" in p for p in problems)
        # The override must not have been applied.
        assert _by_name(targets)["ops-bus"].url == "https://ops.example.com/hooks/cds"

    def test_the_silent_collision_case_is_now_reported(self):
        """The hazard `define` exists for. A pair writes what it believes is a new
        target, the name collides with a disabled global one, `enabled = false` is
        inherited, and the webhook the user just switched on never fires -- with a url
        and events present, so no other validation catches it."""
        disabled_global = WebhooksConfig(targets=[WebhookTarget(
            name="home-assistant", define=True, url="http://old/x",
            events=["sync.completed"], enabled=False, auth=WebhookAuth(mode="none"),
        )])
        pair = WebhooksConfig(targets=[WebhookTarget(
            name="home-assistant", url="http://nas.lan:8123/hook", events=["file.uploaded"],
        )])
        targets, problems = resolve_targets([(SCOPE_GLOBAL, disabled_global), (PAIR, pair)])
        assert targets == []
        assert any("inherited" in p and "will not fire" in p for p in problems), (
            f"the silent drop was not explained; problems={problems}"
        )

    def test_a_nameless_target_is_reported(self):
        _targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(define=True, url="https://x/y")])),
        ])
        assert any("no 'name'" in p for p in problems)


class TestAuthValidation:
    def test_auth_is_replaced_atomically(self):
        targets, problems = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(
                name="ops-bus", auth=WebhookAuth(mode="bearer", token_env="OTHER"),
            )])),
        ])
        assert problems == []
        assert _by_name(targets)["ops-bus"].auth.token_env == "OTHER"

    def test_an_auth_block_without_a_mode_is_refused_not_downgraded(self):
        """Atomic replacement loses the inherited mode. Defaulting it to 'none' would
        be a silent downgrade to unauthenticated POSTs against an endpoint expecting a
        bearer token, so it must be an error."""
        targets, problems = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(
                name="ops-bus", auth=WebhookAuth(token_env="OTHER"),
            )])),
        ])
        assert "ops-bus" not in _by_name(targets)
        assert any("no 'mode'" in p for p in problems)

    def test_an_unknown_mode_is_refused(self):
        _targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                auth=WebhookAuth(mode="telepathy"),
            )])),
        ])
        assert any("unknown auth mode" in p for p in problems)

    def test_jwt_is_not_yet_accepted(self):
        """Phase 3. Accepting it in config while delivery cannot mint one would fail
        per event, indistinguishable from a dead endpoint."""
        _targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                auth=WebhookAuth(mode="jwt"),
            )])),
        ])
        assert any("unknown auth mode" in p for p in problems)

    def test_each_mode_requires_its_own_fields(self):
        cases = [
            (WebhookAuth(mode="basic", username="u"), "password"),
            (WebhookAuth(mode="basic", password="p"), "username"),
            (WebhookAuth(mode="bearer"), "token"),
            (WebhookAuth(mode="custom", header="X-Key"), "value"),
            (WebhookAuth(mode="custom", value="v"), "header"),
        ]
        for auth, expected in cases:
            _targets, problems = resolve_targets([
                (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                    name="t", define=True, url="https://x/y", events=["*"], auth=auth,
                )])),
            ])
            assert any(expected in p for p in problems), (
                f"mode {auth.mode} missing {expected} was accepted; problems={problems}"
            )

    def test_an_env_reference_satisfies_the_requirement(self):
        """Validation checks that a *source* is configured, not that it has a value --
        the resolver stays pure and never reads the environment."""
        targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                auth=WebhookAuth(mode="bearer", token_env="NOT_SET_ANYWHERE"),
            )])),
        ])
        assert problems == []
        assert len(targets) == 1

    def test_a_missing_credential_is_caught_at_resolution_not_delivery(self):
        """An inherited key_file lost to atomic replacement would otherwise fail once
        per event, looking exactly like a dead endpoint."""
        _targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                auth=WebhookAuth(mode="bearer", token_env="TOK"),
            )])),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(
                name="t", auth=WebhookAuth(mode="bearer"),
            )])),
        ])
        assert any("bearer" in p and "token" in p for p in problems)


class TestSignatureValidation:
    def test_a_signature_composes_with_auth(self):
        targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                auth=WebhookAuth(mode="bearer", token_env="TOK"),
                signature=WebhookSignature(secret_env="SIGNING"),
            )])),
        ])
        assert problems == []
        assert targets[0].auth.mode == "bearer"
        assert targets[0].signature is not None

    def test_a_signature_without_a_secret_is_refused(self):
        _targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                auth=WebhookAuth(mode="none"), signature=WebhookSignature(),
            )])),
        ])
        assert any("secret" in p for p in problems)

    def test_an_unsupported_algorithm_is_refused(self):
        _targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, url="https://x/y", events=["*"],
                auth=WebhookAuth(mode="none"),
                signature=WebhookSignature(secret_env="S", algorithm="md5"),
            )])),
        ])
        assert any("algorithm" in p for p in problems)


class TestValidationOfTheBasics:
    def test_a_target_that_inherits_no_url_is_dropped_with_an_explanation(self):
        _targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, events=["*"], auth=WebhookAuth(mode="none"),
            )])),
        ])
        assert any("no 'url'" in p for p in problems)

    def test_an_empty_event_list_is_dropped_with_an_explanation(self):
        _targets, problems = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
                name="t", define=True, url="https://x/y", events=[],
                auth=WebhookAuth(mode="none"),
            )])),
        ])
        assert any("empty event list" in p for p in problems)

    def test_one_bad_target_does_not_take_the_good_ones_with_it(self):
        targets, problems = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(targets=[WebhookTarget(name="broken", define=True)])),
        ])
        assert sorted(t.name for t in targets) == ["home-assistant", "ops-bus"]
        assert problems


class TestDefaults:
    def test_defaults_fill_only_what_a_target_did_not_set(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(
                defaults=WebhookTarget(timeout_seconds=99, max_attempts=7),
                targets=[WebhookTarget(
                    name="t", define=True, url="https://x/y", events=["*"],
                    timeout_seconds=3, auth=WebhookAuth(mode="none"),
                )],
            )),
        ])
        assert targets[0].timeout_seconds == 3, "the target's own value must win"
        assert targets[0].max_attempts == 7, "the gap must be filled by defaults"

    def test_a_lower_levels_defaults_beat_a_higher_levels(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(
                defaults=WebhookTarget(timeout_seconds=10),
                targets=[WebhookTarget(
                    name="t", define=True, url="https://x/y", events=["*"],
                    auth=WebhookAuth(mode="none"),
                )],
            )),
            (PAIR, WebhooksConfig(defaults=WebhookTarget(timeout_seconds=42))),
        ])
        assert targets[0].timeout_seconds == 42

    def test_a_pair_defaults_block_reaches_a_target_inherited_from_global(self):
        """A documented choice: "these are this pair's timeouts" is the useful reading,
        and it is what makes a defaults block worth having below the global level."""
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()),
            (PAIR, WebhooksConfig(defaults=WebhookTarget(timeout_seconds=5))),
        ])
        assert all(t.timeout_seconds == 5 for t in targets)

    def test_falsy_defaults_are_applied(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, WebhooksConfig(
                defaults=WebhookTarget(
                    verify_tls=False, include_paths=False, max_files_per_event=0
                ),
                targets=[WebhookTarget(
                    name="t", define=True, url="https://x/y", events=["*"],
                    auth=WebhookAuth(mode="none"),
                )],
            )),
        ])
        assert targets[0].verify_tls is False
        assert targets[0].include_paths is False
        assert targets[0].max_files_per_event == 0

    def test_documented_fallbacks_apply_when_nothing_sets_them(self):
        targets, _ = resolve_targets([(SCOPE_GLOBAL, _global())])
        target = targets[0]
        assert target.timeout_seconds == 15
        assert target.max_attempts == 5
        assert target.verify_tls is True
        assert target.include_paths is True
        assert target.max_files_per_event == 100


class TestTargetKey:
    def test_two_pairs_using_the_same_name_get_distinct_delivery_keys(self):
        """Delivery state -- queue, worker, circuit breaker -- is keyed on target_key.
        Keying it on `name` would let one dead endpoint open the other's breaker."""
        def pair_with(url, uid):
            return resolve_targets([(f"pair:{uid}", WebhooksConfig(targets=[WebhookTarget(
                name="photo-indexer", define=True, url=url, events=["*"],
                auth=WebhookAuth(mode="none"),
            )]))])[0][0]

        a = pair_with("http://nas-a:9000/x", "aaa")
        b = pair_with("http://nas-b:9000/x", "bbb")
        assert a.name == b.name
        assert a.target_key != b.target_key
        assert a.url != b.url

    def test_the_key_names_the_level_that_introduced_it(self):
        targets, _ = resolve_targets([
            (SCOPE_GLOBAL, _global()), (PAIR, _pair()),
        ])
        by_name = _by_name(targets)
        assert by_name["ops-bus"].target_key == "global|ops-bus", (
            "an overridden target keeps the key of the level that defined it, so its "
            "delivery state follows the definition rather than each override"
        )
        assert by_name["photo-indexer"].target_key == f"{PAIR}|photo-indexer"


class TestEventMatching:
    def test_exact_names_match(self):
        target = resolve_targets([(SCOPE_GLOBAL, _global())])[0][0]
        assert target.matches("sync.completed")
        assert not target.matches("file.uploaded")

    def test_a_glob_segment_matches(self):
        target = resolve_targets([(SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
            name="t", define=True, url="https://x/y", events=["conflict.*"],
            auth=WebhookAuth(mode="none"),
        )]))])[0][0]
        assert target.matches("conflict.detected")
        assert target.matches("conflict.resolved")
        assert not target.matches("sync.completed")

    def test_a_bare_star_matches_everything(self):
        target = resolve_targets([(SCOPE_GLOBAL, WebhooksConfig(targets=[WebhookTarget(
            name="t", define=True, url="https://x/y", events=["*"],
            auth=WebhookAuth(mode="none"),
        )]))])[0][0]
        assert target.matches("anything.at.all")

    def test_matching_is_case_sensitive(self):
        """fnmatch is case-insensitive on Windows by default; event names are a wire
        contract and must behave identically on every platform."""
        target = resolve_targets([(SCOPE_GLOBAL, _global())])[0][0]
        assert not target.matches("SYNC.COMPLETED")


class TestNoLevels:
    def test_no_configuration_yields_no_targets_and_no_complaints(self):
        assert resolve_targets([]) == ([], [])

    def test_empty_levels_are_skipped(self):
        assert resolve_targets([(SCOPE_GLOBAL, None), (PAIR, None)]) == ([], [])
