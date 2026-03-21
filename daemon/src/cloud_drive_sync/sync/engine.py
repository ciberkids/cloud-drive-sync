"""Core sync orchestrator: wires watcher + poller + planner + executor."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cloud_drive_sync.config import Config, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import ChangeToken, ConflictRecord, SyncLogEntry
from cloud_drive_sync.drive.changes import ChangePoller, RemoteChange
from cloud_drive_sync.drive.client import DriveClient
from cloud_drive_sync.drive.operations import FileOperations
from cloud_drive_sync.local.hasher import md5_hash
from cloud_drive_sync.local.scanner import scan_directory, load_ignore_file, DEFAULT_IGNORE_PATTERNS
from cloud_drive_sync.local.watcher import DirectoryWatcher, LocalChange, ChangeType
from cloud_drive_sync.providers.base import CloudChangePoller, CloudClient, CloudFileOps
from cloud_drive_sync.sync.conflict import ConflictResolver
from cloud_drive_sync.sync.executor import SyncExecutor
from cloud_drive_sync.sync.planner import (
    ActionType,
    SyncAction,
    apply_sync_rules,
    filter_actions_by_mode,
    plan_continuous_sync,
    plan_initial_sync,
)
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.throttle import BandwidthThrottle

log = get_logger("sync.engine")


@dataclass
class PairStatus:
    """Runtime status for a single sync pair."""

    pair: SyncPair
    pair_id: str
    active: bool = True
    paused: bool = False
    last_sync: datetime | None = None
    active_transfers: int = 0
    errors: list[str] = field(default_factory=list)
    watcher: DirectoryWatcher | None = None
    executor: SyncExecutor | None = None
    poller: ChangePoller | None = None


class SyncEngine:
    """Orchestrates bidirectional sync across all configured pairs."""

    def __init__(
        self,
        config: Config,
        db: Database,
        drive_client: DriveClient | CloudClient | None = None,
        *,
        clients: dict[str, DriveClient | CloudClient] | None = None,
        file_ops: FileOperations | CloudFileOps | None = None,
        change_poller: ChangePoller | CloudChangePoller | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._client = drive_client
        self._clients = clients or {}
        self._ops = file_ops or (FileOperations(drive_client) if drive_client else None)
        self._poller = change_poller or (ChangePoller(drive_client) if drive_client else None)
        self._conflict_resolver = ConflictResolver(config.sync.conflict_strategy)
        self._pairs: dict[str, PairStatus] = {}
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._notify_callback = None

    @property
    def pairs(self) -> dict[str, PairStatus]:
        return self._pairs

    @property
    def conflict_resolver(self) -> ConflictResolver:
        return self._conflict_resolver

    def set_notify_callback(self, callback) -> None:
        """Set the IPC notification callback."""
        self._notify_callback = callback

    async def start(self) -> None:
        """Initialize and start sync for all enabled pairs."""
        log.info("Starting sync engine")

        # Clean up stale pairs from DB
        active_pair_ids = {f"pair_{i}" for i in range(len(self._config.sync.pairs))}
        await self._db.cleanup_stale_pairs(active_pair_ids)

        # Clean up stale partial transfer records (older than 7 days)
        await self._db.cleanup_stale_partial_transfers()

        for i, pair in enumerate(self._config.sync.pairs):
            if not pair.enabled:
                continue
            pair_id = f"pair_{i}"
            await self._start_pair(pair, pair_id)

    async def stop(self) -> None:
        """Gracefully stop all sync operations."""
        log.info("Stopping sync engine")
        self._stop_event.set()

        for ps in self._pairs.values():
            if ps.watcher:
                await ps.watcher.stop()

        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("Sync engine stopped")

    async def _start_pair(self, pair: SyncPair, pair_id: str) -> None:
        local_root = Path(pair.local_path)
        if not local_root.is_dir():
            log.error("Local path %s does not exist, skipping pair %s", local_root, pair_id)
            return

        # Resolve the client for this pair
        client = self._clients.get(pair.account_id) if pair.account_id else self._client
        if client is None:
            log.error("No client for account %s, skipping pair %s", pair.account_id, pair_id)
            return

        # Per-pair operations and poller (use injected ones if available, e.g. in tests)
        if self._ops:
            ops = self._ops
        else:
            upload_throttle = BandwidthThrottle(self._config.sync.max_upload_kbps)
            download_throttle = BandwidthThrottle(self._config.sync.max_download_kbps)
            ops = FileOperations(client, upload_throttle=upload_throttle, download_throttle=download_throttle)
        poller = self._poller or ChangePoller(client)

        executor = SyncExecutor(
            ops,
            self._db,
            local_root,
            pair_id,
            remote_folder_id=pair.remote_folder_id,
            max_concurrent=self._config.sync.max_concurrent_transfers,
            drive_client=client,
            notify_callback=self._notify_callback,
        )

        # Merge ignore patterns
        ignore_file_patterns = load_ignore_file(local_root)
        merged_patterns = DEFAULT_IGNORE_PATTERNS + list(pair.ignore_patterns) + ignore_file_patterns

        watcher = DirectoryWatcher(
            local_root, debounce_delay=self._config.sync.debounce_delay,
            ignore_hidden=pair.ignore_hidden,
            ignore_patterns=merged_patterns,
        )

        ps = PairStatus(
            pair=pair,
            pair_id=pair_id,
            watcher=watcher,
            executor=executor,
            poller=poller,
        )
        self._pairs[pair_id] = ps

        # Run initial sync
        task_init = asyncio.create_task(self._initial_sync(ps))
        self._tasks.append(task_init)

    async def _initial_sync(self, ps: PairStatus, is_manual: bool = False) -> None:
        """Perform initial full sync for a pair, then start continuous sync."""
        pair_id = ps.pair_id
        local_root = Path(ps.pair.local_path)
        log.info("Starting initial sync for %s (%s)", pair_id, local_root)

        # Clear previous errors for this pair
        ps.errors.clear()

        # Log sync start
        trigger = "Manual sync requested" if is_manual else "Automatic sync started"
        await self._db.add_log_entry(SyncLogEntry(
            action="sync", path="", pair_id=pair_id,
            status="in_progress", detail=f"{trigger} — scanning local and remote files",
        ))

        try:
            # Scan local
            ignore_file_patterns = load_ignore_file(local_root)
            merged_patterns = DEFAULT_IGNORE_PATTERNS + list(ps.pair.ignore_patterns) + ignore_file_patterns
            local_files = await scan_directory(local_root, ignore_patterns=merged_patterns, ignore_hidden=ps.pair.ignore_hidden)

            # Scan remote — use the pair's client
            pair_client = self._clients.get(ps.pair.account_id) if ps.pair.account_id else self._client
            remote_files = await pair_client.list_all_recursive(ps.pair.remote_folder_id)

            # Plan — pass provider-specific settings
            native_mimes = None
            folder_mime = None
            convert_native = self._config.sync.convert_google_docs
            if hasattr(pair_client, 'native_doc_mimes'):
                native_mimes = pair_client.native_doc_mimes
            if hasattr(pair_client, 'folder_mime_type'):
                folder_mime = pair_client.folder_mime_type
            actions = plan_initial_sync(
                local_files,
                remote_files,
                native_doc_mimes=native_mimes,
                folder_mime=folder_mime,
                convert_native_docs=convert_native and getattr(pair_client, 'supports_export', False),
            )

            # Apply advanced sync rules
            actions = apply_sync_rules(actions, ps.pair.sync_rules)

            # Handle conflicts according to strategy
            resolved_actions: list[SyncAction] = []
            for action in actions:
                if action.action == ActionType.CONFLICT:
                    result = await self._conflict_resolver.resolve(
                        path=action.path,
                        local_path=local_root / action.path,
                        local_mtime=action.local_info.mtime if action.local_info else 0,
                        remote_mtime=0,
                        conflict=ConflictRecord(
                            path=action.path,
                            pair_id=pair_id,
                            local_md5=action.local_info.md5 if action.local_info else "",
                            remote_md5=action.remote_info.get("md5Checksum", "")
                            if action.remote_info
                            else "",
                        ),
                        notify_callback=self._notify_callback,
                    )
                    if result:
                        resolved_actions.append(result)
                else:
                    resolved_actions.append(action)

            # Filter by sync mode
            resolved_actions = filter_actions_by_mode(resolved_actions, ps.pair.sync_mode)

            # Execute
            uploaded = 0
            downloaded = 0
            errors = 0
            if ps.executor:
                failed = await ps.executor.execute_all(resolved_actions)
                errors = len(failed)
                failed_paths = {a.path for a in failed} if failed else set()
                if failed:
                    ps.errors.extend(f"Failed: {a.path}" for a in failed)
                for a in resolved_actions:
                    if a.path in failed_paths:
                        continue
                    if a.action == ActionType.UPLOAD:
                        uploaded += 1
                    elif a.action in (ActionType.DOWNLOAD, ActionType.MKDIR):
                        downloaded += 1

            ps.last_sync = datetime.now(timezone.utc)
            log.info("Initial sync complete for %s", pair_id)

            # Log sync result
            total = uploaded + downloaded
            if errors > 0:
                parts = []
                if total > 0:
                    parts.append(f"{total} file{'s' if total != 1 else ''} transferred")
                parts.append(f"{errors} error{'s' if errors != 1 else ''}")
                detail = f"Sync finished with {', '.join(parts)}"
                await self._db.add_log_entry(SyncLogEntry(
                    action="sync", path="", pair_id=pair_id,
                    status="error", detail=detail,
                ))
            elif total == 0:
                await self._db.add_log_entry(SyncLogEntry(
                    action="sync", path="", pair_id=pair_id,
                    status="success", detail="Everything is up to date — nothing to sync",
                ))
            else:
                parts = []
                if uploaded > 0:
                    parts.append(f"{uploaded} uploaded")
                if downloaded > 0:
                    parts.append(f"{downloaded} downloaded")
                await self._db.add_log_entry(SyncLogEntry(
                    action="sync", path="", pair_id=pair_id,
                    status="success", detail=f"Sync complete: {', '.join(parts)}",
                ))

            # Notify UI
            if self._notify_callback:
                await self._notify_callback("sync_complete", {
                    "pair_id": pair_id,
                    "uploaded": uploaded,
                    "downloaded": downloaded,
                    "errors": errors,
                })
                await self._notify_callback("status_changed", {
                    "pair_id": pair_id,
                    "status": "idle",
                })

            # Get change token for future polling
            poller = ps.poller or self._poller
            token = await poller.get_start_page_token()
            await self._db.upsert_change_token(
                ChangeToken(pair_id=pair_id, token=token)
            )

            # Start continuous sync loops
            if not self._stop_event.is_set():
                await self._start_continuous(ps)

        except Exception as exc:
            log.exception("Initial sync failed for %s", pair_id)
            ps.errors.append("Initial sync failed")
            await self._db.add_log_entry(SyncLogEntry(
                action="sync", path="", pair_id=pair_id,
                status="error", detail=f"Sync failed: {exc}",
            ))

    async def _start_continuous(self, ps: PairStatus) -> None:
        """Start the watcher and poller loops for continuous sync."""
        if ps.watcher:
            await ps.watcher.start()

        # Local watcher loop
        task_local = asyncio.create_task(self._local_change_loop(ps))
        self._tasks.append(task_local)

        # Remote poller loop
        task_remote = asyncio.create_task(self._remote_poll_loop(ps))
        self._tasks.append(task_remote)

    async def _local_change_loop(self, ps: PairStatus) -> None:
        """Process local filesystem changes."""
        if not ps.watcher:
            return

        local_root = Path(ps.pair.local_path)

        while not self._stop_event.is_set():
            try:
                change: LocalChange = await asyncio.wait_for(
                    ps.watcher.changes.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            if ps.paused:
                continue

            try:
                stored_entries = {
                    e.path: e for e in await self._db.get_all_entries(ps.pair_id)
                }

                change_data: dict = {
                    "path": change.path,
                    "source": "local",
                    "deleted": change.change_type == ChangeType.DELETED,
                    "md5": None,
                    "mtime": 0,
                    "is_directory": change.is_directory,
                }

                if not change_data["deleted"]:
                    file_path = local_root / change.path
                    if file_path.exists() and file_path.is_file():
                        change_data["md5"] = await md5_hash(file_path)
                        change_data["mtime"] = file_path.stat().st_mtime
                    elif file_path.exists() and file_path.is_dir():
                        change_data["mtime"] = file_path.stat().st_mtime

                actions = plan_continuous_sync([change_data], stored_entries)
                actions = apply_sync_rules(actions, ps.pair.sync_rules)
                actions = filter_actions_by_mode(actions, ps.pair.sync_mode)
                if ps.executor:
                    await ps.executor.execute_all(actions)
                    ps.last_sync = datetime.now(timezone.utc)

            except Exception:
                log.exception("Error processing local change: %s", change.path)

    async def _remote_poll_loop(self, ps: PairStatus) -> None:
        """Poll for remote changes at the configured interval."""
        ct = await self._db.get_change_token(ps.pair_id)
        if not ct:
            return

        token = ct.token
        interval = self._config.sync.poll_interval
        poller = ps.poller or self._poller

        while not self._stop_event.is_set():
            if ps.paused:
                await asyncio.sleep(1)
                continue

            try:
                changes, new_token = await poller.poll_changes(token)
                token = new_token
                await self._db.upsert_change_token(
                    ChangeToken(pair_id=ps.pair_id, token=new_token)
                )
                if changes:
                    await self._process_remote_changes(ps, changes)
                    ps.last_sync = datetime.now(timezone.utc)
            except Exception:
                log.exception("Error polling remote changes for %s", ps.pair_id)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _process_remote_changes(
        self, ps: PairStatus, changes: list[RemoteChange]
    ) -> None:
        """Convert remote changes to sync actions and execute them."""
        stored_entries = {e.path: e for e in await self._db.get_all_entries(ps.pair_id)}

        # Build path mapping from remote_id -> stored path
        id_to_path: dict[str, str] = {}
        # Only track folder IDs (not file IDs) for parent matching.
        # A folder entry has local_md5=None (files always have an md5).
        tracked_folder_ids: set[str] = {ps.pair.remote_folder_id}
        for entry in stored_entries.values():
            if entry.remote_id:
                id_to_path[entry.remote_id] = entry.path
                if entry.local_md5 is None:
                    tracked_folder_ids.add(entry.remote_id)

        change_dicts: list[dict] = []
        for rc in changes:
            is_tracked = rc.file_id in id_to_path

            # For untracked files, check if their parent is a known folder
            # in our sync tree (not just any tracked entry).
            parent_path: str | None = None
            is_in_monitored_folder = False
            if not is_tracked and rc.parents:
                for pid in rc.parents:
                    if pid == ps.pair.remote_folder_id:
                        # Direct child of the sync root
                        parent_path = ""
                        is_in_monitored_folder = True
                        break
                    if pid in tracked_folder_ids and pid in id_to_path:
                        # Child of a tracked subfolder
                        parent_path = id_to_path[pid]
                        is_in_monitored_folder = True
                        break

            if not is_tracked and not is_in_monitored_folder:
                log.debug(
                    "Skipping change for unrelated file: %s (id=%s, parents=%s)",
                    rc.file_name, rc.file_id, rc.parents,
                )
                continue

            # Resolve the correct relative path within the sync tree.
            if is_tracked:
                path = id_to_path[rc.file_id]
            elif parent_path is not None and rc.file_name:
                # Build full relative path from parent's known path
                path = f"{parent_path}/{rc.file_name}" if parent_path else rc.file_name
            else:
                log.warning(
                    "Cannot resolve path for remote change: %s (id=%s)",
                    rc.file_name, rc.file_id,
                )
                continue

            change_dicts.append(
                {
                    "path": path,
                    "source": "remote",
                    "deleted": rc.removed or rc.trashed,
                    "md5": rc.md5,
                    "mtime": 0,
                    "mimeType": rc.mime_type or "",
                    "remote_id": rc.file_id,
                    "remote_info": {
                        "id": rc.file_id,
                        "name": rc.file_name,
                        "md5Checksum": rc.md5,
                        "mimeType": rc.mime_type,
                    },
                }
            )

        actions = plan_continuous_sync(change_dicts, stored_entries)
        actions = apply_sync_rules(actions, ps.pair.sync_rules)
        actions = filter_actions_by_mode(actions, ps.pair.sync_mode)
        if ps.executor:
            await ps.executor.execute_all(actions)

    # ── Public control methods ──────────────────────────────────────

    async def pause_pair(self, pair_id: str) -> bool:
        ps = self._pairs.get(pair_id)
        if not ps:
            return False
        ps.paused = True
        log.info("Paused %s", pair_id)
        return True

    async def resume_pair(self, pair_id: str) -> bool:
        ps = self._pairs.get(pair_id)
        if not ps:
            return False
        ps.paused = False
        log.info("Resumed %s", pair_id)
        return True

    async def force_sync(self, pair_id: str) -> bool:
        ps = self._pairs.get(pair_id)
        if not ps:
            return False
        asyncio.create_task(self._initial_sync(ps, is_manual=True))
        return True

    def get_status(self) -> dict:
        """Get a summary of all pairs' status."""
        result = {}
        for pid, ps in self._pairs.items():
            result[pid] = {
                "local_path": ps.pair.local_path,
                "remote_folder_id": ps.pair.remote_folder_id,
                "active": ps.active,
                "paused": ps.paused,
                "last_sync": ps.last_sync.isoformat() if ps.last_sync else None,
                "active_transfers": ps.executor.active_count if ps.executor else 0,
                "errors": ps.errors[-5:],
            }
        return result

    def get_active_transfers(self) -> list[dict]:
        """Get live transfer info across all pairs."""
        transfers = []
        for pid, ps in self._pairs.items():
            if ps.executor:
                for path, info in ps.executor._active_transfers.items():
                    transfers.append({
                        "pair_id": pid,
                        "path": path,
                        **info,
                    })
        return transfers
