"""Tests for multiple Google accounts support."""

from __future__ import annotations



from cloud_drive_sync.config import Account, Config, SyncPair


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
