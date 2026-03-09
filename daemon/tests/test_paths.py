"""Tests for XDG path resolution utilities."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from gdrive_sync.util.paths import (
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


class TestConfigDir:
    def test_default_uses_home(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_CONFIG_HOME", None)
            result = config_dir()
            assert result == Path.home() / ".config" / "gdrive-sync"

    def test_respects_xdg_config_home(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/custom/config"}):
            result = config_dir()
            assert result == Path("/custom/config/gdrive-sync")


class TestDataDir:
    def test_default_uses_home(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_DATA_HOME", None)
            result = data_dir()
            assert result == Path.home() / ".local" / "share" / "gdrive-sync"

    def test_respects_xdg_data_home(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/custom/data"}):
            result = data_dir()
            assert result == Path("/custom/data/gdrive-sync")


class TestRuntimeDir:
    def test_respects_xdg_runtime_dir(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1234"}):
            result = runtime_dir()
            assert result == Path("/run/user/1234")

    def test_fallback_without_xdg(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            result = runtime_dir()
            assert result == Path(f"/run/user/{os.getuid()}")


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
        assert result.name == "gdrive-sync.sock"

    def test_pid_path(self):
        result = pid_path()
        assert result.name == "gdrive-sync.pid"

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
            assert (tmp_path / "config" / "gdrive-sync").is_dir()
            assert (tmp_path / "data" / "gdrive-sync").is_dir()

    def test_idempotent(self, tmp_path: Path):
        with patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
        }):
            ensure_dirs()
            ensure_dirs()  # Should not raise
            assert (tmp_path / "config" / "gdrive-sync").is_dir()
