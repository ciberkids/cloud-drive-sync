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

    def __init__(self, engine: SyncEngine, config: Config) -> None:
        self._engine = engine
        self._config = config
        self._auth_callback = None

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
        }

    def set_auth_callback(self, callback) -> None:
        """Set a callback for handling auth flow (runs in a thread)."""
        self._auth_callback = callback

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

    async def _get_status(self, params: dict) -> dict:
        return self._engine.get_status()

    async def _get_sync_pairs(self, params: dict) -> list[dict]:
        return [
            {
                "index": i,
                "local_path": p.local_path,
                "remote_folder_id": p.remote_folder_id,
                "enabled": p.enabled,
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
        return {"status": "added", "index": len(self._config.sync.pairs) - 1}

    async def _remove_sync_pair(self, params: dict) -> dict:
        index = params.get("index")
        if index is None or index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair index")
        removed = self._config.sync.pairs.pop(index)
        self._config.save()
        return {"status": "removed", "local_path": removed.local_path}

    async def _set_conflict_strategy(self, params: dict) -> dict:
        strategy = params.get("strategy")
        valid = {"keep_both", "newest_wins", "ask_user"}
        if strategy not in valid:
            raise TypeError(f"strategy must be one of {valid}")
        self._config.sync.conflict_strategy = strategy
        self._engine.conflict_resolver.strategy = strategy
        self._config.save()
        return {"status": "ok", "strategy": strategy}

    async def _resolve_conflict(self, params: dict) -> dict:
        conflict_id = params.get("conflict_id")
        resolution = params.get("resolution")
        if conflict_id is None or resolution is None:
            raise TypeError("conflict_id and resolution are required")
        self._engine.conflict_resolver.set_user_resolution(conflict_id, resolution)
        return {"status": "ok"}

    async def _force_sync(self, params: dict) -> dict:
        pair_id = params.get("pair_id")
        if not pair_id:
            raise TypeError("pair_id is required")
        ok = await self._engine.force_sync(pair_id)
        return {"status": "ok" if ok else "not_found"}

    async def _pause_sync(self, params: dict) -> dict:
        pair_id = params.get("pair_id")
        if not pair_id:
            raise TypeError("pair_id is required")
        ok = await self._engine.pause_pair(pair_id)
        return {"status": "paused" if ok else "not_found"}

    async def _resume_sync(self, params: dict) -> dict:
        pair_id = params.get("pair_id")
        if not pair_id:
            raise TypeError("pair_id is required")
        ok = await self._engine.resume_pair(pair_id)
        return {"status": "resumed" if ok else "not_found"}

    async def _get_activity_log(self, params: dict) -> list[dict]:
        from gdrive_sync.db.database import Database

        # The engine holds a db reference; access it through the handler's engine
        db = self._engine._db
        limit = params.get("limit", 50)
        pair_id = params.get("pair_id")
        entries = await db.get_recent_log(limit=limit, pair_id=pair_id)
        return [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "action": e.action,
                "path": e.path,
                "pair_id": e.pair_id,
                "status": e.status,
                "detail": e.detail,
            }
            for e in entries
        ]

    async def _get_conflicts(self, params: dict) -> list[dict]:
        db = self._engine._db
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

            await asyncio.to_thread(self._auth_callback)
            return {"status": "ok"}
        return {"status": "no_auth_callback"}

    async def _logout(self, params: dict) -> dict:
        from gdrive_sync.util.paths import credentials_path, data_dir

        cred_path = credentials_path()
        salt_path = data_dir() / "token_salt"
        for p in (cred_path, salt_path):
            if p.exists():
                p.unlink()
        return {"status": "logged_out"}
