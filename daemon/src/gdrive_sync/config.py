"""TOML configuration loading and saving."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from gdrive_sync.util.logging import get_logger
from gdrive_sync.util.paths import config_path

log = get_logger("config")


@dataclass
class SyncPair:
    """A local <-> remote folder mapping."""

    local_path: str = ""
    remote_folder_id: str = "root"
    enabled: bool = True
    sync_mode: str = "two_way"  # "two_way", "upload_only", "download_only"
    ignore_hidden: bool = True


@dataclass
class SyncConfig:
    """Sync-related settings."""

    poll_interval: int = 30
    conflict_strategy: str = "keep_both"
    max_concurrent_transfers: int = 4
    debounce_delay: float = 1.0
    pairs: list[SyncPair] = field(default_factory=list)


@dataclass
class GeneralConfig:
    """General daemon settings."""

    log_level: str = "info"


@dataclass
class Config:
    """Top-level configuration."""

    general: GeneralConfig = field(default_factory=GeneralConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Load config from a TOML file, falling back to defaults."""
        path = path or config_path()
        cfg = cls()
        if not path.exists():
            log.info("No config file at %s, using defaults", path)
            return cfg

        with open(path, "rb") as f:
            data = tomllib.load(f)

        # General section
        general = data.get("general", {})
        cfg.general.log_level = general.get("log_level", cfg.general.log_level)

        # Sync section
        sync = data.get("sync", {})
        cfg.sync.poll_interval = sync.get("poll_interval", cfg.sync.poll_interval)
        cfg.sync.conflict_strategy = sync.get("conflict_strategy", cfg.sync.conflict_strategy)
        cfg.sync.max_concurrent_transfers = sync.get(
            "max_concurrent_transfers", cfg.sync.max_concurrent_transfers
        )
        cfg.sync.debounce_delay = sync.get("debounce_delay", cfg.sync.debounce_delay)

        # Sync pairs
        for pair_data in sync.get("pairs", []):
            cfg.sync.pairs.append(
                SyncPair(
                    local_path=pair_data.get("local_path", ""),
                    remote_folder_id=pair_data.get("remote_folder_id", "root"),
                    enabled=pair_data.get("enabled", True),
                    sync_mode=pair_data.get("sync_mode", "two_way"),
                    ignore_hidden=pair_data.get("ignore_hidden", True),
                )
            )

        return cfg

    def save(self, path: Path | None = None) -> None:
        """Persist config to a TOML file."""
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data: dict = {
            "general": {
                "log_level": self.general.log_level,
            },
            "sync": {
                "poll_interval": self.sync.poll_interval,
                "conflict_strategy": self.sync.conflict_strategy,
                "max_concurrent_transfers": self.sync.max_concurrent_transfers,
                "debounce_delay": self.sync.debounce_delay,
                "pairs": [
                    {
                        "local_path": p.local_path,
                        "remote_folder_id": p.remote_folder_id,
                        "enabled": p.enabled,
                        "sync_mode": p.sync_mode,
                        "ignore_hidden": p.ignore_hidden,
                    }
                    for p in self.sync.pairs
                ],
            },
        }

        with open(path, "wb") as f:
            tomli_w.dump(data, f)
        log.info("Config saved to %s", path)
