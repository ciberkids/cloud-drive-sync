"""Tests for the stable per-pair ``uid``.

Pairs are identified internally by their position in the config list -- ``pair_N``,
derived from the list index at load time (roadmap item 9). That is survivable inside
the process, because removing a pair renumbers the stored database rows to match. It
is *not* survivable in an outbound payload: a webhook receiver keys its own state on
whatever identifier we send, on a system we do not control and cannot migrate, so
``pair_2`` silently coming to mean a different folder is a bug we would be exporting.

``uid`` is that stable identity. Three properties matter and each has a test here:

* it is **minted** for new pairs, so two pairs can never collide;
* it is **derived deterministically** for pairs that predate the field, so a restart
  hands the receiver the same identity rather than a fresh one;
* deriving it **must not write the config**, because ``Config.load`` not creating a
  file is what first-run detection depends on (see ``test_feature_first_run_token``).

The last one is the trap. "Mint on load and save" is the obvious implementation and it
would silently switch off authentication-on-by-default for every new install.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from cloud_drive_sync.config import Config, SyncPair, derive_pair_uid

LEGACY_CONFIG = """
[sync]
poll_interval = 30

[[sync.pairs]]
local_path = "/home/me/Documents"
remote_folder_id = "abc123"
account_id = "me@example.com"
provider = "gdrive"
"""


@pytest.fixture
def legacy_config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(LEGACY_CONFIG)
    return path


class TestDerivationForLegacyPairs:
    def test_a_pair_without_a_uid_gets_one(self, legacy_config_file: Path):
        cfg = Config.load(legacy_config_file)
        assert cfg.sync.pairs[0].uid, "a pair with no stored uid should be given one"

    def test_the_derived_uid_is_stable_across_loads(self, legacy_config_file: Path):
        """The whole point. A random uuid per load would give the receiver a new
        identity after every daemon restart, which is worse than no identity."""
        first = Config.load(legacy_config_file).sync.pairs[0].uid
        second = Config.load(legacy_config_file).sync.pairs[0].uid
        assert first == second

    def test_the_derived_uid_matches_the_public_helper(self, legacy_config_file: Path):
        cfg = Config.load(legacy_config_file)
        assert cfg.sync.pairs[0].uid == derive_pair_uid(
            provider="gdrive",
            account_id="me@example.com",
            local_path="/home/me/Documents",
            remote_folder_id="abc123",
        )

    def test_distinct_pairs_derive_distinct_uids(self):
        a = derive_pair_uid(
            provider="gdrive", account_id="me@example.com",
            local_path="/one", remote_folder_id="root",
        )
        b = derive_pair_uid(
            provider="gdrive", account_id="me@example.com",
            local_path="/two", remote_folder_id="root",
        )
        assert a != b

    def test_the_derived_value_is_a_real_uuid(self, legacy_config_file: Path):
        cfg = Config.load(legacy_config_file)
        uuid.UUID(cfg.sync.pairs[0].uid)  # raises if it is not


class TestLoadDoesNotWrite:
    def test_deriving_a_uid_does_not_touch_the_file(self, legacy_config_file: Path):
        before = legacy_config_file.read_text()
        Config.load(legacy_config_file)
        assert legacy_config_file.read_text() == before, (
            "load() must not persist the derived uid -- it must not write at all"
        )

    def test_load_still_does_not_create_an_absent_file(self, tmp_path: Path):
        """Guards the same invariant test_feature_first_run_token pins, from this
        side: the uid backfill runs over an empty pair list and must stay inert."""
        missing = tmp_path / "does-not-exist.toml"
        Config.load(missing)
        assert not missing.exists()


class TestPersistence:
    def test_save_persists_the_derived_uid(self, legacy_config_file: Path):
        cfg = Config.load(legacy_config_file)
        expected = cfg.sync.pairs[0].uid
        cfg.save()
        assert f'uid = "{expected}"' in legacy_config_file.read_text()

    def test_a_stored_uid_is_read_back_verbatim(self, legacy_config_file: Path):
        cfg = Config.load(legacy_config_file)
        expected = cfg.sync.pairs[0].uid
        cfg.save()
        assert Config.load(legacy_config_file).sync.pairs[0].uid == expected

    def test_once_persisted_the_uid_survives_editing_its_derivation_inputs(
        self, legacy_config_file: Path
    ):
        """Derivation is a one-time bridge, not an ongoing function of these fields.

        If it stayed a function of local_path, moving a synced folder would fork the
        receiver's history -- the exact failure the stable id exists to prevent.
        """
        cfg = Config.load(legacy_config_file)
        original = cfg.sync.pairs[0].uid
        cfg.save()

        cfg = Config.load(legacy_config_file)
        cfg.sync.pairs[0].local_path = "/somewhere/entirely/else"
        cfg.sync.pairs[0].account_id = "other@example.com"
        cfg.save()

        assert Config.load(legacy_config_file).sync.pairs[0].uid == original

    def test_round_trip_preserves_uid_alongside_the_falsy_tri_states(self, tmp_path: Path):
        """A regression guard for the marshalling: `uid` is saved on truthiness while
        the delete-failsafe limits are saved on `is not None`. Mixing those up drops
        an explicit 0 and silently re-inherits the global limit."""
        path = tmp_path / "config.toml"
        cfg = Config()
        cfg._source_path = path
        cfg.sync.pairs = [
            SyncPair(
                local_path="/data",
                uid="11111111-2222-3333-4444-555555555555",
                max_deletions_per_sync=0,
                deletion_window_seconds=0,
            )
        ]
        cfg.save()

        pair = Config.load(path).sync.pairs[0]
        assert pair.uid == "11111111-2222-3333-4444-555555555555"
        assert pair.max_deletions_per_sync == 0
        assert pair.deletion_window_seconds == 0


class TestMintingOnCreation:
    @pytest.mark.asyncio
    async def test_add_sync_pair_mints_a_unique_uid(self, tmp_path: Path):
        """New pairs are minted rather than derived, so two pairs that differ only by
        a field later edited to match can never end up sharing an identity."""
        from cloud_drive_sync.ipc.handlers import RequestHandler

        cfg = Config()
        cfg._source_path = tmp_path / "config.toml"
        handler = RequestHandler(None, cfg)

        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        await handler._add_sync_pair({"local_path": str(tmp_path / "a")})
        await handler._add_sync_pair({"local_path": str(tmp_path / "b")})

        uids = [p.uid for p in cfg.sync.pairs]
        assert all(uids), "every new pair should be given a uid"
        assert len(set(uids)) == 2, "minted uids must be unique"
        for u in uids:
            uuid.UUID(u)

    @pytest.mark.asyncio
    async def test_get_sync_pairs_exposes_uid_without_redefining_id(self, tmp_path: Path):
        """`id` must keep meaning the positional index: every front-end already joins
        on it, and the engine and database still key on `pair_N`."""
        from cloud_drive_sync.ipc.handlers import RequestHandler

        cfg = Config()
        cfg._source_path = tmp_path / "config.toml"
        (tmp_path / "a").mkdir()
        cfg.sync.pairs = [SyncPair(local_path=str(tmp_path / "a"), uid="dead-beef")]
        handler = RequestHandler(None, cfg)

        rows = await handler._get_sync_pairs({})
        assert rows[0]["id"] == "0"
        assert rows[0]["uid"] == "dead-beef"
