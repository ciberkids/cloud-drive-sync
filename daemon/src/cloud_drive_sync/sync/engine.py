"""Core sync orchestrator: wires watcher + poller + planner + executor."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cloud_drive_sync.config import Config, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import ChangeToken, ConflictRecord, SyncLogEntry
from cloud_drive_sync.drive.changes import ChangePoller, RemoteChange
from cloud_drive_sync.drive.client import DriveClient
from cloud_drive_sync.drive.operations import FileOperations
from cloud_drive_sync.local.hasher import md5_hash
from cloud_drive_sync.local.scanner import DEFAULT_IGNORE_PATTERNS, load_ignore_file, scan_directory
from cloud_drive_sync.local.watcher import ChangeType, DirectoryWatcher, LocalChange
from cloud_drive_sync.providers.base import CloudChangePoller, CloudClient, CloudFileOps
from cloud_drive_sync.sync import failsafe
from cloud_drive_sync.sync.conflict import ConflictResolver
from cloud_drive_sync.sync.executor import SyncExecutor
from cloud_drive_sync.sync.planner import (
    ActionType,
    SyncAction,
    apply_strategy_overrides,
    apply_sync_rules,
    filter_actions_by_mode,
    plan_continuous_sync,
    plan_initial_sync,
)
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.util.throttle import BandwidthThrottle

log = get_logger("sync.engine")

# How often to prune the activity log and release free database pages. Far
# longer than poll_interval: this is upkeep, not part of the sync cycle.
MAINTENANCE_INTERVAL_SECONDS = 6 * 60 * 60


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
        # Only store explicitly injected ops/poller (used in tests or single-provider mode).
        # Per-pair ops are always built in _start_pair from the provider registry so that
        # non-GDrive pairs use their own ops class rather than the GDrive FileOperations.
        self._ops = file_ops
        self._poller = change_poller
        self._conflict_resolver = ConflictResolver(config.sync.conflict_strategy)
        self._pairs: dict[str, PairStatus] = {}
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._notify_callback = None
        # Pairs whose next pass may delete freely because a user approved a
        # refused batch. One-shot: consumed by the next _deletions_allowed check,
        # otherwise approving would loop — the next pass re-plans the same
        # deletions and the guard blocks them again.
        self._delete_overrides: set[str] = set()

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

        # Re-apply any stop that was in force when the daemon last shut down.
        await self.restore_stop_state()

        # One database-wide loop, not one per pair.
        task_maint = asyncio.create_task(self._maintenance_loop())
        self._tasks.append(task_maint)

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
            try:
                local_root.mkdir(parents=True, exist_ok=True)
                log.info("Created local sync directory %s for %s", local_root, pair_id)
            except Exception as exc:
                log.error("Cannot create local sync directory %s for %s: %s", local_root, pair_id, exc)
                return

        # Resolve the client for this pair.  Use pair.provider to distinguish
        # accounts that share the same email across providers (e.g. gdrive + nextcloud
        # both registered under the same address — #12).
        provider_name = pair.provider or "gdrive"
        account = next(
            (a for a in self._config.accounts
             if a.email == pair.account_id and a.provider == provider_name),
            None,
        ) or next((a for a in self._config.accounts if a.email == pair.account_id), None)
        if pair.account_id:
            client = (
                self._clients.get(f"{provider_name}:{pair.account_id}")
                or self._clients.get(pair.account_id)
            )
        else:
            client = self._client
        if client is None:
            log.error("No client for account %s, skipping pair %s", pair.account_id, pair_id)
            return

        # Per-pair operations and poller — use injected ones if available (e.g. in tests),
        # otherwise instantiate from the provider registry so non-GDrive providers
        # use their own ops/poller instead of the GDrive-specific classes.
        if not self._ops or not self._poller:
            if provider_name == "gdrive":
                upload_throttle = BandwidthThrottle(self._config.sync.max_upload_kbps)
                download_throttle = BandwidthThrottle(self._config.sync.max_download_kbps)
                _provider_ops = FileOperations(client, upload_throttle=upload_throttle, download_throttle=download_throttle)
                _provider_poller = ChangePoller(client)
            else:
                from cloud_drive_sync.providers.registry import get as _get_provider
                try:
                    _entry = _get_provider(provider_name)
                except KeyError:
                    log.error("Unknown provider %s for pair %s, skipping", provider_name, pair_id)
                    return
                _provider_ops = _entry.ops_cls(client)
                _provider_poller = _entry.poller_cls(client)
            ops = self._ops or _provider_ops
            poller = self._poller or _provider_poller
        else:
            ops = self._ops
            poller = self._poller

        per_account = account.max_concurrent_transfers if account else 0
        max_concurrent = per_account if per_account > 0 else self._config.sync.max_concurrent_transfers

        executor = SyncExecutor(
            ops,
            self._db,
            local_root,
            pair_id,
            remote_folder_id=pair.remote_folder_id,
            max_concurrent=max_concurrent,
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

            # Scan remote — use the pair's client (provider-namespaced key,
            # fall back to bare email for backward compat).
            if ps.pair.account_id:
                pair_provider = ps.pair.provider or "gdrive"
                pair_client = (
                    self._clients.get(f"{pair_provider}:{ps.pair.account_id}")
                    or self._clients.get(ps.pair.account_id)
                )
            else:
                pair_client = self._client
            try:
                remote_files = await pair_client.list_all_recursive(ps.pair.remote_folder_id)
            except FileNotFoundError:
                if hasattr(pair_client, "ensure_root_folder"):
                    log.info(
                        "Remote root folder %r not found — creating it",
                        ps.pair.remote_folder_id,
                    )
                    await pair_client.ensure_root_folder(ps.pair.remote_folder_id)
                    remote_files = await pair_client.list_all_recursive(ps.pair.remote_folder_id)
                else:
                    raise

            # Load stored state — needed for locally-deleted file detection and
            # upload_only orphan reconcile (avoids a second DB round-trip below).
            stored_entries = {e.path: e for e in await self._db.get_all_entries(pair_id)}

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
                stored_entries=stored_entries,
            )

            # Apply advanced sync rules
            actions = apply_sync_rules(actions, ps.pair.sync_rules)

            # Apply mirror strategy overrides (local_wins / remote_wins) before
            # the mode filter so CONFLICT, DOWNLOAD, UPLOAD etc. are already
            # redirected to their authoritative equivalents.
            effective_strategy = ps.pair.conflict_strategy or self._config.sync.conflict_strategy
            actions = apply_strategy_overrides(actions, effective_strategy)

            # Apply sync-mode filter BEFORE conflict resolution so that directional
            # modes (upload_only/download_only) convert CONFLICT→UPLOAD/DOWNLOAD
            # deterministically, preventing the ConflictResolver from creating
            # _conflict_TIMESTAMP copies that would cascade on subsequent scans.
            actions = filter_actions_by_mode(actions, ps.pair.sync_mode)

            # Handle conflicts using per-pair strategy (falls back to global).
            # After mode filtering, CONFLICT actions only remain in two_way mode.
            effective_strategy = ps.pair.conflict_strategy or self._config.sync.conflict_strategy
            pair_resolver = ConflictResolver(effective_strategy)
            # Forward any pending user resolutions from the global resolver
            pair_resolver._pending_resolutions = self._conflict_resolver._pending_resolutions
            resolved_actions: list[SyncAction] = []
            for action in actions:
                if action.action == ActionType.CONFLICT:
                    result = await pair_resolver.resolve(
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
                        # Log the auto-resolution so it appears in the Activity tab (#41).
                        # ask_user strategy leaves result=None (pending) — those are
                        # logged by _mark_conflict in the executor instead.
                        if effective_strategy != "ask_user":
                            kept = "local" if result.action == ActionType.UPLOAD else "remote"
                            await self._db.add_log_entry(SyncLogEntry(
                                action="conflict",
                                path=action.path,
                                pair_id=pair_id,
                                status="success",
                                detail=f"Auto-resolved ({effective_strategy}): kept {kept} copy",
                            ))
                else:
                    resolved_actions.append(action)

            # For upload_only pairs, reconcile remote orphans: files that exist on
            # remote but no longer locally.  plan_initial_sync with stored_entries may
            # already have generated DELETE_REMOTE for some of these; the reconcile
            # block picks up any that slipped through (e.g. when stored_entries was
            # empty on the first ever run).  Only delete files we *previously* synced
            # (DB entry exists) — brand-new remote-only files are left untouched so a
            # first-time setup with existing remote content is safe.
            if ps.pair.sync_mode == "upload_only":
                remote_paths = {rf.get("relativePath", rf.get("name", "")) for rf in remote_files}
                remote_paths.discard("")
                local_paths = set(local_files.keys())
                orphan_paths = remote_paths - local_paths
                already_deleting = {
                    a.path for a in resolved_actions if a.action == ActionType.DELETE_REMOTE
                }
                orphan_actions: list[SyncAction] = []
                for opath in orphan_paths:
                    if opath in already_deleting:
                        continue
                    entry = stored_entries.get(opath)
                    if entry and entry.remote_id:
                        orphan_actions.append(SyncAction(
                            ActionType.DELETE_REMOTE,
                            opath,
                            stored_entry=entry,
                            reason="upload_only: locally deleted, removing from remote",
                        ))
                if orphan_actions:
                    log.info(
                        "upload_only reconcile: %d orphaned remote files to remove for %s",
                        len(orphan_actions), pair_id,
                    )
                    resolved_actions = resolved_actions + orphan_actions

            # Execute
            uploaded = 0
            downloaded = 0
            mkdirs = 0
            deleted = 0
            errors = 0
            uploaded_files: list[str] = []
            downloaded_files: list[str] = []
            deleted_files: list[str] = []
            conflicted_files: list[str] = []
            if ps.executor:
                if not await self._may_execute(ps, resolved_actions):
                    return
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
                        if len(uploaded_files) < 200:
                            uploaded_files.append(a.path)
                    elif a.action == ActionType.DOWNLOAD:
                        downloaded += 1
                        if len(downloaded_files) < 200:
                            downloaded_files.append(a.path)
                    elif a.action == ActionType.MKDIR:
                        mkdirs += 1
                    elif a.action in (ActionType.DELETE_LOCAL, ActionType.DELETE_REMOTE):
                        deleted += 1
                        if len(deleted_files) < 200:
                            deleted_files.append(a.path)
            # Collect unresolved conflicts (ask_user strategy)
            for a in actions:
                if a.action == ActionType.CONFLICT and len(conflicted_files) < 200:
                    conflicted_files.append(a.path)

            # Prune DB entries for paths that no longer exist on local or remote.
            # Without this, files_synced accumulates across wipe+resync cycles (#39).
            known_paths: set[str] = set(local_files.keys()) | {
                rf.get("relativePath", rf.get("name", "")) for rf in remote_files
            }
            known_paths.discard("")
            await self._db.prune_stale_entries(pair_id, known_paths)

            ps.last_sync = datetime.now(UTC)
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
            elif total == 0 and mkdirs == 0:
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
                if mkdirs > 0:
                    parts.append(f"{mkdirs} director{'ies' if mkdirs != 1 else 'y'} created")
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
                    "mkdirs": mkdirs,
                    "deleted": deleted,
                    "errors": errors,
                    "files": {
                        "uploaded": uploaded_files,
                        "downloaded": downloaded_files,
                        "deleted": deleted_files,
                        "conflicted": conflicted_files,
                    },
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
        """Process local filesystem changes with batching for concurrency."""
        if not ps.watcher:
            return

        local_root = Path(ps.pair.local_path)

        while not self._stop_event.is_set():
            # Wait for the first change
            try:
                first_change: LocalChange = await asyncio.wait_for(
                    ps.watcher.changes.get(), timeout=1.0
                )
            except TimeoutError:
                continue

            if ps.paused:
                continue

            # Collect additional pending changes (batch window)
            changes = [first_change]
            await asyncio.sleep(0.2)  # let more changes accumulate
            while True:
                try:
                    changes.append(ps.watcher.changes.get_nowait())
                except asyncio.QueueEmpty:
                    break

            try:
                stored_entries = {
                    e.path: e for e in await self._db.get_all_entries(ps.pair_id)
                }

                all_change_dicts = []
                for change in changes:
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

                    all_change_dicts.append(change_data)

                # Deduplicate by path (keep last change for each path)
                seen: dict[str, dict] = {}
                for cd in all_change_dicts:
                    seen[cd["path"]] = cd
                all_change_dicts = list(seen.values())

                actions = plan_continuous_sync(all_change_dicts, stored_entries)
                actions = apply_sync_rules(actions, ps.pair.sync_rules)
                effective_strategy = ps.pair.conflict_strategy or self._config.sync.conflict_strategy
                actions = apply_strategy_overrides(actions, effective_strategy)
                actions = filter_actions_by_mode(actions, ps.pair.sync_mode)
                if ps.executor:
                    if not await self._may_execute(ps, actions):
                        continue
                    await ps.executor.execute_all(actions)
                    ps.last_sync = datetime.now(UTC)

            except Exception:
                log.exception("Error processing local changes batch (%d changes)", len(changes))

    async def _may_execute(self, ps: PairStatus, actions: list[SyncAction]) -> bool:
        """Both gates that stand between a plan and the executor.

        Kept separate from ``_deletions_allowed`` because they are different
        concerns with different consequences: an emergency stop is a deliberate
        user action that should leave no error trail, while a refused deletion
        batch is an alarm that must be recorded and confirmed.
        """
        if self.is_stopped(ps):
            log.debug("Skipping %s: activity is stopped", ps.pair_id)
            return False
        return await self._deletions_allowed(ps, actions)

    async def _deletions_allowed(self, ps: PairStatus, actions: list[SyncAction]) -> bool:
        """Gate a planned batch through the delete fail-safe (#53).

        Returns True when the batch may proceed. On a breach, nothing runs: the
        pair is paused and the refusal is persisted for a human to confirm or
        reject. Deliberately fails closed — a daemon with nobody attached must
        wait rather than assume consent, because waiting costs a delay and
        guessing costs the data.
        """
        if ps.pair_id in self._delete_overrides:
            self._delete_overrides.discard(ps.pair_id)
            log.warning(
                "Delete fail-safe bypassed for %s this pass — a user approved the "
                "refused deletions",
                ps.pair_id,
            )
            return True

        limit = failsafe.effective_limits(
            self._config.sync.max_deletions_per_sync,
            ps.pair.max_deletions_per_sync,
        )
        if limit <= 0:
            return True

        # Only pay for the tracked count when there are deletions to weigh.
        if not failsafe.only_deletions(actions):
            return True

        window = (
            ps.pair.deletion_window_seconds
            if ps.pair.deletion_window_seconds is not None
            else self._config.sync.deletion_window_seconds
        )

        tracked = 0
        try:
            counts = await self._db.count_by_state(ps.pair_id)
            tracked = sum(counts.values())
        except Exception:
            log.debug("Could not read tracked count for %s; ratio check skipped", ps.pair_id)

        # Deletions already performed inside the window count toward the limit, so
        # a drip of just-under-limit passes cannot empty the library unnoticed.
        recent: dict[str, int] = {}
        if window > 0:
            try:
                recent = await self._db.count_recent_deletions(ps.pair_id, window)
            except Exception:
                log.debug("Could not read the recent-deletion window for %s", ps.pair_id)

        verdict = failsafe.check(
            actions,
            max_deletions=limit,
            tracked_files=tracked,
            recent_deletions=recent,
            window_seconds=window,
        )
        if not verdict.blocked:
            return True

        ps.paused = True
        for breach in verdict.breaches:
            log.error(
                "Delete fail-safe blocked %s: %s. Sync is paused for this pair until "
                "you confirm or reject the deletions.",
                ps.pair_id,
                breach.describe(),
            )
            try:
                await self._db.record_pending_deletions(
                    ps.pair_id,
                    breach.direction.value,
                    breach.count,
                    breach.tracked,
                    breach.limit,
                    breach.sample,
                )
            except Exception:
                log.exception("Could not persist the pending deletion decision")
            await self._db.add_log_entry(
                SyncLogEntry(
                    timestamp=datetime.now(UTC),
                    action="delete_blocked",
                    path=f"{breach.count} files",
                    pair_id=ps.pair_id,
                    status="error",
                    detail=breach.describe(),
                    reason="delete fail-safe",
                )
            )

        if self._notify_callback:
            with contextlib.suppress(Exception):
                await self._notify_callback(
                    "delete_blocked",
                    {"pair_id": ps.pair_id, "message": verdict.describe()},
                )
        return False

    async def _maintenance_loop(self) -> None:
        """Prune old activity rows and hand free database pages back periodically.

        Runs on its own long cadence rather than in the poll loop: the poll
        interval defaults to 30 seconds, and issuing pragmas that often for the
        rest of the process lifetime buys nothing.
        """
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=MAINTENANCE_INTERVAL_SECONDS
                )
                return  # stop requested
            except TimeoutError:
                pass
            try:
                await self._db.maintain()
            except Exception:
                log.exception("Database maintenance failed")

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
                    ps.last_sync = datetime.now(UTC)
            except Exception:
                log.exception("Error polling remote changes for %s", ps.pair_id)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except TimeoutError:
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
        effective_strategy = ps.pair.conflict_strategy or self._config.sync.conflict_strategy
        actions = apply_strategy_overrides(actions, effective_strategy)
        actions = filter_actions_by_mode(actions, ps.pair.sync_mode)
        if ps.executor:
            if not await self._may_execute(ps, actions):
                return
            await ps.executor.execute_all(actions)

    # ── Public control methods ──────────────────────────────────────

    # ── Emergency stop (#54) ────────────────────────────────────────

    def _pairs_for_account(self, account_id: str | None) -> list[PairStatus]:
        """Pairs in scope for a stop. ``None`` means every pair."""
        if account_id is None:
            return list(self._pairs.values())
        return [ps for ps in self._pairs.values() if ps.pair.account_id == account_id]

    def is_stopped(self, ps: PairStatus) -> bool:
        """Whether this pair is halted by a global or per-account stop."""
        if self._config.sync.stopped:
            return True
        account = next(
            (a for a in self._config.accounts if a.email == ps.pair.account_id), None
        )
        return bool(account and account.stopped)

    async def emergency_stop(self, account_id: str | None = None) -> dict:
        """Halt activity now, for one account or everything.

        Cancels in-flight work rather than draining it. Provider SDK calls already
        inside ``asyncio.to_thread`` cannot be cancelled, so at most one transfer
        per worker keeps writing until it returns and its result is discarded —
        everything queued behind it stops immediately.

        The flag is persisted, so a restart does not resume what a user halted.
        """
        scope = self._pairs_for_account(account_id)
        if account_id is None:
            self._config.sync.stopped = True
        else:
            for account in self._config.accounts:
                if account.email == account_id:
                    account.stopped = True

        cancelled = 0
        for ps in scope:
            ps.paused = True
            if ps.executor:
                cancelled += ps.executor.stop()
            if ps.watcher:
                with contextlib.suppress(Exception):
                    await ps.watcher.stop()

        try:
            self._config.save()
        except Exception:
            log.exception("Could not persist the emergency stop — it will not survive a restart")

        log.warning(
            "EMERGENCY STOP (%s): %d pair(s) halted, %d in-flight operation(s) cancelled",
            account_id or "all accounts",
            len(scope),
            cancelled,
        )
        if self._notify_callback:
            with contextlib.suppress(Exception):
                await self._notify_callback(
                    "activity_stopped",
                    {"account_id": account_id, "pairs": len(scope), "cancelled": cancelled},
                )
        return {
            "scope": account_id or "all",
            "pairs_stopped": len(scope),
            "operations_cancelled": cancelled,
        }

    async def emergency_resume(self, account_id: str | None = None) -> dict:
        """Undo :meth:`emergency_stop` for one account or everything.

        A per-account resume cannot override a global stop; the global one has to
        be lifted too, otherwise the button would appear to work and nothing would
        move.
        """
        if account_id is None:
            self._config.sync.stopped = False
            for account in self._config.accounts:
                account.stopped = False
        else:
            for account in self._config.accounts:
                if account.email == account_id:
                    account.stopped = False

        resumed = 0
        for ps in self._pairs_for_account(account_id):
            if self.is_stopped(ps):
                continue  # still held by a wider stop
            if ps.executor:
                ps.executor.resume()
            ps.paused = False
            if ps.watcher:
                with contextlib.suppress(Exception):
                    await ps.watcher.start()
            resumed += 1

        try:
            self._config.save()
        except Exception:
            log.exception("Could not persist the resume")

        log.warning("Activity resumed (%s): %d pair(s)", account_id or "all accounts", resumed)
        if self._notify_callback:
            with contextlib.suppress(Exception):
                await self._notify_callback(
                    "activity_resumed", {"account_id": account_id, "pairs": resumed}
                )
        return {"scope": account_id or "all", "pairs_resumed": resumed}

    def stop_state(self) -> dict:
        """Current stop state, globally and per account."""
        return {
            "stopped": self._config.sync.stopped,
            "accounts": {a.email: a.stopped for a in self._config.accounts},
        }

    async def restore_stop_state(self) -> None:
        """Re-apply persisted stops at startup.

        Without this the flag would survive the restart but the daemon would sync
        anyway, which is the failure the persistence exists to prevent.
        """
        for ps in self._pairs.values():
            if self.is_stopped(ps):
                ps.paused = True
                if ps.executor:
                    ps.executor.stop()
                log.warning(
                    "%s starts halted — activity was stopped before the last shutdown",
                    ps.pair_id,
                )

    async def approve_pending_deletions(self, pair_id: str) -> bool:
        """Let the next pass for ``pair_id`` delete without the fail-safe.

        The approval is consumed by that one pass, not stored as configuration —
        a user approving today's mass delete has not agreed to every future one.
        """
        if pair_id not in self._pairs:
            return False
        self._delete_overrides.add(pair_id)
        await self.resume_pair(pair_id)
        return True

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

    async def force_sync_all(self) -> None:
        for ps in self._pairs.values():
            asyncio.create_task(self._initial_sync(ps, is_manual=True))

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
