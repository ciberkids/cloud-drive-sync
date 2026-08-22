"""TOML configuration loading and saving."""

from __future__ import annotations

import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.paths import config_path
from cloud_drive_sync.webhooks.models import WebhooksConfig
from cloud_drive_sync.webhooks.serialise import webhooks_from_toml, webhooks_to_toml

log = get_logger("config")

# Namespace for deriving a stable uid for pairs that predate the ``uid`` field.
# Expressed as a uuid5 of a fixed URL rather than a hardcoded constant so the input
# is self-documenting; the value is stable for all time either way.
_PAIR_UID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/ciberkids/cloud-drive-sync/pair-uid"
)


def derive_pair_uid(
    *, provider: str, account_id: str, local_path: str, remote_folder_id: str
) -> str:
    """Derive a stable uid for a pair that has none stored.

    Deterministic on purpose. A random uuid minted per load would hand a different
    identity to webhook receivers after every restart, which is worse than having no
    identity at all. Derivation is a one-time bridge: the value is persisted by the
    next ``save()``, after which these four fields no longer influence it.

    Note this must never be called from a context that then writes the config as a
    side effect of *loading* it -- see the comment in :meth:`Config.load`.
    """
    key = f"{provider}|{account_id}|{local_path}|{remote_folder_id}"
    return str(uuid.uuid5(_PAIR_UID_NAMESPACE, key))


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
    # Emergency stop for this account (#54). Persisted so a restart cannot
    # silently resume activity the user deliberately halted.
    stopped: bool = False


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
    conflict_strategy: str = ""  # "" = inherit global default
    # Delete fail-safe (#53). None inherits sync.max_deletions_per_sync;
    # 0 disables the guard for this pair.
    max_deletions_per_sync: int | None = None
    # Window the per-pair limit is counted over. None inherits the global value.
    deletion_window_seconds: int | None = None
    # Force ETag polling even when the server advertises notify_push (#56). The
    # mechanism is chosen automatically; this exists for when that choice is wrong.
    force_polling: bool = False
    # Stable identity, independent of position in the list. The engine and the
    # database still key on the positional ``pair_N`` (roadmap item 9), which is
    # survivable internally because removal renumbers the stored rows to match --
    # but it is not survivable in an outbound payload, where a receiver keys its own
    # state on whatever we send and we cannot migrate it. Minted at creation;
    # derived deterministically for pairs that predate this field.
    uid: str = ""
    # Per-pair webhook overrides. The lowest level of the hierarchy: it may override
    # fields of an inherited target, switch one off, or define one of its own.
    webhooks: WebhooksConfig = field(default_factory=WebhooksConfig)


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
    # Delete fail-safe (#53): refuse a sync pass that would delete more than this
    # many files in one direction, until a human confirms it. 0 disables it.
    max_deletions_per_sync: int = 100
    # Deletions are counted over this sliding window, across sync passes — a
    # per-pass cap alone is defeated by a slow drip (#53).
    deletion_window_seconds: int = 60
    # Application-wide emergency stop (#54). Persisted for the same reason.
    stopped: bool = False
    pairs: list[SyncPair] = field(default_factory=list)


@dataclass
class GeneralConfig:
    """General daemon settings."""

    log_level: str = "info"


@dataclass
class HttpConfig:
    """Settings for the HTTP front-end (web UI + REST API)."""

    #: Shared token required on ``/api/*`` and the web UI. Empty means no
    #: authentication. Generated on a fresh install; left alone on upgrade, so an
    #: existing deployment is never locked out of a bookmarked URL by an update.
    token: str = ""


