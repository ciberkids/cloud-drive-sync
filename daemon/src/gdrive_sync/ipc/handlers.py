"""IPC request dispatch: map RPC method names to handler functions."""

from __future__ import annotations

from typing import Any

from gdrive_sync.config import Config, SyncPair
from gdrive_sync.ipc.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JsonRpcRequest,
    JsonRpcResponse,
)
from gdrive_sync.sync.engine import SyncEngine
from gdrive_sync.util.logging import get_logger

log = get_logger("ipc.handlers")


class RequestHandler:
    """Dispatches JSON-RPC requests to the appropriate handler."""

    def __init__(self, engine: SyncEngine | None, config: Config) -> None:
        self._engine = engine
        self._config = config
        self._auth_callback = None
        self._db = None
        self._drive_client = None

        self._handlers: dict[str, Any] = {
            "get_status": self._get_status,
            "get_sync_pairs": self._get_sync_pairs,
            "add_sync_pair": self._add_sync_pair,
            "remove_sync_pair": self._remove_sync_pair,
            "set_conflict_strategy": self._set_conflict_strategy,
            "resolve_conflict": self._resolve_conflict,
            "force_sync": self._force_sync,
            "pause_sync": self._pause_sync,
            "resume_sync": self._resume_sync,
            "get_activity_log": self._get_activity_log,
            "get_conflicts": self._get_conflicts,
            "start_auth": self._start_auth,
            "logout": self._logout,
            "list_remote_folders": self._list_remote_folders,
            "set_sync_mode": self._set_sync_mode,
        }

    def set_auth_callback(self, callback) -> None:
        """Set a callback for handling auth flow (runs in a thread)."""
        self._auth_callback = callback

    def set_engine(self, engine: SyncEngine) -> None:
        """Set or replace the sync engine (e.g. after authentication)."""
        self._engine = engine

    def set_db(self, db) -> None:
        """Set the database reference (for logging before engine init)."""
        self._db = db

    def set_drive_client(self, client) -> None:
        """Set the drive client (for folder browsing)."""
        self._drive_client = client

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

    async def _get_status(self, params: dict) -> dict:
        if self._engine is None:
            return {
                "connected": False,
                "syncing": False,
                "paused": False,
                "error": "Not authenticated. Connect your Google account first.",
                "last_sync": None,
                "files_synced": 0,
                "active_transfers": 0,
            }

        pairs = self._engine.get_status()
        syncing = any(p.get("active_transfers", 0) > 0 for p in pairs.values())
        paused = all(p.get("paused", False) for p in pairs.values()) if pairs else False
        errors = []
        for p in pairs.values():
            errors.extend(p.get("errors", []))
        last_syncs = [p["last_sync"] for p in pairs.values() if p.get("last_sync")]
        total_transfers = sum(p.get("active_transfers", 0) for p in pairs.values())

        return {
            "connected": True,
            "syncing": syncing or total_transfers > 0,
            "paused": paused,
            "error": errors[0] if errors else None,
            "last_sync": max(last_syncs) if last_syncs else None,
            "files_synced": 0,
            "active_transfers": total_transfers,
        }

    async def _get_sync_pairs(self, params: dict) -> list[dict]:
        return [
            {
                "id": str(i),
                "local_path": p.local_path,
                "remote_folder_id": p.remote_folder_id,
                "enabled": p.enabled,
                "sync_mode": p.sync_mode,
            }
            for i, p in enumerate(self._config.sync.pairs)
        ]

    async def _add_sync_pair(self, params: dict) -> dict:
        local_path = params.get("local_path")
        remote_folder_id = params.get("remote_folder_id", "root")
        if not local_path:
            raise TypeError("local_path is required")

        pair = SyncPair(
            local_path=local_path,
            remote_folder_id=remote_folder_id,
            enabled=True,
        )
        self._config.sync.pairs.append(pair)
        self._config.save()
        pair_id = str(len(self._config.sync.pairs) - 1)
        return {
            "id": pair_id,
            "local_path": local_path,
            "remote_folder_id": remote_folder_id,
            "enabled": True,
            "sync_mode": "two_way",
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
        valid = {"keep_both", "newest_wins", "ask_user"}
        if strategy not in valid:
            raise TypeError(f"strategy must be one of {valid}")
        self._config.sync.conflict_strategy = strategy
        if self._engine:
            self._engine.conflict_resolver.strategy = strategy
        self._config.save()
        return {"status": "ok", "strategy": strategy}

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
        pair_id = params.get("pair_id")
        if not pair_id:
            raise TypeError("pair_id is required")
        ok = await engine.force_sync(pair_id)
        return {"status": "ok" if ok else "not_found"}

    async def _pause_sync(self, params: dict) -> dict:
        engine = self._require_engine()
        pair_id = params.get("pair_id")
        if not pair_id:
            raise TypeError("pair_id is required")
        ok = await engine.pause_pair(pair_id)
        return {"status": "paused" if ok else "not_found"}

    async def _resume_sync(self, params: dict) -> dict:
        engine = self._require_engine()
        pair_id = params.get("pair_id")
        if not pair_id:
            raise TypeError("pair_id is required")
        ok = await engine.resume_pair(pair_id)
        return {"status": "resumed" if ok else "not_found"}

    async def _get_activity_log(self, params: dict) -> list[dict]:
        db = self._db or (self._engine._db if self._engine else None)
        if db is None:
            return []
        limit = params.get("limit", 50)
        pair_id = params.get("pair_id")
        entries = await db.get_recent_log(limit=limit, pair_id=pair_id)
        return [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "event_type": e.action,
                "path": e.path,
                "details": e.detail or "",
                "status": e.status,
            }
            for e in entries
        ]

    async def _get_conflicts(self, params: dict) -> list[dict]:
        db = self._db or (self._engine._db if self._engine else None)
        if db is None:
            return []
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

            result = await asyncio.to_thread(self._auth_callback)
            if isinstance(result, dict):
                return result
            return {"status": "ok"}
        return {"status": "no_auth_callback"}

    async def _list_remote_folders(self, params: dict) -> dict:
        """List folders in a given parent folder on Google Drive."""
        if self._engine is None and self._drive_client is None:
            return {"folders": [], "error": "Not authenticated"}

        client = self._drive_client or self._engine._client
        parent_id = params.get("parent_id", "root")

        try:
            query = f"'{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            result = await client.list_files(query=query, page_size=100)
            folders = [
                {"id": f["id"], "name": f["name"]}
                for f in result.get("files", [])
            ]
            folders.sort(key=lambda f: f["name"].lower())
            return {"folders": folders, "parent_id": parent_id}
        except Exception as exc:
            log.error("Failed to list remote folders: %s", exc)
            return {"folders": [], "error": str(exc)}

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

    async def _logout(self, params: dict) -> dict:
        from gdrive_sync.util.paths import credentials_path, data_dir

        cred_path = credentials_path()
        salt_path = data_dir() / "token_salt"
        for p in (cred_path, salt_path):
            if p.exists():
                p.unlink()

        # Log the logout event
        if self._db:
            from gdrive_sync.db.models import SyncLogEntry

            entry = SyncLogEntry(
                action="auth",
                path="",
                pair_id="_system",
                status="success",
                detail="Logged out",
            )
            await self._db.add_log_entry(entry)

        return {"status": "logged_out"}
