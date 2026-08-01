"""IPC request dispatch: map RPC method names to handler functions."""

from __future__ import annotations

import os
import time
from typing import Any, ClassVar

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
            "get_pending_deletions": self._get_pending_deletions,
            "set_max_deletions": self._set_max_deletions,
            "get_max_deletions": self._get_max_deletions,
            "resolve_pending_deletions": self._resolve_pending_deletions,
            "emergency_stop": self._emergency_stop,
            "emergency_resume": self._emergency_resume,
            "get_stop_state": self._get_stop_state,
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
            result = await handler(self._normalise_params(request.params))
            return JsonRpcResponse.success(request.id, result)
        except TypeError as exc:
            return JsonRpcResponse.fail(request.id, INVALID_PARAMS, str(exc))
        except Exception as exc:
            log.exception("Handler error for %s", request.method)
            return JsonRpcResponse.fail(request.id, INTERNAL_ERROR, str(exc))

    @staticmethod
    def _normalise_params(params: dict | None) -> dict | None:
        """Coerce ``pair_id`` to a non-empty string, or drop it entirely.

        Pairs are addressed as ``"0"``, ``"1"`` … throughout this API, and several
        handlers use ``if pair_id:`` to choose between "this pair" and "all pairs".
        The CLI always supplies strings, so that reads correctly — but a hand-written
        HTTP client can send JSON ``{"pair_id": 0}``, and the integer ``0`` is falsy.
        ``PUT /api/settings/max-deletions {"max_deletions_per_sync": 0, "pair_id": 0}``
        therefore disabled delete protection **globally** while appearing to target
        one pair, which is the opposite of what was asked and the dangerous direction.

        Normalising here fixes the whole class rather than the handlers that happen to
        be known: after this, ``0`` arrives as ``"0"`` and is truthy, and an absent or
        blank id is absent. The consequential handlers additionally test
        ``is not None`` explicitly, so a direct method call that bypasses dispatch —
        as tests and any future front-end may do — is still correct.
        """
        if not params or "pair_id" not in params:
            return params
        pair_id = params["pair_id"]
        if pair_id is None:
            return params
        normalised = dict(params)
        text = str(pair_id).strip()
        if text:
            normalised["pair_id"] = text
        else:
            # A blank id means "unspecified"; leaving it in would look like a request
            # for a pair named "".
            normalised.pop("pair_id")
        return normalised

    def _require_engine(self) -> SyncEngine:
        """Raise if engine is not initialized (not yet authenticated)."""
        if self._engine is None:
            raise RuntimeError("Not authenticated. Please connect your Google account first.")
        return self._engine

    def _default_pair_id(self, params: dict | None) -> str:
        """Extract pair_id from params, defaulting to pair_0 if not provided."""
        params = params or {}
        pair_id = params.get("pair_id")
        if pair_id is None:
            if self._config.sync.pairs:
                pair_id = "pair_0"
            else:
                raise TypeError("No sync pairs configured")
        return pair_id

    @staticmethod
    def _engine_pair_id(pair_id: str | int) -> str:
        """Normalise a client pair id to the engine/database form.

        The client API identifies pairs by list index ("0", "1"), while the engine
        and the database use "pair_0". Both forms are accepted here so a caller
        that already has the engine form is not rejected.
        """
        text = str(pair_id)
        return text if text.startswith("pair_") else f"pair_{int(text)}"

    def _pair_by_id(self, pair_id: str | int):
        """Resolve a client pair id to its SyncPair, raising on a bad index."""
        text = str(pair_id)
        raw = text[len("pair_"):] if text.startswith("pair_") else text
        try:
            index = int(raw)
        except (TypeError, ValueError):
            raise TypeError("Invalid pair_id") from None
        if index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair_id")
        return self._config.sync.pairs[index]

    async def _get_status(self, params: dict) -> dict:
        import datetime

        from cloud_drive_sync.util.paths import socket_path

        uptime = time.monotonic() - self._start_time
        sock_path = str(socket_path())
        started_at = (
            datetime.datetime.now(datetime.UTC).astimezone()
            - datetime.timedelta(seconds=uptime)
        ).strftime("%Y-%m-%d %H:%M")

        from cloud_drive_sync import __build_date__

        daemon_info = {
            "pid": self._pid,
            "uptime": int(uptime),
            "uptime_formatted": self._format_uptime(uptime),
            "socket_path": sock_path,
            "version": self._get_version(),
            "started_at": started_at,
            "build_date": __build_date__ or None,
            "database": await self._database_info(),
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
                    "change_detection": pairs.get(pair_id, {}).get("change_detection", ""),
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
            "conflict_strategy": self._config.sync.conflict_strategy,
        }

    async def _emergency_stop(self, params: dict) -> dict:
        """Halt all activity immediately (#54).

        Scope is application-wide unless ``account_id`` is given. Cancels in-flight
        work rather than draining it; provider calls already inside a thread cannot
        be cancelled, so at most one transfer per worker finishes writing before
        stopping, with its result discarded.
        """
        engine = self._require_engine()
        account_id = (params or {}).get("account_id")
        return await engine.emergency_stop(account_id)

    async def _emergency_resume(self, params: dict) -> dict:
        """Resume after an emergency stop, for one account or everything."""
        engine = self._require_engine()
        account_id = (params or {}).get("account_id")
        return await engine.emergency_resume(account_id)

    async def _get_stop_state(self, params: dict) -> dict:
        """Whether activity is stopped, globally and per account."""
        if self._engine is None:
            # Report the persisted intent even before the engine exists, so the UI
            # does not show "running" on a daemon that starts halted.
            return {
                "stopped": self._config.sync.stopped,
                "accounts": {a.email: a.stopped for a in self._config.accounts},
            }
        return self._engine.stop_state()

    async def _get_max_deletions(self, params: dict) -> dict:
        """Current delete fail-safe limits: global defaults and per-pair overrides.

        Pairs are keyed by index string ("0", "1"), matching ``get_sync_pairs``
        and every other pair-addressing method in this API. The engine's internal
        "pair_0" form is not exposed here.
        """
        return {
            "max_deletions_per_sync": self._config.sync.max_deletions_per_sync,
            "deletion_window_seconds": self._config.sync.deletion_window_seconds,
            "pairs": {
                str(i): p.max_deletions_per_sync
                for i, p in enumerate(self._config.sync.pairs)
            },
            "pair_windows": {
                str(i): p.deletion_window_seconds
                for i, p in enumerate(self._config.sync.pairs)
            },
        }

    async def _set_max_deletions(self, params: dict) -> dict:
        """Set the delete fail-safe limit, globally or for one pair.

        ``0`` disables the guard; ``null`` on a pair restores inheritance.
        """
        params = params or {}
        pair_id = params.get("pair_id")
        window = params.get("deletion_window_seconds")

        has_limit = "max_deletions_per_sync" in params
        if not has_limit and window is None:
            raise TypeError("max_deletions_per_sync or deletion_window_seconds is required")

        value = params.get("max_deletions_per_sync")
        if has_limit and value is not None:
            value = int(value)
            if value < 0:
                raise TypeError("max_deletions_per_sync cannot be negative")
        if window is not None:
            window = int(window)
            if window < 0:
                raise TypeError("deletion_window_seconds cannot be negative")

        # `is not None`, not truthiness: "0" is a valid pair index. Dispatch also
        # normalises a JSON number to a string, so both routes are safe.
        if pair_id is not None:
            pair = self._pair_by_id(pair_id)
            if has_limit:
                pair.max_deletions_per_sync = value
            if window is not None:
                pair.deletion_window_seconds = window
        else:
            if has_limit:
                if value is None:
                    raise TypeError("the global limit cannot be null")
                self._config.sync.max_deletions_per_sync = value
            if window is not None:
                self._config.sync.deletion_window_seconds = window

        self._config.save()
        log.info(
            "Delete fail-safe set to limit=%s window=%s for %s",
            value if has_limit else "unchanged",
            f"{window}s" if window is not None else "unchanged",
            pair_id if pair_id is not None else "all pairs",
        )
        return {
            "status": "ok",
            "pair_id": pair_id,
            "max_deletions_per_sync": value if has_limit else None,
            "deletion_window_seconds": window,
        }

    async def _get_pending_deletions(self, params: dict) -> list[dict]:
        """Deletion batches the fail-safe refused, awaiting a decision (#53)."""
        db = self._db or (self._engine._db if self._engine else None)
        if db is None:
            return []
        pair_id = (params or {}).get("pair_id")
        return await db.get_pending_deletions(
            self._engine_pair_id(pair_id) if pair_id is not None else None
        )

    async def _resolve_pending_deletions(self, params: dict) -> dict:
        """Approve or reject a refused deletion batch.

        Approving does not replay the deletions directly — it clears the block and
        resumes the pair, and the next sync pass re-plans them. Replaying a stored
        batch would act on a snapshot that may be minutes old; re-planning means
        the daemon deletes what is actually still missing. If the user meanwhile
        restored the files, approving deletes nothing, which is the safe outcome.
        """
        params = params or {}
        pair_id = params.get("pair_id")
        if not pair_id:
            raise TypeError("pair_id is required")
        approve = bool(params.get("approve", False))
        engine_id = self._engine_pair_id(pair_id)

        db = self._db or (self._engine._db if self._engine else None)
        if db is None:
            raise RuntimeError("Database not available")

        pending = await db.get_pending_deletions(engine_id)
        if not pending:
            return {"status": "not_found", "pair_id": pair_id}

        stale = self._blocks_not_matching_pair(pair_id, pending)
        if stale:
            # The block describes files outside this pair's folder, so it was recorded
            # by a pair that has since been removed or renumbered. Approving would
            # hand a delete-protection bypass to a folder the user was never asked
            # about, so refuse and forget the block instead. Forgetting is safe: the
            # next pass re-detects any deletions still outstanding and asks again.
            await db.clear_pending_deletions(engine_id)
            log.warning(
                "Refused to resolve a deletion block for %s: it describes paths "
                "outside %s, so it belongs to a pair that has been removed or "
                "renumbered. The stale block has been discarded.",
                pair_id,
                stale,
            )
            return {"status": "stale", "pair_id": pair_id, "expected_under": stale}

        await db.clear_pending_deletions(engine_id)

        if approve:
            if self._engine:
                await self._engine.approve_pending_deletions(engine_id)
            log.warning(
                "User approved %d refused deletion batch(es) for %s; sync resumed",
                len(pending),
                pair_id,
            )
        else:
            log.info("User rejected the refused deletions for %s; pair stays paused", pair_id)

        return {
            "status": "approved" if approve else "rejected",
            "pair_id": pair_id,
            "batches": len(pending),
        }

    def _blocks_not_matching_pair(self, pair_id: str, pending: list) -> str | None:
        """The pair's local path, if a stored block clearly describes somewhere else.

        Returns ``None`` when the blocks look like they belong to this pair — which
        includes the case where there is nothing to compare, since refusing on missing
        evidence would strand a legitimate block with no way to approve it.

        Only local-direction samples are checked. Remote-direction samples are remote
        paths and have no relationship to ``local_path``.
        """
        try:
            pair = self._pair_by_id(pair_id)
        except Exception:
            return None
        root = (pair.local_path or "").strip()
        if not root:
            return None

        def _parts(text: str) -> list[str]:
            """Path components, separator-agnostic and case-folded.

            Compared this way rather than with ``os.path.commonpath``, which resolves
            against the *host's* flavour: on Windows it turned "/tmp/x" into
            "\\tmp\\x" and then failed to match the "/tmp/x" it was given, so every
            block looked like it belonged to another pair and no legitimate deletion
            could be approved there. Paths in the database were written by whichever
            platform recorded them, so the comparison must not assume either.
            """
            return [p.casefold() for p in text.replace("\\", "/").split("/") if p]

        root_parts = _parts(root)
        if not root_parts:
            return None

        for block in pending:
            if block.get("direction") != "local":
                continue
            for sample in block.get("sample") or []:
                if not isinstance(sample, str) or not sample.strip():
                    continue
                sample_parts = _parts(sample)
                if sample_parts[: len(root_parts)] != root_parts:
                    return root
        return None

    async def _database_info(self) -> dict | None:
        """Size and reclaimable-space gauge for ``state.db``.

        Early warning for the failure mode in issue #49, where the file reached
        4.4 GB while every table was empty: SQLite reuses freed pages but never
        shrinks the file, so bloat is invisible until someone checks disk usage.
        ``reclaimable_ratio`` is the number worth watching — a high ratio on a
        large file means most of it is dead space.
        """
        db = self._db or (self._engine._db if self._engine else None)
        if db is None:
            return None
        try:
            page_count, freelist, page_size = await db.free_page_stats()
            size = db.file_size()
        except Exception as exc:
            log.debug("Could not read database stats: %s", exc)
            return None

        reclaimable = freelist * page_size
        return {
            "size_bytes": size,
            "size_formatted": self._format_bytes(size),
            "reclaimable_bytes": reclaimable,
            "reclaimable_formatted": self._format_bytes(reclaimable),
            "reclaimable_ratio": round(freelist / page_count, 4) if page_count else 0.0,
            "page_count": page_count,
            "freelist_count": freelist,
        }

    @staticmethod
    def _format_bytes(size: float) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if abs(size) < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

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
        """Unbind pairs referencing accounts that no longer exist.

        This used to **delete** them, from a code path reached by simply *listing* the
        pairs — so a read discarded configuration, and the local path, sync mode,
        ignore patterns and rules went with it. Now the pair is unbound and disabled,
        which is the same thing removing an account does, and leaves it visible so the
        user can point it at another account.
        """
        known_emails = {a.email for a in self._config.accounts}
        changed = False
        for pair in self._config.sync.pairs:
            if pair.account_id and pair.account_id not in known_emails:
                log.warning(
                    "Sync pair %s references unknown account %s; disabling it until "
                    "it is reassigned",
                    pair.local_path,
                    pair.account_id,
                )
                pair.account_id = ""
                pair.enabled = False
                changed = True
        if changed:
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

        # An account that does not exist used to be accepted and persisted, reported
        # as success, and then the pair was silently discarded by the next listing —
        # so a mistyped address looked like it worked and the pair simply vanished.
        account_id = params.get("account_id", "")
        if account_id:
            known = {a.email for a in self._config.accounts}
            if account_id not in known:
                raise TypeError(
                    f"No account {account_id!r} is configured"
                    + (f". Known accounts: {', '.join(sorted(known))}" if known else
                       ". Add one first with 'account add'.")
                )

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
        # `or` would treat a JSON `{"id": 0}` as missing and then fail outright, so
        # pair 0 could not be removed by numeric id at all.
        id_val = params.get("id")
        if id_val is None:
            id_val = params.get("index")
        try:
            index = int(id_val)
        except (TypeError, ValueError):
            raise TypeError("Invalid pair id")
        if index < 0 or index >= len(self._config.sync.pairs):
            raise TypeError("Invalid pair id")
        removed = self._config.sync.pairs.pop(index)
        self._config.save()
        await self._forget_positional_state_from(index)
        return {"status": "removed", "local_path": removed.local_path}

    async def _forget_positional_state_from(self, index: int) -> None:
        """Drop refused-deletion blocks whose owning pair moved or disappeared.

        Pairs are identified positionally — pair ``N`` is ``pair_N`` — so removing one
        renumbers every pair after it, and any stored row keyed by the old number now
        describes a different folder. A refused deletion batch is the dangerous case:
        it survives on purpose, so that restarting cannot silently resolve a safety
        question, and ``clear_pair``/``cleanup_stale_pairs`` do not touch it. The
        result was that removing a pair left its block behind, the next pair inherited
        the id, and approving that block granted a delete-protection bypass to a
        folder the user had never been asked about.

        Forgetting the block is the fail-safe direction: nothing is deleted as a
        result, and if the deletions are still pending the next sync pass re-detects
        them and asks again. Only blocks at or after the removal point are affected —
        lower indices still mean what they did.
        """
        db = self._db or (self._engine._db if self._engine else None)
        if db is None:
            return
        remaining = len(self._config.sync.pairs)
        # +1 because the pair that was removed also had an id at `index`.
        for i in range(index, remaining + 1):
            try:
                await db.clear_pending_deletions(f"pair_{i}")
            except Exception as exc:
                log.warning("Could not clear pending deletions for pair_%d: %s", i, exc)

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
        if pair_id is not None:
            # Normalised, because `pair list` prints "0" while the engine keys on
            # "pair_0" — so `sync 0` used to match nothing and report ok anyway.
            ok = await engine.force_sync(self._engine_pair_id(pair_id))
            return {"status": "ok" if ok else "not_found", "pair_id": pair_id}
        # No pair_id supplied → sync all pairs
        await engine.force_sync_all()
        return {"status": "ok"}

    async def _pause_sync(self, params: dict) -> dict:
        """Pause one pair, or every pair when no id is given.

        Two defects here. The id was passed to the engine unnormalised, so ``pause 0``
        — the form ``pair list`` prints — looked up a pair named "0" while the engine
        keys on "pair_0", found nothing, and returned ``not_found`` which the CLI then
        reported as success. And with no id at all it paused only the *first* pair
        while the docs, the CLI output and the web UI's global toggle all say "all".
        """
        engine = self._require_engine()
        params = params or {}
        if params.get("pair_id") is None:
            count = await engine.pause_all()
            return {"status": "paused", "pairs": count}
        pair_id = self._engine_pair_id(params["pair_id"])
        ok = await engine.pause_pair(pair_id)
        return {"status": "paused" if ok else "not_found", "pair_id": params["pair_id"]}

    async def _resume_sync(self, params: dict) -> dict:
        """Resume one pair, or every pair when no id is given. See _pause_sync."""
        engine = self._require_engine()
        params = params or {}
        if params.get("pair_id") is None:
            count = await engine.resume_all()
            return {"status": "resumed", "pairs": count}
        pair_id = self._engine_pair_id(params["pair_id"])
        ok = await engine.resume_pair(pair_id)
        return {"status": "resumed" if ok else "not_found", "pair_id": params["pair_id"]}

    # Maps UI filter tab names → (status, actions) for server-side filtering.
    _FILTER_TO_ACTIONS: ClassVar[dict[str, list[str]]] = {
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

    def _delete_account_credentials(self, removed: list, email: str) -> None:
        """Delete stored credentials for each account that was actually removed.

        Nothing is deleted when nothing was removed. That matters because the email
        alone does not identify a credential file: several providers can hold an
        account for the same address, and deleting by email would take out the wrong
        one.
        """
        from cloud_drive_sync.providers.registry import get

        for account in removed:
            try:
                entry = get(account.provider)
                entry.auth_cls().delete_credentials(email)
            except Exception as exc:
                # A provider whose extra is not installed, or a credential file
                # already gone. Worth a line, but the account is removed either way
                # and failing here would leave the config half-updated.
                log.warning(
                    "Could not delete %s credentials for %s: %s",
                    account.provider,
                    email,
                    exc,
                )

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
        if not provider:
            # One address can hold accounts on several providers. Removing "all of
            # them" because only an email was given is not a reasonable reading of the
            # request, and it is how a Dropbox removal used to take out Google Drive.
            candidates = sorted({a.provider for a in self._config.accounts if a.email == email})
            if len(candidates) > 1:
                raise TypeError(
                    f"{email} has accounts on {', '.join(candidates)}. "
                    f"Say which one to remove, e.g. provider={candidates[0]!r}."
                )

        if provider:
            removed = [
                a for a in self._config.accounts
                if a.email == email and a.provider == provider
            ]
            self._config.accounts = [
                a for a in self._config.accounts
                if not (a.email == email and a.provider == provider)
            ]
            orphaned = [
                p for p in self._config.sync.pairs
                if p.account_id == email and p.provider == provider
            ]
        else:
            removed = [a for a in self._config.accounts if a.email == email]
            self._config.accounts = [a for a in self._config.accounts if a.email != email]
            orphaned = [p for p in self._config.sync.pairs if p.account_id == email]

        # Unbind the affected pairs rather than deleting them. Deleting threw away the
        # local path, sync mode, ignore patterns and rules the user had configured —
        # unrecoverably, and as a side effect of removing an account. It also
        # contradicted the documented behaviour, which is that a pair "will lose its
        # account binding and stop syncing until reassigned". Reassigning is only
        # possible if the pair still exists.
        for pair in orphaned:
            pair.account_id = ""
            pair.enabled = False

        self._config.save()

        # Delete the credentials of the accounts actually removed, via each
        # provider's own path.
        #
        # This used to unlink `account_credentials_path(email)` unconditionally — the
        # *Google Drive* credential file — whatever provider was being removed. So
        # removing a Dropbox account deleted the Google credentials for the same
        # address, breaking an account the user had not touched, while the Dropbox
        # credentials stayed on disk after the account was gone.
        self._delete_account_credentials(removed, email)

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

        # A pair id that matches nothing used to leave pairs_to_scan empty, and the
        # command then reported "everything looks healthy" having examined nothing at
        # all — the most misleading possible answer from a repair tool. Resolve the id
        # first so a typo, or the "pair_0" form, is an error rather than a silent
        # no-op. _pair_by_id raises for anything that is not a configured pair.
        target_index: int | None = None
        if target_pair_id is not None:
            self._pair_by_id(target_pair_id)
            text = str(target_pair_id)
            target_index = int(text[len("pair_"):] if text.startswith("pair_") else text)

        pairs_to_scan: list[tuple[int, object]] = []
        for i, pair in enumerate(self._config.sync.pairs):
            if target_index is not None and i != target_index:
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
