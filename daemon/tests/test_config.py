"""Tests for config load/save."""

from __future__ import annotations

from pathlib import Path

import pytest

from cloud_drive_sync.config import Config, SyncPair


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


def test_load_defaults_when_missing(tmp_path: Path):
    cfg = Config.load(tmp_path / "nonexistent.toml")
    assert cfg.general.log_level == "info"
    assert cfg.sync.poll_interval == 30
    assert cfg.sync.conflict_strategy == "keep_both"
    assert cfg.sync.max_concurrent_transfers == 4
    assert cfg.sync.debounce_delay == 1.0
    assert cfg.sync.pairs == []


def test_save_and_load_roundtrip(tmp_config: Path):
    cfg = Config()
    cfg.general.log_level = "debug"
    cfg.sync.poll_interval = 60
    cfg.sync.conflict_strategy = "newest_wins"
    cfg.sync.pairs = [
        SyncPair(local_path="/home/user/Drive", remote_folder_id="abc123", enabled=True),
        SyncPair(local_path="/tmp/backup", remote_folder_id="root", enabled=False),
    ]
    cfg.save(tmp_config)

    loaded = Config.load(tmp_config)
    assert loaded.general.log_level == "debug"
    assert loaded.sync.poll_interval == 60
    assert loaded.sync.conflict_strategy == "newest_wins"
    assert len(loaded.sync.pairs) == 2
    assert loaded.sync.pairs[0].local_path == "/home/user/Drive"
    assert loaded.sync.pairs[0].remote_folder_id == "abc123"
    assert loaded.sync.pairs[0].enabled is True
    assert loaded.sync.pairs[1].enabled is False


def test_partial_config(tmp_config: Path):
    """Config should use defaults for missing keys."""
    tmp_config.write_text('[general]\nlog_level = "warning"\n')
    cfg = Config.load(tmp_config)
    assert cfg.general.log_level == "warning"
    assert cfg.sync.poll_interval == 30  # default


def test_save_creates_parent_dirs(tmp_path: Path):
    deep_path = tmp_path / "a" / "b" / "c" / "config.toml"
    cfg = Config()
    cfg.save(deep_path)
    assert deep_path.exists()
    loaded = Config.load(deep_path)
    assert loaded.general.log_level == "info"