@dataclass
class Config:
    """Top-level configuration."""

    general: GeneralConfig = field(default_factory=GeneralConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    accounts: list[Account] = field(default_factory=list)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    webhooks: WebhooksConfig = field(default_factory=WebhooksConfig)
    # Where this config was loaded from, so save() writes back to the same file.
    # Without it, `--config /custom/path` loaded from there and saved to the
    # default location: every setting change went to a file the user was not
    # using, appeared to work in memory, and vanished on restart.
    _source_path: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Load config from a TOML file, falling back to defaults."""
        path = path or config_path()
        cfg = cls()
        cfg._source_path = path
        if not path.exists():
            log.info("No config file at %s, using defaults", path)
            return cfg

        with open(path, "rb") as f:
            data = tomllib.load(f)

        # General section
        general = data.get("general", {})
        cfg.general.log_level = general.get("log_level", cfg.general.log_level)

        # HTTP section
        http = data.get("http", {})
        cfg.http.token = http.get("token", cfg.http.token)

        # Webhooks section (global level of the hierarchy)
        cfg.webhooks = webhooks_from_toml(data.get("webhooks"))

        # Sync section
        sync = data.get("sync", {})
        cfg.sync.poll_interval = sync.get("poll_interval", cfg.sync.poll_interval)
        cfg.sync.conflict_strategy = sync.get("conflict_strategy", cfg.sync.conflict_strategy)
        cfg.sync.max_concurrent_transfers = sync.get(
            "max_concurrent_transfers", cfg.sync.max_concurrent_transfers
        )
        cfg.sync.debounce_delay = sync.get("debounce_delay", cfg.sync.debounce_delay)
        cfg.sync.max_deletions_per_sync = sync.get(
            "max_deletions_per_sync", cfg.sync.max_deletions_per_sync
        )
        cfg.sync.stopped = sync.get("stopped", cfg.sync.stopped)
        cfg.sync.deletion_window_seconds = sync.get(
            "deletion_window_seconds", cfg.sync.deletion_window_seconds
        )
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
                    conflict_strategy=pair_data.get("conflict_strategy", ""),
                    max_deletions_per_sync=pair_data.get("max_deletions_per_sync"),
                    deletion_window_seconds=pair_data.get("deletion_window_seconds"),
                    force_polling=pair_data.get("force_polling", False),
                    uid=pair_data.get("uid", ""),
                    webhooks=webhooks_from_toml(pair_data.get("webhooks")),
                )
            )

        # Backfill a stable uid for pairs written before the field existed.
        #
        # Done in memory only. `load()` must not write -- first-run detection is
        # `not config_path().exists()`, so a config created as a side effect of
        # loading one would make every install look like an upgrade and silently
        # switch off authentication-on-by-default for new installs (roadmap item 7,
        # pinned by test_feature_first_run_token). The derived value is persisted by
        # the next save(), which is a write the caller asked for.
        for pair in cfg.sync.pairs:
            if not pair.uid:
                pair.uid = derive_pair_uid(
                    provider=pair.provider,
                    account_id=pair.account_id,
                    local_path=pair.local_path,
                    remote_folder_id=pair.remote_folder_id,
                )

        # Warn if two pairs share a local_path but have conflicting strategies
        path_strategies: dict[str, str] = {}
        for pair in cfg.sync.pairs:
            effective = pair.conflict_strategy or cfg.sync.conflict_strategy
            if pair.local_path in path_strategies:
                if path_strategies[pair.local_path] != effective:
                    log.warning(
                        "Pairs sharing local_path '%s' have different conflict strategies "
                        "('%s' vs '%s') — behaviour may be inconsistent",
                        pair.local_path,
                        path_strategies[pair.local_path],
                        effective,
                    )
            else:
                path_strategies[pair.local_path] = effective

        # Accounts
        for acct_data in data.get("accounts", []):
            cfg.accounts.append(
                Account(
                    email=acct_data.get("email", ""),
                    display_name=acct_data.get("display_name", ""),
                    provider=acct_data.get("provider", "gdrive"),
                    server_url=acct_data.get("server_url", ""),
                    max_concurrent_transfers=acct_data.get("max_concurrent_transfers", 0),
                    stopped=acct_data.get("stopped", False),
                )
            )

        # Proxy section
        proxy_data = data.get("proxy", {})
        cfg.proxy.http_proxy = proxy_data.get("http_proxy", cfg.proxy.http_proxy)
        cfg.proxy.https_proxy = proxy_data.get("https_proxy", cfg.proxy.https_proxy)
        cfg.proxy.no_proxy = proxy_data.get("no_proxy", cfg.proxy.no_proxy)

        return cfg

    def save(self, path: Path | None = None) -> None:
        """Persist config to a TOML file.

        Defaults to wherever this config was loaded from, not to the standard
        location — otherwise ``--config`` would be honoured on read and ignored on
        write.
        """
        path = path or self._source_path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data: dict = {
            "general": {
                "log_level": self.general.log_level,
            },
            # Omitted entirely when unset, so an upgraded config does not gain an
            # empty token line that looks like a setting someone cleared.
            **({"http": {"token": self.http.token}} if self.http.token else {}),
            "sync": {
                "poll_interval": self.sync.poll_interval,
                "conflict_strategy": self.sync.conflict_strategy,
                "max_deletions_per_sync": self.sync.max_deletions_per_sync,
                "stopped": self.sync.stopped,
                "deletion_window_seconds": self.sync.deletion_window_seconds,
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
                        **({"conflict_strategy": p.conflict_strategy} if p.conflict_strategy else {}),
                        **(
                            {"max_deletions_per_sync": p.max_deletions_per_sync}
                            if p.max_deletions_per_sync is not None
                            else {}
                        ),
                        **(
                            {"deletion_window_seconds": p.deletion_window_seconds}
                            if p.deletion_window_seconds is not None
                            else {}
                        ),
                        **({"force_polling": True} if p.force_polling else {}),
                        # Truthiness is correct here: "" means "not yet assigned",
                        # and there is no meaningful empty-but-set uid.
                        **({"uid": p.uid} if p.uid else {}),
                        **(
                            {"webhooks": webhooks_to_toml(p.webhooks)}
                            if not p.webhooks.is_empty()
                            else {}
                        ),
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
                    **({"stopped": True} if a.stopped else {}),
                }
                for a in self.accounts
            ],
        }

        # Webhooks section, omitted entirely when unset for the same reason as
        # [http]: an upgraded config should not sprout empty tables.
        if not self.webhooks.is_empty():
            data["webhooks"] = webhooks_to_toml(self.webhooks)

        # Proxy section
        data["proxy"] = {
            "http_proxy": self.proxy.http_proxy,
            "https_proxy": self.proxy.https_proxy,
            "no_proxy": self.proxy.no_proxy,
        }

        # Owner-only, because since v2.4.3 this file can contain the web UI access
        # token — the credential for /api/* and the whole UI, which grants adding and
        # removing cloud accounts, changing where data syncs, and switching off delete
        # protection. It was landing at the umask default (0644), readable by every
        # local account, while the credential files next to it are 0600. The mode is
        # set before the content is written and re-applied to an existing file, the
        # same way auth.credentials._write_private does it.
        path.touch(mode=0o600, exist_ok=True)
        try:
            path.chmod(0o600)
        except OSError as exc:  # pragma: no cover - unusual filesystems
            log.warning("Could not restrict permissions on %s: %s", path, exc)
        with open(path, "wb") as f:
            tomli_w.dump(data, f)
        log.info("Config saved to %s", path)
