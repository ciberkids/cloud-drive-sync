"""Tests that all auth provider classes can be instantiated.

Catches the bug where abstract methods end up on the wrong class
(e.g., Issue #4: methods accidentally placed on _AuthUrlReady instead
of GoogleDriveAuth, making it uninstantiable).
"""

from __future__ import annotations

import pytest

# Import all provider packages to trigger registration
import cloud_drive_sync.providers.gdrive  # noqa: F401
import cloud_drive_sync.providers.dropbox  # noqa: F401
import cloud_drive_sync.providers.onedrive  # noqa: F401
import cloud_drive_sync.providers.nextcloud  # noqa: F401
import cloud_drive_sync.providers.box  # noqa: F401
import cloud_drive_sync.providers.proton  # noqa: F401

from cloud_drive_sync.providers.registry import all_providers


class TestAuthProviderInstantiation:
    """Every registered auth provider must be instantiable."""

    @pytest.mark.parametrize(
        "provider",
        [p for p in all_providers()],
        ids=[p.name for p in all_providers()],
    )
    def test_auth_class_instantiates(self, provider):
        """Auth class must not be abstract — all methods implemented."""
        auth = provider.auth_cls()
        assert auth is not None

    @pytest.mark.parametrize(
        "provider",
        [p for p in all_providers()],
        ids=[p.name for p in all_providers()],
    )
    def test_auth_class_has_required_methods(self, provider):
        """Auth class must implement all AuthProvider abstract methods."""
        auth = provider.auth_cls()
        for method in ("run_auth_flow", "save_credentials", "load_credentials",
                       "create_client", "get_account_email"):
            assert hasattr(auth, method), f"{provider.name} auth missing {method}"
            assert callable(getattr(auth, method)), f"{provider.name} auth.{method} not callable"

    @pytest.mark.parametrize(
        "provider",
        [p for p in all_providers()],
        ids=[p.name for p in all_providers()],
    )
    def test_client_class_instantiation_signature(self, provider):
        """Client class must exist and be a class."""
        assert provider.client_cls is not None
        assert isinstance(provider.client_cls, type)

    @pytest.mark.parametrize(
        "provider",
        [p for p in all_providers()],
        ids=[p.name for p in all_providers()],
    )
    def test_ops_and_poller_classes(self, provider):
        """Ops and poller classes must exist."""
        assert provider.ops_cls is not None
        assert provider.poller_cls is not None


class TestGoogleDriveAuthTwoStep:
    """Test the two-step auth flow (URL generation + code exchange)."""

    def test_pending_flow_initially_none(self):
        from cloud_drive_sync.providers.gdrive.auth import GoogleDriveAuth
        assert GoogleDriveAuth._pending_flow is None

    def test_exchange_code_without_pending_flow_raises(self):
        from cloud_drive_sync.providers.gdrive.auth import GoogleDriveAuth
        GoogleDriveAuth._pending_flow = None
        with pytest.raises(ValueError, match="No pending auth flow"):
            GoogleDriveAuth.exchange_code("fake-code")

    def test_auth_url_ready_exception(self):
        from cloud_drive_sync.providers.gdrive.auth import _AuthUrlReady
        exc = _AuthUrlReady("https://example.com/auth")
        assert exc.url == "https://example.com/auth"
        assert str(exc) == "https://example.com/auth"
