"""TOML configuration loading and saving."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.paths import config_path

log = get_logger("config")


@dataclass
class ProxyConfig:
    """Proxy settings."""
    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = ""


@dataclass
class Account:
    """A registered cloud account."""
    email: str = ""
    display_name: str = ""
    provider: str = "gdrive"
    server_url: str = ""  # For self-hosted providers (e.g. Nextcloud)
    max_concurrent_transfers: int = 0  # 0 = use global default


@dataclass
class SyncRules:
    """Advanced sync filtering rules for a pair."""
    max_file_size_mb: float = 0
    include_regex: list[str] = field(default_factory=list)
    exclude_regex: list[str] = field(default_factory=list)
    min_date: str = ""


@dataclass
class SyncPair:
    """A local <-> remote folder mapping."""

    local_path: str = ""
    remote_folder_id: str = "root"
    enabled: bool = True
    sync_mode: str = "two_way"  # "two_way", "upload_only", "download_only"
    ignore_hidden: bool = True
    ignore_patterns: list[str] = field(default_factory=list)
    account_id: str = ""
    provider: str = "gdrive"
    sync_rules: SyncRules = field(default_factory=SyncRules)


@dataclass
class SyncConfig:
    """Sync-related settings."""

    poll_interval: int = 30
    conflict_strategy: str = "keep_both"
    max_concurrent_transfers: int = 4
    debounce_delay: float = 1.0
    convert_google_docs: bool = True
    notify_sync_complete: bool = True
    notify_conflicts: bool = True
    notify_errors: bool = True
    max_upload_kbps: int = 0
    max_download_kbps: int = 0
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
    accounts: list[Account] = field(default_factory=list)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

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
        cfg.sync.convert_google_docs = sync.get("convert_google_docs", cfg.sync.convert_google_docs)
        cfg.sync.notify_sync_complete = sync.get("notify_sync_complete", cfg.sync.notify_sync_complete)
        cfg.sync.notify_conflicts = sync.get("notify_conflicts", cfg.sync.notify_conflicts)
        cfg.sync.notify_errors = sync.get("notify_errors", cfg.sync.notify_errors)
        cfg.sync.max_upload_kbps = sync.get("max_upload_kbps", cfg.sync.max_upload_kbps)
        cfg.sync.max_download_kbps = sync.get("max_download_kbps", cfg.sync.max_download_kbps)

        # Sync pairs
        for pair_data in sync.get("pairs", []):
            rules_data = pair_data.get("sync_rules", {})
            sync_rules = SyncRules(
                max_file_size_mb=rules_data.get("max_file_size_mb", 0),
                include_regex=rules_data.get("include_regex", []),
                exclude_regex=rules_data.get("exclude_regex", []),
                min_date=rules_data.get("min_date", ""),
            )
            cfg.sync.pairs.append(
                SyncPair(
                    local_path=pair_data.get("local_path", ""),
                    remote_folder_id=pair_data.get("remote_folder_id", "root"),
                    enabled=pair_data.get("enabled", True),
                    sync_mode=pair_data.get("sync_mode", "two_way"),
                    ignore_hidden=pair_data.get("ignore_hidden", True),
                    ignore_patterns=pair_data.get("ignore_patterns", []),
                    account_id=pair_data.get("account_id", ""),
                    provider=pair_data.get("provider", "gdrive"),
                    sync_rules=sync_rules,
                )
            )

        # Accounts
        for acct_data in data.get("accounts", []):
            cfg.accounts.append(
                Account(
                    email=acct_data.get("email", ""),
                    display_name=acct_data.get("display_name", ""),
                    provider=acct_data.get("provider", "gdrive"),
                    server_url=acct_data.get("server_url", ""),
                    max_concurrent_transfers=acct_data.get("max_concurrent_transfers", 0),
                )
            )

        # Proxy section
        proxy_data = data.get("proxy", {})
        cfg.proxy.http_proxy = proxy_data.get("http_proxy", cfg.proxy.http_proxy)
        cfg.proxy.https_proxy = proxy_data.get("https_proxy", cfg.proxy.https_proxy)
        cfg.proxy.no_proxy = proxy_data.get("no_proxy", cfg.proxy.no_proxy)

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
                "convert_google_docs": self.sync.convert_google_docs,
                "notify_sync_complete": self.sync.notify_sync_complete,
                "notify_conflicts": self.sync.notify_conflicts,
                "notify_errors": self.sync.notify_errors,
                "max_upload_kbps": self.sync.max_upload_kbps,
                "max_download_kbps": self.sync.max_download_kbps,
                "pairs": [
                    {
                        "local_path": p.local_path,
                        "remote_folder_id": p.remote_folder_id,
                        "enabled": p.enabled,
                        "sync_mode": p.sync_mode,
                        "ignore_hidden": p.ignore_hidden,
                        "ignore_patterns": p.ignore_patterns,
                        "account_id": p.account_id,
                        "provider": p.provider,
                        "sync_rules": {
                            "max_file_size_mb": p.sync_rules.max_file_size_mb,
                            "include_regex": p.sync_rules.include_regex,
                            "exclude_regex": p.sync_rules.exclude_regex,
                            "min_date": p.sync_rules.min_date,
                        },
                    }
                    for p in self.sync.pairs
                ],
            },
            "accounts": [
                {
                    "email": a.email,
                    "display_name": a.display_name,
                    "provider": a.provider,
                    **({"server_url": a.server_url} if a.server_url else {}),
                    **({"max_concurrent_transfers": a.max_concurrent_transfers} if a.max_concurrent_transfers else {}),
                }
                for a in self.accounts
            ],
        }

        # Proxy section
        data["proxy"] = {
            "http_proxy": self.proxy.http_proxy,
            "https_proxy": self.proxy.https_proxy,
            "no_proxy": self.proxy.no_proxy,
        }

        with open(path, "wb") as f:
            tomli_w.dump(data, f)
        log.info("Config saved to %s", path)
