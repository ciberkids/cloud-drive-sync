"""Tests for provider registration and non-OAuth auth flows."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestProviderRegistry:
    def test_gdrive_registered(self):
        import cloud_drive_sync.providers.gdrive  # noqa: F401
        from cloud_drive_sync.providers.registry import get
        entry = get("gdrive")
        assert entry.name == "gdrive"
        assert entry.available is True

    def test_nextcloud_registered_after_import(self):
        import cloud_drive_sync.providers.nextcloud  # noqa: F401
        from cloud_drive_sync.providers.registry import get
        entry = get("nextcloud")
        assert entry.name == "nextcloud"
        # available depends on whether nc-py-api is installed — just check it's registered
        assert entry.auth_cls is not None

    def test_dropbox_registered_after_import(self):
        import cloud_drive_sync.providers.dropbox  # noqa: F401
        from cloud_drive_sync.providers.registry import get
        entry = get("dropbox")
        assert entry.name == "dropbox"

    def test_onedrive_registered_after_import(self):
        import cloud_drive_sync.providers.onedrive  # noqa: F401
        from cloud_drive_sync.providers.registry import get
        entry = get("onedrive")
        assert entry.name == "onedrive"

    def test_box_registered_after_import(self):
        import cloud_drive_sync.providers.box  # noqa: F401
        from cloud_drive_sync.providers.registry import get
        entry = get("box")
        assert entry.name == "box"

    def test_unknown_provider_raises_key_error(self):
        from cloud_drive_sync.providers.registry import get
        with pytest.raises(KeyError, match="Unknown provider"):
            get("does_not_exist")

    def test_error_message_lists_available_providers(self):
        import cloud_drive_sync.providers.gdrive  # noqa: F401
        from cloud_drive_sync.providers.registry import get
        with pytest.raises(KeyError) as exc_info:
            get("bogus")
        assert "gdrive" in str(exc_info.value)


# ---------------------------------------------------------------------------
# NextcloudAuth.run_auth_flow
# ---------------------------------------------------------------------------

class TestNextcloudAuthFlow:
    def _auth(self):
        from cloud_drive_sync.providers.nextcloud.auth import NextcloudAuth
        return NextcloudAuth()

    def test_uses_extra_credentials_directly(self):
        import sys
        from unittest.mock import MagicMock, patch

        auth = self._auth()
        extra = {
            "server_url": "https://cloud.example.com",
            "username": "alice",
            "app_password": "secret123",
        }
        mock_user = MagicMock(display_name="Alice")
        mock_nc_instance = MagicMock()
        mock_nc_instance.users.get_user.return_value = mock_user
        mock_nc_cls = MagicMock(return_value=mock_nc_instance)
        mock_nc_py_api = MagicMock()
        mock_nc_py_api.Nextcloud = mock_nc_cls

        with patch.dict(sys.modules, {"nc_py_api": mock_nc_py_api}):
            result = auth.run_auth_flow(extra=extra)

        assert result["server_url"] == "https://cloud.example.com"
        assert result["username"] == "alice"
        assert result["app_password"] == "secret123"
        mock_nc_cls.assert_called_once_with(
            nextcloud_url="https://cloud.example.com",
            nc_auth_user="alice",
            nc_auth_pass="secret123",
        )

    def test_raises_value_error_when_no_tty_and_no_extra(self):
        from unittest.mock import patch
        auth = self._auth()
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(ValueError, match="Nextcloud credentials required"):
                auth.run_auth_flow()

    def test_raises_value_error_when_no_tty_and_partial_extra(self):
        from unittest.mock import patch
        auth = self._auth()
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(ValueError, match="Nextcloud credentials required"):
                auth.run_auth_flow(extra={"server_url": "https://cloud.example.com"})

    def test_missing_field_listed_in_error(self):
        from unittest.mock import patch
        auth = self._auth()
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(ValueError) as exc_info:
                auth.run_auth_flow(extra={"server_url": "https://x.com", "username": "bob"})
            assert "app_password" in str(exc_info.value)

    def test_trailing_slash_stripped_from_server_url(self):
        import sys
        from unittest.mock import MagicMock, patch

        auth = self._auth()
        mock_nc_instance = MagicMock()
        mock_nc_instance.users.get_user.return_value = MagicMock(display_name="Alice")
        mock_nc_cls = MagicMock(return_value=mock_nc_instance)
        mock_nc_py_api = MagicMock()
        mock_nc_py_api.Nextcloud = mock_nc_cls

        with patch.dict(sys.modules, {"nc_py_api": mock_nc_py_api}):
            auth.run_auth_flow(extra={
                "server_url": "https://cloud.example.com/",
                "username": "alice",
                "app_password": "pw",
            })

        call_kwargs = mock_nc_cls.call_args.kwargs
        assert not call_kwargs["nextcloud_url"].endswith("/")


# ---------------------------------------------------------------------------
# _add_account forwards extra params to auth callback
# ---------------------------------------------------------------------------

class TestAddAccountExtraParams:
    @pytest.mark.asyncio
    async def test_nextcloud_credentials_forwarded(self):
        from cloud_drive_sync.config import Config
        from cloud_drive_sync.ipc.handlers import RequestHandler
        from cloud_drive_sync.ipc.protocol import JsonRpcRequest

        received = {}

        def mock_auth(provider="gdrive", headless=False, extra=None):
            received["provider"] = provider
            received["extra"] = extra
            return {"status": "ok", "email": "alice@cloud.example.com"}

        config = Config()
        handler = RequestHandler(engine=None, config=config)
        handler.set_auth_callback(mock_auth)

        req = JsonRpcRequest(
            id=1,
            method="add_account",
            params={
                "provider": "nextcloud",
                "server_url": "https://cloud.example.com",
                "username": "alice",
                "app_password": "secret",
            },
        )
        resp = await handler.handle(req)
        assert resp.error is None
        assert received["provider"] == "nextcloud"
        assert received["extra"] == {
            "server_url": "https://cloud.example.com",
            "username": "alice",
            "app_password": "secret",
        }

    @pytest.mark.asyncio
    async def test_gdrive_extra_is_none_when_no_credentials(self):
        from cloud_drive_sync.config import Config
        from cloud_drive_sync.ipc.handlers import RequestHandler
        from cloud_drive_sync.ipc.protocol import JsonRpcRequest

        received = {}

        def mock_auth(provider="gdrive", headless=False, extra=None):
            received["extra"] = extra
            return {"status": "ok", "email": "user@gmail.com"}

        config = Config()
        handler = RequestHandler(engine=None, config=config)
        handler.set_auth_callback(mock_auth)

        req = JsonRpcRequest(id=2, method="add_account", params={"provider": "gdrive"})
        await handler.handle(req)
        assert received["extra"] is None
