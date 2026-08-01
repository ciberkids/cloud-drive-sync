"""Tests that state-mutating IPC handlers actually persist what they claim.

``ipc/handlers.py`` is the single backend behind all three front-ends — the Unix
socket, the HTTP/REST API and MCP — so one bug here surfaces in the CLI, the web UI
and an AI assistant at once. It was the least covered large module in the daemon.

**Every test here asserts durable state, not the response.** That is the whole
point. A handler that mutates the in-memory config and returns the new value looks
perfectly correct while writing nothing, or writing under the wrong key. That
already happened once: ``get_max_deletions`` reported per-pair overrides keyed
``pair_0`` while every other method in the API uses ``"0"``, so the response was
well-formed, the UI read it, and the override silently never applied. Asserting the
response would not have caught it; reloading the config from disk does.

So the shape is: drive ``handler.handle()`` with a real request, then
``Config.load()`` the file back and assert the change survived a round trip.

One hazard this file has to avoid. A bare ``Config()`` has ``_source_path = None``,
and ``save()`` then falls back to :func:`config_path` — the **user's real config**.
Fifteen handlers call ``save()``. The pre-existing handler tests get away with it by
never reaching one; anything here would overwrite a real file, so the fixture always
points ``_source_path`` at a temp directory. There is a test at the bottom asserting
that guard, because a fixture that silently stopped isolating would send this whole
file at the user's configuration.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest

from cloud_drive_sync.config import Account, Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.ipc.handlers import RequestHandler
from cloud_drive_sync.ipc.protocol import JsonRpcRequest

#: An absolute path on whichever platform the tests run on. "/home/u/x" is not
#: absolute on Windows, so local_path validation rejects it before reaching the
#: account check these tests are about.
ABS = "C:/tmp/cds-test" if sys.platform == "win32" else "/tmp/cds-test"

#: File modes are POSIX-only; Windows does not implement these bits.
posix_config_modes = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not meaningful on Windows"
)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


@pytest.fixture
def config(config_file: Path) -> Config:
    cfg = Config()
    cfg.sync = SyncConfig(
        poll_interval=10,
        conflict_strategy="keep_both",
        pairs=[
            SyncPair(local_path="/tmp/first", remote_folder_id="root", enabled=True),
            SyncPair(
                local_path="/tmp/second",
                remote_folder_id="folder_abc",
                enabled=False,
                sync_mode="upload_only",
            ),
        ],
    )
    cfg.accounts = [
        Account(email="alice@example.com", provider="gdrive"),
        Account(email="bob@example.com", provider="dropbox"),
    ]
    # Without this, save() writes to the user's real config file.
    cfg._source_path = config_file
    return cfg


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "handlers.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def handler(config: Config, db: Database) -> RequestHandler:
    h = RequestHandler(engine=None, config=config)
    h.set_db(db)
    return h


async def call(handler: RequestHandler, method: str, params: dict | None = None):
    """Drive a handler the way a front-end does, and return the result payload."""
    resp = await handler.handle(JsonRpcRequest(id=1, method=method, params=params or {}))
    assert resp.error is None, f"{method} failed: {resp.error}"
    return resp.result


async def expect_error(handler: RequestHandler, method: str, params: dict | None = None):
    resp = await handler.handle(JsonRpcRequest(id=1, method=method, params=params or {}))
    assert resp.error is not None, f"{method} unexpectedly succeeded"
    return resp.error


def reload(config_file: Path) -> Config:
    """The config as a restarted daemon would see it."""
    assert config_file.exists(), "nothing was written to disk at all"
    return Config.load(config_file)


# ── Delete protection: the settings a mistake makes dangerous ────────────


async def test_setting_the_global_deletion_cap_persists(handler, config_file):
    await call(handler, "set_max_deletions", {"max_deletions_per_sync": 7})

    assert reload(config_file).sync.max_deletions_per_sync == 7


async def test_setting_the_deletion_window_persists(handler, config_file):
    await call(handler, "set_max_deletions", {"deletion_window_seconds": 120})

    assert reload(config_file).sync.deletion_window_seconds == 120


async def test_a_per_pair_override_persists_and_reads_back_under_the_same_key(
    handler, config_file
):
    """The exact bug this file exists for.

    ``set_max_deletions`` addresses pairs as ``"0"``; ``get_max_deletions`` once
    reported them as ``pair_0``. Both looked right in isolation, so the override was
    written and then never found. Asserting a write-then-read round trip through the
    API is what catches an identifier mismatch.
    """
    await call(handler, "set_max_deletions", {"pair_id": "1", "max_deletions_per_sync": 2})

    got = await call(handler, "get_max_deletions")
    assert got["pairs"]["1"] == 2, f"override not readable under the same key: {got['pairs']}"
    assert reload(config_file).sync.pairs[1].max_deletions_per_sync == 2


async def test_a_per_pair_override_does_not_leak_onto_other_pairs(handler, config_file):
    await call(handler, "set_max_deletions", {"pair_id": "1", "max_deletions_per_sync": 2})

    saved = reload(config_file)
    assert saved.sync.pairs[0].max_deletions_per_sync is None
    assert saved.sync.pairs[1].max_deletions_per_sync == 2


async def test_zero_disables_the_guard_and_is_not_confused_with_unset(handler, config_file):
    """``0`` means "no limit" and ``None`` means "inherit". A serialiser that treats
    0 as falsy would turn "disabled" into "inherit the default of 100" on restart —
    the opposite of what the user asked for, and silently."""
    await call(handler, "set_max_deletions", {"pair_id": "0", "max_deletions_per_sync": 0})

    assert reload(config_file).sync.pairs[0].max_deletions_per_sync == 0
    got = await call(handler, "get_max_deletions")
    assert got["pairs"]["0"] == 0


async def test_clearing_an_override_restores_inheritance(handler, config_file):
    await call(handler, "set_max_deletions", {"pair_id": "0", "max_deletions_per_sync": 5})

    await call(handler, "set_max_deletions", {"pair_id": "0", "max_deletions_per_sync": None})

    assert reload(config_file).sync.pairs[0].max_deletions_per_sync is None


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"max_deletions_per_sync": -1},
        {"deletion_window_seconds": -1},
    ],
    ids=["nothing-to-set", "negative-cap", "negative-window"],
)
async def test_invalid_deletion_settings_are_refused(handler, config_file, params):
    """And refused before writing anything — a rejected request that still persisted
    part of its change is worse than one that fails cleanly."""
    await expect_error(handler, "set_max_deletions", params)

    assert not config_file.exists(), "a rejected request still wrote to disk"


# ── Removing things ─────────────────────────────────────────────────────


async def test_removing_a_pair_removes_the_right_one_and_persists(handler, config_file):
    result = await call(handler, "remove_sync_pair", {"id": "0"})

    assert result["local_path"] == "/tmp/first"
    saved = reload(config_file)
    assert [p.local_path for p in saved.sync.pairs] == ["/tmp/second"]


async def test_removing_a_pair_by_index_alias_also_works(handler, config_file):
    """``index`` is accepted alongside ``id``; the CLI and UI have used both."""
    await call(handler, "remove_sync_pair", {"index": "1"})

    assert [p.local_path for p in reload(config_file).sync.pairs] == ["/tmp/first"]


@pytest.mark.parametrize(
    "params",
    [{"id": "9"}, {"id": "-1"}, {"id": "abc"}, {}],
    ids=["out-of-range", "negative", "non-numeric", "missing"],
)
async def test_an_invalid_pair_removal_changes_nothing(handler, config, config_file, params):
    await expect_error(handler, "remove_sync_pair", params)

    assert len(config.sync.pairs) == 2, "a rejected removal dropped a pair anyway"
    assert not config_file.exists()


async def test_removing_an_account_persists(handler, config_file):
    await call(handler, "remove_account", {"email": "bob@example.com"})

    assert [a.email for a in reload(config_file).accounts] == ["alice@example.com"]


async def test_removing_an_unknown_account_reports_ok_and_changes_nothing(handler, config):
    """Pins idempotent removal rather than asserting an error.

    I expected a failure here and got ``{"status": "ok"}``. Removal being idempotent
    is defensible — retrying a remove should not fail — so this records the behaviour
    instead of changing it. The cost is that a typo'd address reports success, which
    is worth knowing but not worth breaking every caller over.

    What must hold either way is that nothing else changed.
    """
    result = await call(handler, "remove_account", {"email": "nobody@example.com"})

    assert result["status"] == "ok"
    assert len(config.accounts) == 2


async def test_removing_one_provider_keeps_the_other_accounts_credentials(
    handler, config, tmp_path, monkeypatch
):
    """The bug this pair of tests was written for.

    Credentials were deleted via a hardcoded Google Drive path regardless of which
    provider was removed. Two accounts can share an address, so removing Alice's
    Dropbox account wiped Alice's *Google* credentials — breaking an account she had
    not touched — while leaving the Dropbox ones behind.
    """
    from cloud_drive_sync.providers.dropbox.auth import DropboxAuth
    from cloud_drive_sync.util import paths

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    config.accounts = [
        Account(email="alice@example.com", provider="gdrive"),
        Account(email="alice@example.com", provider="dropbox"),
    ]

    google = paths.account_credentials_path("alice@example.com")
    google.parent.mkdir(parents=True, exist_ok=True)
    google.write_bytes(b"google")
    dropbox = DropboxAuth._credentials_path("alice@example.com")
    dropbox.parent.mkdir(parents=True, exist_ok=True)
    dropbox.write_bytes(b"dropbox")

    await call(handler, "remove_account", {"email": "alice@example.com", "provider": "dropbox"})

    assert google.exists(), "removing Dropbox deleted the Google Drive credentials"
    assert not dropbox.exists(), "the removed account's credentials were left on disk"
    assert [a.provider for a in config.accounts] == ["gdrive"]


async def test_removing_an_account_that_does_not_exist_deletes_no_credentials(
    handler, config, tmp_path, monkeypatch
):
    """An email alone does not identify a credential file, so a removal that matched
    nothing must not delete anything."""
    from cloud_drive_sync.util import paths

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    victim = paths.account_credentials_path("alice@example.com")
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"still needed")

    await call(handler, "remove_account", {"email": "alice@example.com", "provider": "onedrive"})

    assert victim.exists(), "a no-op removal deleted a live credential file"


# ── Per-pair settings ───────────────────────────────────────────────────


async def test_setting_a_sync_mode_persists(handler, config_file):
    await call(handler, "set_sync_mode", {"pair_id": "0", "sync_mode": "download_only"})

    assert reload(config_file).sync.pairs[0].sync_mode == "download_only"


async def test_an_invalid_sync_mode_is_refused(handler, config, config_file):
    await expect_error(handler, "set_sync_mode", {"pair_id": "0", "sync_mode": "sideways"})

    assert config.sync.pairs[0].sync_mode == "two_way"
    assert not config_file.exists()


async def test_ignore_hidden_persists(handler, config_file):
    await call(handler, "set_ignore_hidden", {"pair_id": "1", "ignore_hidden": False})

    assert reload(config_file).sync.pairs[1].ignore_hidden is False


async def test_ignore_patterns_persist_as_a_list(handler, config_file):
    patterns = ["*.tmp", "node_modules/", ".DS_Store"]

    await call(handler, "set_ignore_patterns", {"pair_id": "0", "patterns": patterns})

    assert reload(config_file).sync.pairs[0].ignore_patterns == patterns


async def test_ignore_patterns_reject_a_non_list(handler, config_file):
    """A bare string would otherwise be stored and then iterated character by
    character, silently excluding files matching single letters."""
    await expect_error(handler, "set_ignore_patterns", {"pair_id": "0", "patterns": "*.tmp"})

    assert not config_file.exists()


async def test_clearing_ignore_patterns_persists_the_empty_list(handler, config_file):
    await call(handler, "set_ignore_patterns", {"pair_id": "0", "patterns": ["*.tmp"]})

    await call(handler, "set_ignore_patterns", {"pair_id": "0", "patterns": []})

    assert reload(config_file).sync.pairs[0].ignore_patterns == []


async def test_a_per_pair_conflict_strategy_persists(handler, config_file):
    await call(
        handler, "set_pair_conflict_strategy", {"pair_id": "0", "strategy": "newest_wins"}
    )

    assert reload(config_file).sync.pairs[0].conflict_strategy == "newest_wins"


async def test_an_empty_pair_strategy_restores_inheritance(handler, config_file):
    await call(handler, "set_pair_conflict_strategy", {"pair_id": "0", "strategy": "local_wins"})

    await call(handler, "set_pair_conflict_strategy", {"pair_id": "0", "strategy": ""})

    saved = reload(config_file)
    assert not saved.sync.pairs[0].conflict_strategy


# ── Global settings ─────────────────────────────────────────────────────


async def test_the_global_conflict_strategy_persists(handler, config_file):
    await call(handler, "set_conflict_strategy", {"strategy": "remote_wins"})

    assert reload(config_file).sync.conflict_strategy == "remote_wins"


async def test_an_invalid_conflict_strategy_is_refused(handler, config, config_file):
    await expect_error(handler, "set_conflict_strategy", {"strategy": "coin_flip"})

    assert config.sync.conflict_strategy == "keep_both"
    assert not config_file.exists()


async def test_bandwidth_limits_persist_and_read_back(handler, config_file):
    await call(
        handler, "set_bandwidth_limits", {"max_upload_kbps": 512, "max_download_kbps": 2048}
    )

    saved = reload(config_file)
    assert saved.sync.max_upload_kbps == 512
    assert saved.sync.max_download_kbps == 2048
    got = await call(handler, "get_bandwidth_limits")
    assert got["max_upload_kbps"] == 512


async def test_zero_bandwidth_means_unlimited_and_survives_a_reload(handler, config_file):
    """0 is "no limit" here. If the serialiser dropped it as falsy, a user who
    removed a limit would find it back after restarting."""
    await call(handler, "set_bandwidth_limits", {"max_upload_kbps": 100})
    await call(handler, "set_bandwidth_limits", {"max_upload_kbps": 0})

    assert reload(config_file).sync.max_upload_kbps == 0


async def test_notification_preferences_persist(handler, config_file):
    await call(
        handler,
        "set_notification_prefs",
        {"notify_sync_complete": False, "notify_conflicts": False, "notify_errors": True},
    )

    saved = reload(config_file)
    assert saved.sync.notify_sync_complete is False
    assert saved.sync.notify_conflicts is False
    assert saved.sync.notify_errors is True


async def test_a_partial_notification_update_leaves_the_rest_alone(handler, config_file):
    await call(handler, "set_notification_prefs", {"notify_errors": False})

    saved = reload(config_file)
    assert saved.sync.notify_errors is False
    assert saved.sync.notify_sync_complete is True, "an unrelated preference was reset"


async def test_proxy_settings_persist(handler, config_file):
    await call(
        handler,
        "set_proxy",
        {
            "http_proxy": "http://proxy.example:3128",
            "https_proxy": "http://proxy.example:3128",
            "no_proxy": "localhost,127.0.0.1",
        },
    )

    saved = reload(config_file)
    assert saved.proxy.http_proxy == "http://proxy.example:3128"
    assert saved.proxy.no_proxy == "localhost,127.0.0.1"


async def test_clearing_a_proxy_persists_the_empty_value(handler, config_file):
    await call(handler, "set_proxy", {"http_proxy": "http://proxy.example:3128"})

    await call(handler, "set_proxy", {"http_proxy": ""})

    assert reload(config_file).proxy.http_proxy == ""


async def test_sync_rules_persist(handler, config_file):
    await call(
        handler,
        "set_sync_rules",
        {"pair_id": "0", "rules": {"max_file_size_mb": 50, "exclude_regex": r".*\.iso$"}},
    )

    rules = reload(config_file).sync.pairs[0].sync_rules
    assert rules.max_file_size_mb == 50
    assert rules.exclude_regex == r".*\.iso$"


async def test_an_account_transfer_limit_persists(handler, config_file):
    await call(
        handler,
        "set_account_max_transfers",
        {"email": "alice@example.com", "max_concurrent_transfers": 2},
    )

    saved = reload(config_file)
    alice = next(a for a in saved.accounts if a.email == "alice@example.com")
    assert alice.max_concurrent_transfers == 2


async def test_a_transfer_limit_for_an_unknown_account_is_refused(handler, config_file):
    await expect_error(
        handler,
        "set_account_max_transfers",
        {"email": "nobody@example.com", "max_concurrent_transfers": 2},
    )

    assert not config_file.exists()


# ── Dispatch ────────────────────────────────────────────────────────────


async def test_an_unknown_method_is_reported_not_swallowed(handler):
    error = await expect_error(handler, "no_such_method")

    assert "no_such_method" in str(error) or error.get("code") is not None


async def test_a_read_only_call_writes_nothing(handler, config_file):
    """Reads must not persist. A getter that saved would rewrite the config on every
    UI poll, and any hand edit would be lost on the next refresh."""
    await call(handler, "get_max_deletions")
    await call(handler, "get_sync_pairs")
    await call(handler, "get_bandwidth_limits")
    await call(handler, "get_notification_prefs")

    assert not config_file.exists(), "a getter wrote to the config file"


# ── The guard on this file's own isolation ──────────────────────────────


def test_a_bare_config_would_target_the_real_user_config():
    """Why every fixture here sets ``_source_path``.

    ``Config()`` leaves it ``None``, and ``save()`` then falls back to the real
    per-user path. Fifteen handlers call ``save()``. If this ever stops being true
    the fixture is redundant; while it is true, a fixture that forgot would point
    this entire file at the user's actual configuration.
    """
    from cloud_drive_sync.util.paths import config_path

    assert Config()._source_path is None
    assert (Config()._source_path or config_path()) == config_path()


async def test_the_fixture_writes_only_inside_the_temp_directory(handler, config_file, tmp_path):
    await call(handler, "set_max_deletions", {"max_deletions_per_sync": 3})

    assert config_file.exists()
    assert tmp_path in config_file.parents


# ── Pairs are addressed positionally, and 0 is falsy ────────────────────
#
# Every handler that chooses between "this pair" and "all pairs" used `if pair_id:`.
# The CLI always sends strings, so "0" is truthy and it reads correctly — but a
# hand-written HTTP client can send JSON `{"pair_id": 0}`, and the integer 0 is not.
# `PUT /api/settings/max-deletions {"max_deletions_per_sync": 0, "pair_id": 0}`
# therefore switched delete protection off GLOBALLY while appearing to target one
# pair: the opposite of the request, in the dangerous direction.


async def test_a_numeric_pair_id_targets_that_pair_not_every_pair(handler, config, config_file):
    """The finding, at its most consequential: disabling the guard for pair 0."""
    await call(handler, "set_max_deletions", {"max_deletions_per_sync": 0, "pair_id": 0})

    saved = reload(config_file)
    assert saved.sync.max_deletions_per_sync == 100, (
        "a numeric pair_id of 0 disabled delete protection globally"
    )
    assert saved.sync.pairs[0].max_deletions_per_sync == 0


async def test_a_numeric_and_a_string_pair_id_behave_identically(handler, config_file):
    await call(handler, "set_max_deletions", {"max_deletions_per_sync": 5, "pair_id": 1})
    numeric = reload(config_file).sync.pairs[1].max_deletions_per_sync

    await call(handler, "set_max_deletions", {"max_deletions_per_sync": 7, "pair_id": "1"})
    string = reload(config_file).sync.pairs[1].max_deletions_per_sync

    assert (numeric, string) == (5, 7)


async def test_omitting_the_pair_id_still_means_all_pairs(handler, config_file):
    """The other half — normalising must not turn "unspecified" into pair 0."""
    await call(handler, "set_max_deletions", {"max_deletions_per_sync": 42})

    saved = reload(config_file)
    assert saved.sync.max_deletions_per_sync == 42
    assert saved.sync.pairs[0].max_deletions_per_sync is None


async def test_a_blank_pair_id_is_treated_as_unspecified(handler, config_file):
    await call(handler, "set_max_deletions", {"max_deletions_per_sync": 9, "pair_id": ""})

    assert reload(config_file).sync.max_deletions_per_sync == 9


async def test_pair_zero_can_be_removed_by_numeric_id(handler, config_file):
    """`params.get("id") or params.get("index")` treated a numeric 0 as absent, so
    removing pair 0 by numeric id failed outright."""
    result = await call(handler, "remove_sync_pair", {"id": 0})

    assert result["local_path"] == "/tmp/first"
    assert [p.local_path for p in reload(config_file).sync.pairs] == ["/tmp/second"]


# ── A refused deletion block must not outlive its pair ───────────────────


async def test_removing_a_pair_discards_its_refused_deletion_block(handler, db, config_file):
    """Pair ids are positional, so removal renumbers the survivors.

    A refused deletion batch is stored deliberately — so restarting cannot silently
    resolve a safety question — and neither `clear_pair` nor `cleanup_stale_pairs`
    touches it. So the block stayed behind under `pair_0`, the next pair inherited that
    id, and approving granted a delete-protection bypass for a folder the user had
    never been asked about.
    """
    await db.record_pending_deletions(
        "pair_0", "local", 4200, 5000, 100, ["/tmp/first/a", "/tmp/first/b"]
    )

    await call(handler, "remove_sync_pair", {"id": "0"})

    assert await db.get_pending_deletions("pair_0") == [], (
        "the removed pair's block was inherited by the pair that took its id"
    )


async def test_approving_a_block_from_another_folder_is_refused(handler, db, config_file):
    """Defence in depth for the same bug: if a stale block exists by any route,
    approving it must not grant a bypass to a different folder."""
    # A block whose sample paths sit outside pair 1's folder.
    await db.record_pending_deletions(
        "pair_1", "local", 9, 9, 1, ["/tmp/somewhere-else/x"]
    )

    result = await call(handler, "resolve_pending_deletions", {"pair_id": "1", "approve": True})

    assert result["status"] == "stale"
    assert await db.get_pending_deletions("pair_1") == [], "the stale block was left in place"


async def test_approving_a_matching_block_still_works(handler, db):
    """The guard must not strand a legitimate block — that would leave a pair paused
    with no way to release it."""
    await db.record_pending_deletions(
        "pair_1", "local", 200, 400, 100, ["/tmp/second/one", "/tmp/second/two"]
    )

    result = await call(handler, "resolve_pending_deletions", {"pair_id": "1", "approve": True})

    assert result["status"] == "approved"
    assert result["batches"] == 1


async def test_a_remote_direction_block_is_not_judged_against_local_paths(handler, db):
    """Remote samples are remote paths, so comparing them to `local_path` would
    reject every remote-direction block."""
    await db.record_pending_deletions(
        "pair_1", "remote", 300, 400, 100, ["/Documents/report.pdf"]
    )

    result = await call(handler, "resolve_pending_deletions", {"pair_id": "1", "approve": True})

    assert result["status"] == "approved"


# ── Removing an account must not take out another provider's ─────────────


async def test_an_ambiguous_account_removal_is_refused(handler, config):
    """One address can hold accounts on several providers. Removing "all of them"
    because only an email was given is not a reasonable reading of the request — and
    is how a Dropbox removal used to take out Google Drive."""
    config.accounts = [
        Account(email="dup@example.com", provider="gdrive"),
        Account(email="dup@example.com", provider="dropbox"),
    ]

    error = await expect_error(handler, "remove_account", {"email": "dup@example.com"})

    assert "gdrive" in str(error) and "dropbox" in str(error), "the error should name the candidates"
    assert len(config.accounts) == 2, "an ambiguous request removed something anyway"


async def test_an_unambiguous_account_removal_still_needs_no_provider(handler, config):
    """Only ambiguity is refused; the common single-provider case is unchanged."""
    await call(handler, "remove_account", {"email": "bob@example.com"})

    assert [a.email for a in config.accounts] == ["alice@example.com"]


async def test_removing_an_account_unbinds_its_pairs_instead_of_deleting_them(
    handler, config, config_file
):
    """docs/CLI.md promises a pair "will lose its account binding and stop syncing
    until reassigned". The handler deleted it instead — discarding the local path,
    sync mode, ignore patterns and rules, unrecoverably, as a side effect of removing
    an account. Reassigning is only possible if the pair still exists.
    """
    config.sync.pairs = [
        SyncPair(
            local_path=f"{ABS}/Docs",
            remote_folder_id="folder_1",
            account_id="bob@example.com",
            provider="dropbox",
            sync_mode="upload_only",
            ignore_patterns=["*.tmp"],
        )
    ]

    await call(handler, "remove_account", {"email": "bob@example.com"})

    saved = reload(config_file)
    assert len(saved.sync.pairs) == 1, "the pair was deleted along with the account"
    pair = saved.sync.pairs[0]
    assert pair.account_id == "", "the pair is still bound to a removed account"
    assert pair.enabled is False, "an unbound pair should not stay enabled"
    # The configuration the user built is still there to reassign.
    assert pair.local_path == f"{ABS}/Docs"
    assert pair.sync_mode == "upload_only"
    assert pair.ignore_patterns == ["*.tmp"]


@posix_config_modes
async def test_the_config_file_is_not_readable_by_other_users(handler, config_file):
    """Since v2.4.3 this file can hold the web UI access token — the credential for
    the whole API, granting account changes and switching off delete protection. It
    was being written at the umask default (0644) beside credential files that are
    0600, so any local account could read it.
    """
    await call(handler, "set_max_deletions", {"max_deletions_per_sync": 7})

    mode = config_file.stat().st_mode & 0o777
    assert mode == 0o600, f"config.toml is {oct(mode)}; it can contain the access token"


@posix_config_modes
def test_saving_over_a_loose_config_tightens_it(tmp_path):
    """Anyone who ran an earlier version already has a 0644 config on disk, so saving
    has to repair the mode rather than preserve it."""
    path = tmp_path / "config.toml"
    path.write_text("[general]\n")
    path.chmod(0o644)

    cfg = Config.load(path)
    cfg.save(path)

    assert path.stat().st_mode & 0o777 == 0o600


async def test_a_direct_handler_call_with_a_numeric_pair_id_is_still_safe(handler, config_file):
    """Dispatch normalises pair_id, so `if pair_id:` inside the handler happens to be
    safe when called through `handle()`. This calls the method directly — as tests, and
    any future front-end that skips dispatch, would — to prove the handler's own
    `is not None` guard is doing the work rather than relying on the normaliser.
    """
    await handler._set_max_deletions({"max_deletions_per_sync": 0, "pair_id": 0})

    saved = reload(config_file)
    assert saved.sync.max_deletions_per_sync == 100, (
        "bypassing dispatch let a numeric 0 disable delete protection globally"
    )
    assert saved.sync.pairs[0].max_deletions_per_sync == 0


# ── Pair ids the CLI prints must be the ids the engine understands ───────
#
# `pair list` prints "0", "1"; the engine keys pairs as "pair_0". The pause, resume
# and sync handlers passed the client form straight through, so `pause 0` looked up a
# pair named "0", found nothing, and returned not_found — which the CLI then reported
# as success. The id was correct, the command did nothing, and nothing said so.


class _EngineSpy:
    """Records what the engine was asked to do, with no real sync machinery."""

    #: `_repair` reaches into the engine for the provider client of a pair, so the spy
    #: has to carry both attribute names it looks for.
    _clients: ClassVar[dict] = {}
    _client = None

    def __init__(self, known=("pair_0", "pair_1")) -> None:
        self.known = set(known)
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.synced: list[str] = []
        self.pause_all_calls = 0
        self.resume_all_calls = 0
        self.sync_all_calls = 0

    async def pause_pair(self, pair_id):
        self.paused.append(pair_id)
        return pair_id in self.known

    async def resume_pair(self, pair_id):
        self.resumed.append(pair_id)
        return pair_id in self.known

    async def force_sync(self, pair_id):
        self.synced.append(pair_id)
        return pair_id in self.known

    async def pause_all(self):
        self.pause_all_calls += 1
        return len(self.known)

    async def resume_all(self):
        self.resume_all_calls += 1
        return len(self.known)

    async def force_sync_all(self):
        self.sync_all_calls += 1


@pytest.fixture
def handler_with_engine(config: Config, db: Database):
    spy = _EngineSpy()
    h = RequestHandler(engine=spy, config=config)
    h.set_db(db)
    return h, spy


@pytest.mark.parametrize(
    ("method", "attr"),
    [("pause_sync", "paused"), ("resume_sync", "resumed"), ("force_sync", "synced")],
)
async def test_the_client_pair_id_is_translated_for_the_engine(handler_with_engine, method, attr):
    handler, spy = handler_with_engine

    result = await call(handler, method, {"pair_id": "0"})

    assert getattr(spy, attr) == ["pair_0"], (
        f"{method} passed the client id through unnormalised, so it matched nothing"
    )
    assert result["status"] != "not_found"


@pytest.mark.parametrize("method", ["pause_sync", "resume_sync", "force_sync"])
async def test_the_engine_form_is_also_accepted(handler_with_engine, method):
    """Callers that already hold "pair_0" must not be rejected."""
    handler, _ = handler_with_engine

    result = await call(handler, method, {"pair_id": "pair_1"})

    assert result["status"] != "not_found"


@pytest.mark.parametrize("method", ["pause_sync", "resume_sync", "force_sync"])
async def test_an_unknown_pair_is_reported_as_not_found(handler_with_engine, method):
    handler, _ = handler_with_engine

    result = await call(handler, method, {"pair_id": "9"})

    assert result["status"] == "not_found"


async def test_pausing_with_no_id_pauses_every_pair(handler_with_engine):
    """It used to pause only the first pair while the output, the docs and the web UI's
    global toggle all say every pair — so two of three pairs kept syncing."""
    handler, spy = handler_with_engine

    result = await call(handler, "pause_sync", {})

    assert spy.pause_all_calls == 1
    assert spy.paused == [], "a single pair was paused instead of all of them"
    assert result["pairs"] == 2


async def test_resuming_with_no_id_resumes_every_pair(handler_with_engine):
    handler, spy = handler_with_engine

    await call(handler, "resume_sync", {})

    assert spy.resume_all_calls == 1
    assert spy.resumed == []


# ── repair must not report health after examining nothing ────────────────


async def test_repair_rejects_a_pair_id_that_matches_nothing(handler_with_engine):
    """It used to scan zero pairs and report "everything looks healthy" — the most
    misleading answer a repair tool can give."""
    handler, _ = handler_with_engine

    await expect_error(handler, "repair", {"pair_id": "9"})


async def test_repair_accepts_both_id_forms(handler_with_engine):
    handler, _ = handler_with_engine

    for form in ("0", "pair_0"):
        result = await call(handler, "repair", {"pair_id": form, "dry_run": True})
        assert "pairs_scanned" in result


# ── pair add must not accept an account that does not exist ─────────────


async def test_adding_a_pair_with_an_unknown_account_is_refused(handler, config_file):
    """It used to be accepted, persisted, reported as success — and then silently
    discarded by the next `pair list`, so a mistyped address looked like it worked and
    the pair simply vanished."""
    error = await expect_error(
        handler,
        "add_sync_pair",
        {"local_path": f"{ABS}/New", "remote_folder_id": "root",
         "account_id": "typo@example.com"},
    )

    assert "typo@example.com" in str(error)
    assert "alice@example.com" in str(error), "the error should name the known accounts"


async def test_adding_a_pair_with_a_known_account_works(handler, config_file):
    await call(
        handler,
        "add_sync_pair",
        {"local_path": f"{ABS}/New", "remote_folder_id": "root",
         "account_id": "alice@example.com"},
    )

    saved = reload(config_file)
    assert any(p.local_path == f"{ABS}/New" for p in saved.sync.pairs)


async def test_adding_a_pair_with_no_account_is_still_allowed(handler, config_file):
    """An unbound pair is a legitimate state — it is what removing an account leaves
    behind, and it must be possible to create one and assign it later."""
    await call(handler, "add_sync_pair", {"local_path": f"{ABS}/Later", "remote_folder_id": "root"})

    assert any(p.local_path == f"{ABS}/Later" for p in reload(config_file).sync.pairs)


# ── Listing pairs is a read and must not destroy configuration ──────────


async def test_listing_pairs_unbinds_an_orphan_rather_than_deleting_it(handler, config, config_file):
    """`get_sync_pairs` — a *listing* — used to delete pairs whose account was gone,
    taking the local path, sync mode, ignore patterns and rules with them. A read must
    not discard configuration the user cannot get back.
    """
    config.sync.pairs = [
        SyncPair(
            local_path=f"{ABS}/Orphan",
            remote_folder_id="folder_x",
            account_id="deleted@example.com",
            sync_mode="download_only",
            ignore_patterns=["*.bak"],
        )
    ]

    listed = await call(handler, "get_sync_pairs")

    assert len(listed) == 1, "listing the pairs deleted one"
    saved = reload(config_file)
    assert saved.sync.pairs[0].local_path == f"{ABS}/Orphan"
    assert saved.sync.pairs[0].account_id == ""
    assert saved.sync.pairs[0].enabled is False
    assert saved.sync.pairs[0].ignore_patterns == ["*.bak"], "the pair's settings were lost"
