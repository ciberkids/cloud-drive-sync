"""IPC request dispatch: map RPC method names to handler functions."""

from __future__ import annotations

import os
import time
from typing import Any

from cloud_drive_sync.config import Config, SyncPair, SyncRules
from cloud_drive_sync.ipc.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JsonRpcRequest,
    JsonRpcResponse,
)
from cloud_drive_sync.sync.engine import SyncEngine
from cloud_drive_sync.util.logging import get_logger

log = get_logger("ipc.handlers")


class RequestHandler:
    """Dispatches JSON-RPC requests to the appropriate handler."""

    def __init__(self, engine: SyncEngine | None, config: Config) -> None:
        self._engine = engine
        self._config = config
        self._auth_callback = None
        self._exchange_code_callback = None
        self._shutdown_callback = None
        self._db = None
        self._drive_client = None
        self._start_time = time.monotonic()
        self._pid = os.getpid()

        self._handlers: dict[str, Any] = {
            "get_status": self._get_status,
            "get_sync_pairs": self._get_sync_pairs,
            "add_sync_pair": self._add_sync_pair,
            "remove_sync_pair": self._remove_sync_pair,
            "set_conflict_strategy": self._set_conflict_strategy,
            "set_pair_conflict_strategy": self._set_pair_conflict_strategy,
            "resolve_conflict": self._resolve_conflict,
            "force_sync": self._force_sync,
            "pause_sync": self._pause_sync,
            "resume_sync": self._resume_sync,
            "get_activity_log": self._get_activity_log,
            "get_conflicts": self._get_conflicts,
            "start_auth": self._start_auth,
            "logout": self._logout,
            "list_remote_folders": self._list_remote_folders,
            "create_remote_folder": self._create_remote_folder,
            "set_sync_mode": self._set_sync_mode,
            "set_ignore_hidden": self._set_ignore_hidden,
            "set_ignore_patterns": self._set_ignore_patterns,
            "add_account": self._add_account,
            "remove_account": self._remove_account,
            "list_accounts": self._list_accounts,
            "set_notification_prefs": self._set_notification_prefs,
            "get_notification_prefs": self._get_notification_prefs,
            "set_bandwidth_limits": self._set_bandwidth_limits,
            "get_bandwidth_limits": self._get_bandwidth_limits,
            "set_sync_rules": self._set_sync_rules,
            "get_sync_rules": self._get_sync_rules,
            "set_proxy": self._set_proxy,
            "get_proxy": self._get_proxy,
            "get_file_status": self._get_file_status,
            "set_account_max_transfers": self._set_account_max_transfers,
            "exchange_auth_code": self._exchange_auth_code,
            "shutdown": self._shutdown,
            "list_local_dirs": self._list_local_dirs,
            "mkdir_local": self._mkdir_local,
            "repair": self._repair,
        }

    def set_auth_callback(self, callback) -> None:
        """Set a callback for handling auth flow (runs in a thread)."""
        self._auth_callback = callback

    def set_exchange_code_callback(self, callback) -> None:
        """Set a callback for exchanging an auth code (two-step flow)."""
        self._exchange_code_callback = callback

    def set_shutdown_callback(self, callback) -> None:
        """Set a callback for shutting down the daemon."""
        self._shutdown_callback = callback

    def set_engine(self, engine: SyncEngine) -> None:
        """Set or replace the sync engine (e.g. after authentication)."""
        self._engine = engine

    def set_db(self, db) -> None:
        """Set the database reference (for logging before engine init)."""
        self._db = db

    def set_drive_client(self, client) -> None:
        """Set the drive client (for folder browsing)."""
        self._drive_client = client

    @staticmethod
    def _get_version() -> str:
        try:
            from importlib.metadata import version
            return version("cloud-drive-sync")
        except Exception:
            from cloud_drive_sync import __version__
            return __version__

    async def handle(self, request: JsonRpcRequest) -> JsonRpcResponse:
        """Dispatch a request to its handler and return a response."""
        handler = self._handlers.get(request.method)
        if handler is None:
            return JsonRpcResponse.fail(
                request.id, METHOD_NOT_FOUND, f"Unknown method: {request.method}"
            )

        try:
            result = await handler(request.params)
            return JsonRpcResponse.success(request.id, result)
        except TypeError as exc:
            return JsonRpcResponse.fail(request.id, INVALID_PARAMS, str(exc))
        except Exception as exc:
            log.exception("Handler error for %s", request.method)
            return JsonRpcResponse.fail(request.id, INTERNAL_ERROR, str(exc))

    def _require_engine(self) -> SyncEngine:
        """Raise if engine is not initialized (not yet authenticated)."""
        if self._engine is None:
            raise RuntimeError("Not authenticated. Please connect your Google account first.")
        return self._engine

    def _default_pair_id(self, params: dict | None) -> str:
        """Extract pair_id from params, defaulting to pair_0 if not provided."""
        params = params or {}
        pair_id = params.get("pair_id")
        if not pair_id:
            if self._config.sync.pairs:
                pair_id = "pair_0"
            else:
                raise TypeError("No sync pairs configured")
        return pair_id

    async def _get_status(self, params: dict) -> dict:
        import datetime
        from cloud_drive_sync.util.paths import socket_path

        uptime = time.monotonic() - self._start_time
        sock_path = str(socket_path())
        started_at = (datetime.datetime.now() - datetime.timedelta(seconds=uptime)).strftime("%Y-%m-%d %H:%M")

        from cloud_drive_sync import __build_date__

        daemon_info = {
            "pid": self._pid,
            "uptime": int(uptime),
            "uptime_formatted": self._format_uptime(uptime),
            "socket_path": sock_path,
            "version": self._get_version(),
            "started_at": started_at,
            "build_date": __build_date__ or None,
        }

        if self._engine is None:
            return {
                "connected": False,
                "syncing": False,
                "paused": False,
                "error": "Not authenticated. Connect your Google account first.",
                "last_sync": None,
                "files_synced": 0,
                "active_transfers": 0,
                "live_transfers": [],
                "daemon": daemon_info,
            }

        pairs = self._engine.get_status()
        syncing = any(p.get("active_transfers", 0) > 0 for p in pairs.values())
        paused = all(p.get("paused", False) for p in pairs.values()) if pairs else False
        all_errors = []
        for p in pairs.values():
            all_errors.extend(p.get("errors", []))
        last_syncs = [p["last_sync"] for p in pairs.values() if p.get("last_sync")]
        total_transfers = sum(p.get("active_transfers", 0) for p in pairs.values())

        # Bug 6 fix: count actual synced files from DB, per pair
        total_synced = 0
        pair_counts = []
        db = self._db or (self._engine._db if self._engine else None)
        if db:
            for i, pair_cfg in enumerate(self._config.sync.pairs):
                pair_id = f"pair_{i}"
                counts = await db.count_by_state(pair_id)
                pair_synced = counts.get("synced", 0)
                total_synced += pair_synced
                pair_counts.append({
                    "pair_id": pair_id,
                    "files_synced": pair_synced,
                    "account_id": pair_cfg.account_id or "",
                    "provider": pair_cfg.provider or "gdrive",
                    "local_path": pair_cfg.local_path or "",
                })

        # Live transfer info
        live_transfers = self._engine.get_active_transfers()

        return {
            "connected": True,
            "syncing": syncing or total_transfers > 0,
            "paused": paused,
            "error": f"{len(all_errors)} sync error{'s' if len(all_errors) != 1 else ''} — check Activity for details" if all_errors else None,
            "last_sync": max(last_syncs) if last_syncs else None,
            "files_synced": total_synced,
            "pair_counts": pair_counts,
            "active_transfers": len(live_transfers),
            "live_transfers": live_transfers,
            "daemon": daemon_info,
        }

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        s = int(seconds)
        days, s = divmod(s, 86400)
        hours, s = divmod(s, 3600)
        minutes, s = divmod(s, 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {s}s"
        return f"{s}s"

    def _cleanup_orphaned_pairs(self) -> None:
        """Remove pairs referencing accounts that no longer exist."""
        known_emails = {a.email for a in self._config.accounts}
        before = len(self._config.sync.pairs)
        self._config.sync.pairs = [
            p for p in self._config.sync.pairs
            if not p.account_id or p.account_id in known_emails
        ]
        if len(self._config.sync.pairs) < before:
            self._config.save()

    async def _get_sync_pairs(self, params: dict) -> list[dict]:
        self._cleanup_orphaned_pairs()
        return [
            {
                "id": str(i),
                "local_path": p.local_path,
                "remote_folder_id": p.remote_folder_id,
                "enabled": p.enabled,
                "sync_mode": p.sync_mode,
                "ignore_hidden": p.ignore_hidden,
                "ignore_patterns": p.ignore_patterns,
                "account_id": p.account_id,
                "provider": p.provider,
                "conflict_strategy": p.conflict_strategy,
            }
            for i, p in enumerate(self._config.sync.pairs)
        ]

    async def _add_sync_pair(self, params: dict) -> dict:
        local_path = params.get("local_path")
        remote_folder_id = params.get("remote_folder_id", "root")
        ignore_hidden = params.get("ignore_hidden", True)
        provider = params.get("provider", "gdrive")
        sync_mode = params.get("sync_mode", "two_way")
        if not local_path:
            raise TypeError("local_path is required")

        # Validate local_path is absolute and doesn't contain traversal
        from pathlib import Path
        if not Path(local_path).is_absolute():
            raise TypeError("local_path must be an absolute path")
        if ".." in Path(local_path).parts:
            raise TypeError("local_path must not contain '..' components")

        # Prevent duplicate pairs
        for existing in self._config.sync.pairs:
            if existing.local_path == local_path and existing.remote_folder_id == remote_folder_id:
                raise TypeError("This sync pair already exists")

        pair = SyncPair(
            local_path=local_path,
            remote_folder_id=remote_folder_id,
            enabled=True,
            sync_mode=sync_mode,
            ignore_hidden=ignore_hidden,
            ignore_patterns=params.get("ignore_patterns", []),
            account_id=params.get("account_id", ""),
            provider=provider,
        )
        self._config.sync.pairs.append(pair)
        self._config.save()
        index = len(self._config.sync.pairs) - 1
        pair_id = str(index)
        internal_pair_id = f"pair_{index}"

        # Register the new pair with the running engine immediately so sync
        # starts without requiring a daemon restart.
        if self._engine is not None:
            await self._engine._start_pair(pair, internal_pair_id)

        return {
            "id": pair_id,
            "local_path": local_path,
            "remote_folder_id": remote_folder_id,
            "enabled": True,
            "sync_mode": sync_mode,
            "ignore_hidden": ignore_hidden,
            "ignore_patterns": pair.ignore_patterns,
            "account_id": pair.account_id,
            "provider": provider,
        }

    async def _remove_sync_pair(self, params: dict) -> dict:
        id_val = params.get("id") or params.get("index")
        try:
            index = int(id_val)
        except (TypeError, ValueError):
            raise TypeError("Invalid pair id")
        if index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair id")
        removed = self._config.sync.pairs.pop(index)
        self._config.save()
        return {"status": "removed", "local_path": removed.local_path}

    async def _set_conflict_strategy(self, params: dict) -> dict:
        strategy = params.get("strategy")
        valid = {"keep_both", "newest_wins", "ask_user", "local_wins", "remote_wins"}
        if strategy not in valid:
            raise TypeError(f"strategy must be one of {valid}")
        self._config.sync.conflict_strategy = strategy
        if self._engine:
            self._engine.conflict_resolver.strategy = strategy
        self._config.save()
        return {"status": "ok", "strategy": strategy}

    async def _set_pair_conflict_strategy(self, params: dict) -> dict:
        pair_id = params.get("pair_id")
        strategy = params.get("strategy", "")
        valid = {"keep_both", "newest_wins", "ask_user", "local_wins", "remote_wins", ""}
        if strategy not in valid:
            raise TypeError(
                "strategy must be one of 'keep_both', 'newest_wins', 'ask_user', "
                "'local_wins', 'remote_wins' or '' to inherit global"
            )
        try:
            index = int(pair_id)
        except (TypeError, ValueError):
            raise TypeError("Invalid pair_id")
        if index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair_id")
        self._config.sync.pairs[index].conflict_strategy = strategy
        self._config.save()
        return {"status": "ok", "pair_id": pair_id, "conflict_strategy": strategy}

    async def _resolve_conflict(self, params: dict) -> dict:
        engine = self._require_engine()
        conflict_id = params.get("conflict_id")
        resolution = params.get("resolution")
        if conflict_id is None or resolution is None:
            raise TypeError("conflict_id and resolution are required")
        engine.conflict_resolver.set_user_resolution(conflict_id, resolution)
        return {"status": "ok"}

    async def _force_sync(self, params: dict) -> dict:
        engine = self._require_engine()
        params = params or {}
        pair_id = params.get("pair_id")
        if pair_id:
            ok = await engine.force_sync(pair_id)
            return {"status": "ok" if ok else "not_found"}
        # No pair_id supplied → sync all pairs
        await engine.force_sync_all()
        return {"status": "ok"}

    async def _pause_sync(self, params: dict) -> dict:
        engine = self._require_engine()
        pair_id = self._default_pair_id(params)
        ok = await engine.pause_pair(pair_id)
        return {"status": "paused" if ok else "not_found"}

    async def _resume_sync(self, params: dict) -> dict:
        engine = self._require_engine()
        pair_id = self._default_pair_id(params)
        ok = await engine.resume_pair(pair_id)
        return {"status": "resumed" if ok else "not_found"}

    # Maps UI filter tab names → (status, actions) for server-side filtering.
    _FILTER_TO_ACTIONS: dict[str, list[str]] = {
        "upload": ["upload"],
        "download": ["download"],
        "delete": ["delete_local", "delete_remote"],
        "conflict": ["conflict"],
        "sync": ["sync", "mkdir"],
        "auth": ["auth"],
        "move": ["move"],
    }

    async def _get_activity_log(self, params: dict) -> list[dict]:
        db = self._db or (self._engine._db if self._engine else None)
        if db is None:
            return []
        params = params or {}
        limit = params.get("limit", 50)
        pair_id = params.get("pair_id")
        offset = params.get("offset", 0)
        ui_filter = params.get("filter", "all")

        # Translate UI filter to SQL predicates
        db_status: str | None = None
        db_actions: list[str] | None = None
        if ui_filter == "error":
            db_status = "error"
        elif ui_filter in self._FILTER_TO_ACTIONS:
            db_actions = self._FILTER_TO_ACTIONS[ui_filter]

        entries = await db.get_recent_log(
            limit=limit, offset=offset, pair_id=pair_id,
            status=db_status, actions=db_actions,
        )

        # Bug 4 fix: filter by active pair IDs when no specific pair_id requested
        if not pair_id:
            active_pair_ids = {f"pair_{i}" for i in range(len(self._config.sync.pairs))}
            active_pair_ids.add("_system")
            entries = [e for e in entries if e.pair_id in active_pair_ids]

        # Human-readable action descriptions
        _ACTION_LABELS = {
            "upload": "File uploaded",
            "download": "File downloaded",
            "mkdir": "Directory created",
            "delete_local": "Local file deleted",
            "delete_remote": "Remote file deleted",
            "move": "Renamed/moved",
            "conflict": "Conflict detected",
            "auth": "Authentication",
            "sync": "Sync",
        }

        result = []
        for e in entries:
            # Normalize event_type for UI filter tabs.
            # event_type is based on the action, not the status — failed uploads
            # still show in the "Upload" filter, with status="error" visible as
            # a badge. The "Error" filter is handled client-side via status.
            if e.action == "mkdir":
                event_type = "sync"  # folder creation is part of sync, not download
            elif e.action.startswith("delete"):
                event_type = "delete"
            elif e.action == "sync":
                event_type = "sync"
            elif e.action in ("upload", "download", "move", "conflict", "auth"):
                event_type = e.action
            else:
                event_type = e.action

            # Build a human-readable detail string
            detail = e.detail or ""
            label = _ACTION_LABELS.get(e.action, e.action)
            if e.action == "sync" and detail:
                # Sync events already have self-explanatory detail
                pass
            elif detail:
                detail = f"{label}: {detail}"
            else:
                detail = label

            result.append({
                "id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "event_type": event_type,
                "path": e.path,
                "details": detail,
                "status": e.status,
                "pair_id": e.pair_id,
                "reason": e.reason or "",
            })

        return result

    async def _get_conflicts(self, params: dict) -> list[dict]:
        db = self._db or (self._engine._db if self._engine else None)
        if db is None:
            return []
        params = params or {}
        pair_id = params.get("pair_id")
        conflicts = await db.get_unresolved_conflicts(pair_id)
        return [
            {
                "id": c.id,
                "path": c.path,
                "pair_id": c.pair_id,
                "local_md5": c.local_md5,
                "remote_md5": c.remote_md5,
                "detected_at": c.detected_at.isoformat(),
            }
            for c in conflicts
        ]

    async def _start_auth(self, params: dict) -> dict:
        if self._auth_callback:
            import asyncio

            try:
                result = await asyncio.to_thread(self._auth_callback)
                # Bug 8 fix: log successful auth event
                if self._db:
                    from cloud_drive_sync.db.models import SyncLogEntry
                    await self._db.add_log_entry(SyncLogEntry(
                        action="auth", path="", pair_id="_system",
                        status="success", detail="Authentication successful",
                    ))
                if isinstance(result, dict):
                    return result
                return {"status": "ok"}
            except Exception as exc:
                # Bug 8 fix: log failed auth event
                if self._db:
                    from cloud_drive_sync.db.models import SyncLogEntry
                    await self._db.add_log_entry(SyncLogEntry(
                        action="auth", path="", pair_id="_system",
                        status="error", detail=str(exc),
                    ))
                return {"status": "error", "message": str(exc)}
        return {"status": "no_auth_callback"}

    async def _list_remote_folders(self, params: dict) -> dict:
        """List folders in a given parent folder on the remote provider."""
        if self._engine is None and self._drive_client is None:
            return {"folders": [], "shared_drives": [], "error": "Not authenticated"}

        params = params or {}
        account_id = params.get("account_id", "")
        client = None
        if account_id and self._engine:
            # Try compound key first (e.g. "nextcloud:user@gmail.com")
            client = self._engine._clients.get(account_id)
            if client is None:
                # Backward compat: bare email passed — find by email suffix
                for key, c in self._engine._clients.items():
                    if ":" in key and key.split(":", 1)[1] == account_id:
                        client = c
                        break
        if client is None:
            client = self._drive_client or (self._engine._client if self._engine else None)
        if client is None:
            return {"folders": [], "shared_drives": [], "error": "Not authenticated"}

        parent_id = params.get("parent_id", "root")

        try:
            folder_mime = getattr(client, "folder_mime_type", None)

            if folder_mime is not None:
                # GDrive-style: use Drive query syntax
                query = (
                    f"'{parent_id}' in parents"
                    f" and mimeType = '{folder_mime}'"
                    " and trashed = false"
                )
                result = await client.list_files(query=query, page_size=100)
                folders = [
                    {"id": f["id"], "name": f["name"]}
                    for f in result.get("files", [])
                ]
                folders.sort(key=lambda f: f["name"].lower())

                shared_drives: list[dict] = []
                if parent_id == "root":
                    try:
                        drives = await client.list_shared_drives()
                        shared_drives = [
                            {"id": d["id"], "name": d["name"]} for d in drives
                        ]
                        shared_drives.sort(key=lambda d: d["name"].lower())
                    except Exception as exc:
                        log.warning("Failed to list shared drives: %s", exc)
            else:
                # Path-based provider (Nextcloud, etc.)
                result = await client.list_files(folder_id=parent_id, page_size=500)
                nc_parent = "/" if parent_id == "root" else parent_id
                folders = []
                for f in result.get("files", []):
                    if f.get("mimeType") == "httpd/unix-directory":
                        folder_path = (
                            f"/{f['name']}" if nc_parent == "/"
                            else f"{nc_parent}/{f['name']}"
                        )
                        folders.append({"id": folder_path, "name": f["name"]})
                folders.sort(key=lambda f: f["name"].lower())
                shared_drives = []

            return {"folders": folders, "shared_drives": shared_drives, "parent_id": parent_id}
        except Exception as exc:
            log.error("Failed to list remote folders: %s", exc)
            return {"folders": [], "shared_drives": [], "error": str(exc)}

    async def _create_remote_folder(self, params: dict) -> dict:
        """Create a new folder on the remote provider."""
        if self._engine is None and self._drive_client is None:
            return {"error": "Not authenticated"}

        params = params or {}
        account_id = params.get("account_id", "")
        name = (params.get("name") or "").strip()
        parent_id = params.get("parent_id", "root")

        if not name:
            return {"error": "Folder name is required"}

        client = None
        if account_id and self._engine:
            client = self._engine._clients.get(account_id)
            if client is None:
                for key, c in self._engine._clients.items():
                    if ":" in key and key.split(":", 1)[1] == account_id:
                        client = c
                        break
        if client is None:
            client = self._drive_client or (self._engine._client if self._engine else None)
        if client is None:
            return {"error": "Not authenticated"}

        try:
            result = await client.create_file(
                name=name,
                parent_id=parent_id,
                is_folder=True,
            )
            return {"id": result.get("id", ""), "name": result.get("name", name)}
        except Exception as exc:
            log.error("Failed to create remote folder: %s", exc)
            return {"error": str(exc)}

    async def _set_sync_mode(self, params: dict) -> dict:
        """Change the sync mode for a given pair."""
        id_val = params.get("pair_id")
        mode = params.get("sync_mode")
        valid_modes = {"two_way", "upload_only", "download_only"}
        if mode not in valid_modes:
            raise TypeError(f"sync_mode must be one of {valid_modes}")
        try:
            index = int(id_val)
        except (TypeError, ValueError):
            raise TypeError("Invalid pair_id")
        if index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair_id")
        self._config.sync.pairs[index].sync_mode = mode
        self._config.save()
        return {"status": "ok", "sync_mode": mode}

    async def _set_ignore_hidden(self, params: dict) -> dict:
        """Toggle the ignore_hidden setting for a sync pair."""
        params = params or {}
        pair_id = params.get("pair_id")
        ignore_hidden = params.get("ignore_hidden")
        if pair_id is None or ignore_hidden is None:
            raise TypeError("pair_id and ignore_hidden are required")
        try:
            index = int(pair_id)
        except (TypeError, ValueError):
            raise TypeError("Invalid pair_id")
        if index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair_id")
        self._config.sync.pairs[index].ignore_hidden = ignore_hidden
        self._config.save()
        return {"status": "ok", "ignore_hidden": ignore_hidden}

    async def _set_ignore_patterns(self, params: dict) -> dict:
        """Set custom ignore patterns for a sync pair."""
        params = params or {}
        pair_id = params.get("pair_id")
        patterns = params.get("patterns")
        if pair_id is None or patterns is None:
            raise TypeError("pair_id and patterns are required")
        if not isinstance(patterns, list):
            raise TypeError("patterns must be a list of strings")
        try:
            index = int(pair_id)
        except (TypeError, ValueError):
            raise TypeError("Invalid pair_id")
        if index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair_id")
        self._config.sync.pairs[index].ignore_patterns = patterns
        self._config.save()
        return {"status": "ok", "ignore_patterns": patterns}

    async def _add_account(self, params: dict) -> dict:
        """Trigger auth flow to add a new account."""
        params = params or {}
        provider = params.get("provider", "gdrive")
        headless = params.get("headless", False)
        # Collect any provider-specific credentials passed directly (e.g. Nextcloud
        # server_url / username / app_password) so non-OAuth providers don't need a TTY.
        extra_keys = {"server_url", "username", "app_password", "server", "token"}
        extra = {k: v for k, v in params.items() if k in extra_keys and v}
        if self._auth_callback:
            import asyncio
            try:
                result = await asyncio.to_thread(self._auth_callback, provider, headless, extra or None)
                if isinstance(result, dict):
                    return result
                return {"status": "ok"}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}
        return {"status": "no_auth_callback"}

    async def _remove_account(self, params: dict) -> dict:
        """Remove a registered account and its credentials."""
        params = params or {}
        email = params.get("email")
        if not email:
            raise TypeError("email is required")
        provider = params.get("provider")

        # Determine provider for namespaced client key.
        # Use the explicit provider param when given to correctly handle the
        # case where two providers share the same email address.
        if provider:
            provider_name = provider
        else:
            acct_to_remove = next((a for a in self._config.accounts if a.email == email), None)
            provider_name = acct_to_remove.provider if acct_to_remove else "gdrive"

        # Remove from config — if provider is given, remove only that account;
        # otherwise remove all accounts with this email (backward compat).
        if provider:
            self._config.accounts = [
                a for a in self._config.accounts
                if not (a.email == email and a.provider == provider)
            ]
            self._config.sync.pairs = [
                p for p in self._config.sync.pairs
                if not (p.account_id == email and p.provider == provider)
            ]
        else:
            self._config.accounts = [a for a in self._config.accounts if a.email != email]
            self._config.sync.pairs = [
                p for p in self._config.sync.pairs if p.account_id != email
            ]

        self._config.save()

        # Delete credential file
        from cloud_drive_sync.util.paths import account_credentials_path
        cred_path = account_credentials_path(email)
        if cred_path.exists():
            cred_path.unlink()

        # Remove client from engine (namespaced key, with backward-compat
        # fallback to bare email for legacy entries).
        if self._engine:
            namespaced = f"{provider_name}:{email}"
            if namespaced in self._engine._clients:
                del self._engine._clients[namespaced]
            elif email in self._engine._clients:
                del self._engine._clients[email]

        return {"status": "ok", "email": email}

    async def _list_accounts(self, params: dict) -> list[dict]:
        """List all registered accounts."""
        accounts = []
        for acct in self._config.accounts:
            has_client = (
                self._engine is not None
                and f"{acct.provider or 'gdrive'}:{acct.email}" in self._engine._clients
            )
            accounts.append({
                "email": acct.email,
                "display_name": acct.display_name,
                "status": "connected" if has_client else "disconnected",
                "provider": acct.provider,
                "max_concurrent_transfers": acct.max_concurrent_transfers,
            })
        return accounts

    async def _set_notification_prefs(self, params: dict) -> dict:
        """Update notification preferences."""
        params = params or {}
        if "notify_sync_complete" in params:
            self._config.sync.notify_sync_complete = bool(params["notify_sync_complete"])
        if "notify_conflicts" in params:
            self._config.sync.notify_conflicts = bool(params["notify_conflicts"])
        if "notify_errors" in params:
            self._config.sync.notify_errors = bool(params["notify_errors"])
        self._config.save()
        return {
            "notify_sync_complete": self._config.sync.notify_sync_complete,
            "notify_conflicts": self._config.sync.notify_conflicts,
            "notify_errors": self._config.sync.notify_errors,
        }

    async def _get_notification_prefs(self, params: dict) -> dict:
        """Return current notification preferences."""
        return {
            "notify_sync_complete": self._config.sync.notify_sync_complete,
            "notify_conflicts": self._config.sync.notify_conflicts,
            "notify_errors": self._config.sync.notify_errors,
        }

    async def _logout(self, params: dict) -> dict:
        from cloud_drive_sync.util.paths import credentials_path, data_dir

        cred_path = credentials_path()
        salt_path = data_dir() / "token_salt"
        for p in (cred_path, salt_path):
            if p.exists():
                p.unlink()

        # Log the logout event
        if self._db:
            from cloud_drive_sync.db.models import SyncLogEntry

            entry = SyncLogEntry(
                action="auth",
                path="",
                pair_id="_system",
                status="success",
                detail="Logged out",
            )
            await self._db.add_log_entry(entry)

        return {"status": "logged_out"}

    async def _set_bandwidth_limits(self, params: dict) -> dict:
        """Set upload and/or download bandwidth limits (KB/s, 0=unlimited)."""
        params = params or {}
        if "max_upload_kbps" in params:
            self._config.sync.max_upload_kbps = int(params["max_upload_kbps"])
        if "max_download_kbps" in params:
            self._config.sync.max_download_kbps = int(params["max_download_kbps"])
        self._config.save()
        return {
            "max_upload_kbps": self._config.sync.max_upload_kbps,
            "max_download_kbps": self._config.sync.max_download_kbps,
        }

    async def _get_bandwidth_limits(self, params: dict) -> dict:
        """Return current bandwidth limits."""
        return {
            "max_upload_kbps": self._config.sync.max_upload_kbps,
            "max_download_kbps": self._config.sync.max_download_kbps,
        }

    async def _set_sync_rules(self, params: dict) -> dict:
        """Set advanced sync rules for a given pair."""
        params = params or {}
        pair_id = params.get("pair_id")
        if pair_id is None:
            raise TypeError("pair_id is required")
        try:
            index = int(pair_id)
        except (TypeError, ValueError):
            raise TypeError("Invalid pair_id")
        if index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair_id")

        rules_data = params.get("rules", {})
        pair = self._config.sync.pairs[index]
        pair.sync_rules = SyncRules(
            max_file_size_mb=float(rules_data.get("max_file_size_mb", 0)),
            include_regex=rules_data.get("include_regex", []),
            exclude_regex=rules_data.get("exclude_regex", []),
            min_date=rules_data.get("min_date", ""),
        )
        self._config.save()
        return {
            "status": "ok",
            "sync_rules": {
                "max_file_size_mb": pair.sync_rules.max_file_size_mb,
                "include_regex": pair.sync_rules.include_regex,
                "exclude_regex": pair.sync_rules.exclude_regex,
                "min_date": pair.sync_rules.min_date,
            },
        }

    async def _get_sync_rules(self, params: dict) -> dict:
        """Return advanced sync rules for a given pair."""
        params = params or {}
        pair_id = params.get("pair_id")
        if pair_id is None:
            raise TypeError("pair_id is required")
        try:
            index = int(pair_id)
        except (TypeError, ValueError):
            raise TypeError("Invalid pair_id")
        if index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair_id")

        rules = self._config.sync.pairs[index].sync_rules
        return {
            "max_file_size_mb": rules.max_file_size_mb,
            "include_regex": rules.include_regex,
            "exclude_regex": rules.exclude_regex,
            "min_date": rules.min_date,
        }

    async def _set_proxy(self, params: dict) -> dict:
        """Update proxy settings."""
        params = params or {}
        if "http_proxy" in params:
            self._config.proxy.http_proxy = str(params["http_proxy"])
        if "https_proxy" in params:
            self._config.proxy.https_proxy = str(params["https_proxy"])
        if "no_proxy" in params:
            self._config.proxy.no_proxy = str(params["no_proxy"])
        self._config.save()
        return {
            "http_proxy": self._config.proxy.http_proxy,
            "https_proxy": self._config.proxy.https_proxy,
            "no_proxy": self._config.proxy.no_proxy,
        }

    async def _get_proxy(self, params: dict) -> dict:
        """Return current proxy settings."""
        return {
            "http_proxy": self._config.proxy.http_proxy,
            "https_proxy": self._config.proxy.https_proxy,
            "no_proxy": self._config.proxy.no_proxy,
        }

    async def _get_file_status(self, params: dict) -> dict:
        """Return the sync state for a specific file path.

        Params:
            path: Absolute filesystem path to query.

        Returns:
            {"state": "synced"|"uploading"|"downloading"|...} or
            {"state": "unknown"} if the file is not tracked.
        """
        params = params or {}
        abs_path = params.get("path")
        if not abs_path:
            raise TypeError("path is required")


        db = self._db or (self._engine._db if self._engine else None)
        if db is None:
            return {"state": "unknown"}

        # Determine which sync pair (if any) this path belongs to
        for i, pair in enumerate(self._config.sync.pairs):
            local_root = pair.local_path.rstrip("/")
            if abs_path == local_root or abs_path.startswith(local_root + "/"):
                rel_path = abs_path[len(local_root) + 1:] if abs_path != local_root else ""
                pair_id = f"pair_{i}"
                entry = await db.get_sync_entry(rel_path, pair_id)
                if entry:
                    return {"state": entry.state.value}
                return {"state": "unknown"}

        return {"state": "unknown"}

    async def _set_account_max_transfers(self, params: dict) -> dict:
        """Set max concurrent transfers for an account."""
        params = params or {}
        email = params.get("email")
        value = params.get("max_concurrent_transfers")
        if not email or value is None:
            raise TypeError("email and max_concurrent_transfers are required")
        value = int(value)
        if value < 0:
            raise TypeError("max_concurrent_transfers must be >= 0")

        for acct in self._config.accounts:
            if acct.email == email:
                acct.max_concurrent_transfers = value
                self._config.save()
                return {"status": "ok", "max_concurrent_transfers": value}

        raise TypeError(f"Account {email} not found")

    async def _exchange_auth_code(self, params: dict) -> dict:
        """Complete a two-step auth flow by exchanging the authorization code."""
        params = params or {}
        code = params.get("code")
        provider = params.get("provider", "gdrive")
        if not code:
            raise TypeError("code is required")

        if self._auth_callback:
            import asyncio
            try:
                # The auth callback with code triggers the exchange path
                result = await asyncio.to_thread(
                    self._exchange_code_callback, provider, code
                )
                if isinstance(result, dict):
                    return result
                return {"status": "ok"}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}
        return {"status": "no_auth_callback"}

    async def _shutdown(self, params: dict) -> dict:
        """Gracefully shut down the daemon."""
        if self._shutdown_callback:
            self._shutdown_callback()
        return {"status": "ok", "message": "Shutting down"}

    async def _list_local_dirs(self, params: dict) -> dict:
        """List directories on the host filesystem for the file browser."""
        from pathlib import Path

        params = params or {}
        parent = params.get("path", str(Path.home()))

        try:
            p = Path(parent).resolve()
            if not p.is_dir():
                return {"path": str(p), "dirs": [], "error": "Not a directory"}

            dirs = []
            for entry in sorted(p.iterdir()):
                if entry.is_dir() and not entry.name.startswith("."):
                    dirs.append({"name": entry.name, "path": str(entry)})

            # Include parent for navigation
            parent_path = str(p.parent) if p.parent != p else None

            return {
                "path": str(p),
                "parent": parent_path,
                "dirs": dirs,
            }
        except PermissionError:
            return {"path": parent, "dirs": [], "error": "Permission denied"}
        except Exception as e:
            return {"path": parent, "dirs": [], "error": str(e)}

    async def _mkdir_local(self, params: dict) -> dict:
        """Create a new directory on the host filesystem."""
        from pathlib import Path

        params = params or {}
        path = params.get("path", "")
        if not path:
            return {"ok": False, "error": "No path provided"}

        try:
            p = Path(path)
            p.mkdir(parents=False, exist_ok=False)
            return {"ok": True, "path": str(p)}
        except FileExistsError:
            return {"ok": False, "error": "Directory already exists"}
        except PermissionError:
            return {"ok": False, "error": "Permission denied"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _repair(self, params: dict) -> dict:
        """Scan for remote stub files (size 0 or mismatched vs local) and delete them.

        On the next sync cycle the planner will re-classify these files as plain
        uploads and push the correct content.

        Params:
            pair_id (optional): restrict to a specific pair (e.g. "0"); default = all pairs
            dry_run (bool): if True, only report what would be deleted, default False

        Returns:
            {"repaired": N, "pairs_scanned": M, "stubs": [...path...]}
        """
        from pathlib import Path as _Path

        params = params or {}
        dry_run: bool = bool(params.get("dry_run", False))
        target_pair_id: str | None = params.get("pair_id")

        engine = self._engine
        db = self._db or (engine._db if engine else None)
        if db is None:
            raise RuntimeError("Database not available")

        pairs_to_scan: list[tuple[int, object]] = []
        for i, pair in enumerate(self._config.sync.pairs):
            if target_pair_id is not None and str(i) != str(target_pair_id):
                continue
            if not pair.enabled:
                continue
            pairs_to_scan.append((i, pair))

        stubs: list[str] = []
        pairs_scanned = 0

        for i, pair in pairs_to_scan:
            pair_id = f"pair_{i}"
            local_root = _Path(pair.local_path)

            # Resolve the cloud client for this pair
            client = None
            if engine:
                provider_name = pair.provider or "gdrive"
                client = (
                    engine._clients.get(f"{provider_name}:{pair.account_id}")
                    or engine._clients.get(pair.account_id)
                    or engine._client
                )
            if client is None:
                continue

            try:
                remote_files = await client.list_all_recursive(pair.remote_folder_id)
            except Exception as exc:
                log.warning("repair: failed to list remote for pair_%d: %s", i, exc)
                continue

            pairs_scanned += 1

            for remote in remote_files:
                path = remote.get("path", "")
                if not path:
                    continue
                local_file = local_root / path
                if not local_file.is_file():
                    continue
                local_size = local_file.stat().st_size
                remote_size = remote.get("size", -1)
                if remote_size == -1:
                    # No size info — skip
                    continue
                if remote_size == 0 and local_size > 0:
                    # Zero-byte remote stub
                    stubs.append(path)
                    if not dry_run:
                        try:
                            remote_id = remote.get("id") or remote.get("fileId")
                            if remote_id:
                                await client.delete_file(remote_id)
                            # Clear the stored sync entry so the planner treats it as new
                            await db.delete_sync_entry(path, pair_id)
                        except Exception as exc:
                            log.warning("repair: failed to delete stub %s: %s", path, exc)

        return {
            "repaired": len(stubs),
            "pairs_scanned": pairs_scanned,
            "dry_run": dry_run,
            "stubs": stubs,
        }
