"""Tests for multiple Google accounts support."""

from __future__ import annotations

import pytest

from cloud_drive_sync.config import Account, Config, SyncConfig, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.drive.mock_client import MockChangePoller, MockDriveClient, MockFileOperations
from cloud_drive_sync.sync.engine import SyncEngine


class TestAccountDataclass:
    def test_default_values(self):
        acct = Account()
        assert acct.email == ""
        assert acct.display_name == ""

    def test_with_values(self):
        acct = Account(email="user@example.com", display_name="Test User")
        assert acct.email == "user@example.com"
        assert acct.display_name == "Test User"


class TestSyncPairAccountId:
    def test_default_account_id(self):
        pair = SyncPair()
        assert pair.account_id == ""

    def test_with_account_id(self):
        pair = SyncPair(account_id="user@example.com")
        assert pair.account_id == "user@example.com"


class TestAccountCredentialsPath:
    def test_path_sanitization(self):
        from cloud_drive_sync.util.paths import account_credentials_path

        path = account_credentials_path("user@example.com")
        assert "user_at_example_com" in path.name
        assert path.name.startswith("credentials-")
        assert path.name.endswith(".enc")

    def test_different_accounts_different_paths(self):
        from cloud_drive_sync.util.paths import account_credentials_path

        path1 = account_credentials_path("alice@example.com")
        path2 = account_credentials_path("bob@example.com")
        assert path1 != path2


class TestConfigWithAccounts:
    def test_save_load_accounts(self, tmp_path):
        cfg = Config()
        cfg.accounts.append(Account(email="alice@gmail.com", display_name="Alice"))
        cfg.accounts.append(Account(email="bob@work.com", display_name="Bob"))
        cfg.sync.pairs.append(
            SyncPair(
                local_path="/tmp/test",
                remote_folder_id="root",
                account_id="alice@gmail.com",
            )
        )

        config_file = tmp_path / "config.toml"
        cfg.save(config_file)

        loaded = Config.load(config_file)
        assert len(loaded.accounts) == 2
        assert loaded.accounts[0].email == "alice@gmail.com"
        assert loaded.accounts[0].display_name == "Alice"
        assert loaded.accounts[1].email == "bob@work.com"
        assert len(loaded.sync.pairs) == 1
        assert loaded.sync.pairs[0].account_id == "alice@gmail.com"

    def test_load_config_without_accounts(self, tmp_path):
        """Config files without accounts section should load fine."""
        cfg = Config()
        cfg.sync.pairs.append(SyncPair(local_path="/tmp/test"))

        config_file = tmp_path / "config.toml"
        cfg.save(config_file)

        loaded = Config.load(config_file)
        # The saved config will have empty accounts list serialized
        assert len(loaded.sync.pairs) == 1
        assert loaded.sync.pairs[0].account_id == ""

    def test_empty_account_id_default(self, tmp_path):
        """Pairs without account_id should default to empty string."""
        cfg = Config()
        cfg.sync.pairs.append(SyncPair(local_path="/tmp/test"))

        config_file = tmp_path / "config.toml"
        cfg.save(config_file)

        loaded = Config.load(config_file)
        assert loaded.sync.pairs[0].account_id == ""


class TestSameEmailDifferentProvider:
    """Regression tests for issue #12: same email, two providers."""

    @pytest.mark.asyncio
    async def test_nextcloud_pair_uses_nextcloud_client(self, tmp_path):
        """A pair with provider='nextcloud' must resolve to the Nextcloud client,
        not the GDrive client, even when both share the same email."""
        email = "shared@example.com"

        cfg = Config()
        cfg.accounts = [
            Account(email=email, provider="gdrive"),
            Account(email=email, provider="nextcloud"),
        ]
        cfg.sync = SyncConfig(pairs=[
            SyncPair(
                local_path=str(tmp_path / "local"),
                remote_folder_id="root",
                account_id=email,
                provider="nextcloud",
            )
        ])

        db = Database(tmp_path / "test.db")
        await db.open()

        gdrive_client = MockDriveClient(tmp_path / "remote_gdrive")
        nextcloud_client = MockDriveClient(tmp_path / "remote_nextcloud")
        clients = {
            f"gdrive:{email}": gdrive_client,
            f"nextcloud:{email}": nextcloud_client,
        }

        ops = MockFileOperations(nextcloud_client)
        poller = MockChangePoller(nextcloud_client)
        engine = SyncEngine(cfg, db, clients=clients, file_ops=ops, change_poller=poller)

        pair = cfg.sync.pairs[0]
        (tmp_path / "local").mkdir(parents=True, exist_ok=True)
        await engine._start_pair(pair, "pair_0")

        assert "pair_0" in engine.pairs
        resolved_client = engine.pairs["pair_0"].executor._drive_client
        assert resolved_client is nextcloud_client, (
            "Expected Nextcloud client but got GDrive client — "
            "provider disambiguation by (email, provider) is broken"
        )

        await engine.stop()
        await db.close()

    @pytest.mark.asyncio
    async def test_gdrive_pair_uses_gdrive_client(self, tmp_path):
        """A pair with provider='gdrive' must resolve to the GDrive client."""
        email = "shared@example.com"

        cfg = Config()
        cfg.accounts = [
            Account(email=email, provider="gdrive"),
            Account(email=email, provider="nextcloud"),
        ]
        cfg.sync = SyncConfig(pairs=[
            SyncPair(
                local_path=str(tmp_path / "local"),
                remote_folder_id="root",
                account_id=email,
                provider="gdrive",
            )
        ])

        db = Database(tmp_path / "test.db")
        await db.open()

        gdrive_client = MockDriveClient(tmp_path / "remote_gdrive")
        nextcloud_client = MockDriveClient(tmp_path / "remote_nextcloud")
        clients = {
            f"gdrive:{email}": gdrive_client,
            f"nextcloud:{email}": nextcloud_client,
        }

        ops = MockFileOperations(gdrive_client)
        poller = MockChangePoller(gdrive_client)
        engine = SyncEngine(cfg, db, clients=clients, file_ops=ops, change_poller=poller)

        pair = cfg.sync.pairs[0]
        (tmp_path / "local").mkdir(parents=True, exist_ok=True)
        await engine._start_pair(pair, "pair_0")

        assert "pair_0" in engine.pairs
        resolved_client = engine.pairs["pair_0"].executor._drive_client
        assert resolved_client is gdrive_client

        await engine.stop()
        await db.close()
