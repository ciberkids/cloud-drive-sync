"""Core sync orchestrator: wires watcher + poller + planner + executor."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gdrive_sync.config import Config, SyncPair
from gdrive_sync.db.database import Database
from gdrive_sync.db.models import ChangeToken, ConflictRecord, FileState, SyncEntry
from gdrive_sync.drive.changes import ChangePoller, RemoteChange
from gdrive_sync.drive.client import DriveClient
from gdrive_sync.drive.operations import FileOperations
from gdrive_sync.local.hasher import md5_hash
from gdrive_sync.local.scanner import scan_directory
from gdrive_sync.local.watcher import DirectoryWatcher, LocalChange, ChangeType
from gdrive_sync.sync.conflict import ConflictResolver
from gdrive_sync.sync.executor import SyncExecutor
from gdrive_sync.sync.planner import (
    ActionType,
    SyncAction,
    filter_actions_by_mode,
    plan_continuous_sync,
    plan_initial_sync,
)
from gdrive_sync.util.logging import get_logger

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


class SyncEngine:
    """Orchestrates bidirectional sync across all configured pairs."""

    def __init__(
        self,
        config: Config,
        db: Database,
        drive_client: DriveClient,
        *,
        file_ops: FileOperations | None = None,
        change_poller: ChangePoller | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._client = drive_client
        self._ops = file_ops or FileOperations(drive_client)
        self._poller = change_poller or ChangePoller(drive_client)
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

        executor = SyncExecutor(
            self._ops,
            self._db,
            local_root,
            pair_id,
            remote_folder_id=pair.remote_folder_id,
            max_concurrent=self._config.sync.max_concurrent_transfers,
            drive_client=self._client,
        )

        watcher = DirectoryWatcher(
            local_root, debounce_delay=self._config.sync.debounce_delay
        )

        ps = PairStatus(
            pair=pair,
            pair_id=pair_id,
            watcher=watcher,
            executor=executor,
        )
        self._pairs[pair_id] = ps

        # Run initial sync
        task_init = asyncio.create_task(self._initial_sync(ps))
        self._tasks.append(task_init)

    async def _initial_sync(self, ps: PairStatus) -> None:
        """Perform initial full sync for a pair, then start continuous sync."""
        pair_id = ps.pair_id
        local_root = Path(ps.pair.local_path)
        log.info("Starting initial sync for %s (%s)", pair_id, local_root)

        try:
            # Scan local
            local_files = await scan_directory(local_root)

            # Scan remote
            remote_files = await self._client.list_all_recursive(ps.pair.remote_folder_id)

            # Plan
            actions = plan_initial_sync(local_files, remote_files)

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
            if ps.executor:
                failed = await ps.executor.execute_all(resolved_actions)
                if failed:
                    ps.errors.extend(f"Failed: {a.path}" for a in failed)

            ps.last_sync = datetime.now(timezone.utc)
            log.info("Initial sync complete for %s", pair_id)

            # Get change token for future polling
            token = await self._poller.get_start_page_token()
            await self._db.upsert_change_token(
                ChangeToken(pair_id=pair_id, token=token)
            )

            # Start continuous sync loops
            if not self._stop_event.is_set():
                await self._start_continuous(ps)

        except Exception:
            log.exception("Initial sync failed for %s", pair_id)
            ps.errors.append("Initial sync failed")

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
                }

                if not change_data["deleted"]:
                    file_path = local_root / change.path
                    if file_path.exists() and file_path.is_file():
                        change_data["md5"] = await md5_hash(file_path)
                        change_data["mtime"] = file_path.stat().st_mtime

                actions = plan_continuous_sync([change_data], stored_entries)
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

        while not self._stop_event.is_set():
            if ps.paused:
                await asyncio.sleep(1)
                continue

            try:
                changes, new_token = await self._poller.poll_changes(token)
                if changes:
                    await self._process_remote_changes(ps, changes)
                    token = new_token
                    await self._db.upsert_change_token(
                        ChangeToken(pair_id=ps.pair_id, token=new_token)
                    )
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
        for entry in stored_entries.values():
            if entry.remote_id:
                id_to_path[entry.remote_id] = entry.path

        change_dicts: list[dict] = []
        for rc in changes:
            path = id_to_path.get(rc.file_id, rc.file_name or rc.file_id)
            change_dicts.append(
                {
                    "path": path,
                    "source": "remote",
                    "deleted": rc.removed or rc.trashed,
                    "md5": rc.md5,
                    "mtime": 0,
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
        asyncio.create_task(self._initial_sync(ps))
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
