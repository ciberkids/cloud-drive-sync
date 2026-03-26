"""Tests for cross-platform path resolution utilities."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import platformdirs

from cloud_drive_sync.util.paths import (
    config_dir,
    config_path,
    credentials_path,
    data_dir,
    db_path,
    ensure_dirs,
    pid_path,
    runtime_dir,
    socket_path,
)

APP_NAME = "cloud-drive-sync"


class TestConfigDir:
    def test_default_uses_platformdirs(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_CONFIG_HOME", None)
            result = config_dir()
            expected = Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))
            assert result == expected

    def test_respects_xdg_config_home(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/custom/config"}):
            result = config_dir()
            assert result == Path("/custom/config/cloud-drive-sync")


class TestDataDir:
    def test_default_uses_platformdirs(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_DATA_HOME", None)
            result = data_dir()
            expected = Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
            assert result == expected

    def test_respects_xdg_data_home(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/custom/data"}):
            result = data_dir()
            assert result == Path("/custom/data/cloud-drive-sync")


class TestRuntimeDir:
    def test_respects_xdg_runtime_dir(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1234"}):
            result = runtime_dir()
            assert result == Path("/run/user/1234/cloud-drive-sync")

    def test_fallback_without_xdg(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            result = runtime_dir()
            expected = Path(platformdirs.user_runtime_dir(APP_NAME, appauthor=False))
            assert result == expected


class TestSpecificPaths:
    def test_config_path(self):
        result = config_path()
        assert result.name == "config.toml"
        assert result.parent == config_dir()

    def test_db_path(self):
        result = db_path()
        assert result.name == "state.db"
        assert result.parent == data_dir()

    def test_socket_path(self):
        result = socket_path()
        assert result.name == "cloud-drive-sync.sock"

    def test_pid_path(self):
        result = pid_path()
        assert result.name == "cloud-drive-sync.pid"

    def test_credentials_path(self):
        result = credentials_path()
        assert result.name == "credentials.enc"
        assert result.parent == data_dir()


class TestEnsureDirs:
    def test_creates_directories(self, tmp_path: Path):
        with patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
        }):
            ensure_dirs()
            assert (tmp_path / "config" / "cloud-drive-sync").is_dir()
            assert (tmp_path / "data" / "cloud-drive-sync").is_dir()

    def test_idempotent(self, tmp_path: Path):
        with patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
        }):
            ensure_dirs()
            ensure_dirs()  # Should not raise
            assert (tmp_path / "config" / "cloud-drive-sync").is_dir()
