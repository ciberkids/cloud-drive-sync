"""Execute planned sync actions with concurrency control."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from gdrive_sync.db.database import Database
from gdrive_sync.db.models import FileState, SyncEntry, SyncLogEntry
from gdrive_sync.drive.operations import FileOperations
from gdrive_sync.sync.planner import ActionType, SyncAction
from gdrive_sync.util.logging import get_logger

log = get_logger("sync.executor")


class SyncExecutor:
    """Executes sync actions with bounded concurrency and error handling."""

    def __init__(
        self,
        ops: FileOperations,
        db: Database,
        local_root: Path,
        pair_id: str,
        remote_folder_id: str = "root",
        max_concurrent: int = 4,
        drive_client=None,
    ) -> None:
        self._ops = ops
        self._db = db
        self._local_root = local_root
        self._pair_id = pair_id
        self._remote_folder_id = remote_folder_id
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_count = 0
        self._drive_client = drive_client
        # Cache of remote folder paths -> Drive folder IDs
        self._folder_cache: dict[str, str] = {}
        # Serialize folder creation to prevent duplicate folders from races
        self._mkdir_lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return self._active_count

    async def execute_all(self, actions: list[SyncAction]) -> list[SyncAction]:
        """Execute a batch of sync actions concurrently.

        Returns list of failed actions.
        """
        # Filter out noops
        real_actions = [a for a in actions if a.action != ActionType.NOOP]
        if not real_actions:
            return []

        log.info("Executing %d sync actions", len(real_actions))
        tasks = [self._execute_one(action) for action in real_actions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        failed: list[SyncAction] = []
        for action, result in zip(real_actions, results):
            if isinstance(result, Exception):
                log.error("Action %s on %s failed: %s", action.action.value, action.path, result)
                failed.append(action)
                await self._log_action(action, "error", str(result))
            else:
                await self._log_action(action, "ok", "")

        log.info(
            "Execution complete: %d succeeded, %d failed",
            len(real_actions) - len(failed),
            len(failed),
        )
        return failed

    async def _execute_one(self, action: SyncAction) -> None:
        """Execute a single action under the semaphore."""
        async with self._semaphore:
            self._active_count += 1
            try:
                match action.action:
                    case ActionType.UPLOAD:
                        await self._do_upload(action)
                    case ActionType.DOWNLOAD:
                        await self._do_download(action)
                    case ActionType.MKDIR:
                        await self._do_mkdir(action)
                    case ActionType.DELETE_LOCAL:
                        await self._do_delete_local(action)
                    case ActionType.DELETE_REMOTE:
                        await self._do_delete_remote(action)
                    case ActionType.CONFLICT:
                        await self._mark_conflict(action)
                    case _:
                        log.warning("Unhandled action type: %s", action.action)
            finally:
                self._active_count -= 1

    async def _ensure_remote_dirs(self, rel_path: str) -> str:
        """Create any intermediate Drive folders for a nested path.

        Returns the Drive folder ID of the immediate parent folder.
        Serialized via _mkdir_lock to prevent duplicate folder creation
        when multiple uploads target the same parent concurrently.
        """
        parts = PurePosixPath(rel_path).parts[:-1]  # directory components only
        if not parts:
            return self._remote_folder_id

        if not self._drive_client:
            log.warning("No drive client, cannot create remote directories for %s", rel_path)
            return self._remote_folder_id

        async with self._mkdir_lock:
            current_parent = self._remote_folder_id
            accumulated = ""
            for part in parts:
                accumulated = f"{accumulated}/{part}" if accumulated else part
                if accumulated in self._folder_cache:
                    current_parent = self._folder_cache[accumulated]
                    continue

                # Search for existing folder
                query = (
                    f"'{current_parent}' in parents "
                    f"and name = '{part.replace(chr(39), chr(92) + chr(39))}' "
                    f"and mimeType = 'application/vnd.google-apps.folder' "
                    f"and trashed = false"
                )
                result = await self._drive_client.list_files(query=query, page_size=1)
                files = result.get("files", [])

                if files:
                    folder_id = files[0]["id"]
                else:
                    # Create the folder
                    created = await self._drive_client.create_file(
                        name=part, parent_id=current_parent, is_folder=True
                    )
                    folder_id = created["id"]
                    log.debug("Created remote folder %s -> %s", accumulated, folder_id)

                self._folder_cache[accumulated] = folder_id
                current_parent = folder_id

        return current_parent

    async def _do_mkdir(self, action: SyncAction) -> None:
        """Create a local directory and record it as synced."""
        local_path = self._local_root / action.path
        local_path.mkdir(parents=True, exist_ok=True)

        remote_id = None
        if action.remote_info:
            remote_id = action.remote_info.get("id")

        now = datetime.now(timezone.utc)
        entry = SyncEntry(
            path=action.path,
            pair_id=self._pair_id,
            local_md5=None,
            remote_md5=None,
            remote_id=remote_id,
            state=FileState.SYNCED,
            local_mtime=local_path.stat().st_mtime if local_path.exists() else 0,
            remote_mtime=0,
            last_synced=now,
        )
        await self._db.upsert_sync_entry(entry)
        log.debug("Created local directory %s", action.path)

    async def _do_upload(self, action: SyncAction) -> None:
        local_path = self._local_root / action.path
        if not local_path.exists():
            raise FileNotFoundError(f"Local file missing: {local_path}")

        existing_id = action.stored_entry.remote_id if action.stored_entry else None

        if existing_id:
            # Update in place, no need to resolve parent
            parent_id = self._remote_folder_id
        else:
            # New file: ensure intermediate directories exist in Drive
            parent_id = await self._ensure_remote_dirs(
                action.path.replace(os.sep, "/")
            )

        result = await self._ops.upload_file(
            local_path=local_path,
            remote_parent=parent_id,
            remote_name=action.path.replace(os.sep, "/").split("/")[-1],
            existing_id=existing_id,
        )

        now = datetime.now(timezone.utc)
        stat = local_path.stat()
        entry = SyncEntry(
            path=action.path,
            pair_id=self._pair_id,
            local_md5=action.local_info.md5 if action.local_info else None,
            remote_md5=result.get("md5Checksum"),
            remote_id=result["id"],
            state=FileState.SYNCED,
            local_mtime=stat.st_mtime,
            remote_mtime=stat.st_mtime,
            last_synced=now,
        )
        await self._db.upsert_sync_entry(entry)
        log.debug("Uploaded %s -> %s", action.path, result["id"])

    async def _do_download(self, action: SyncAction) -> None:
        remote_id = None
        if action.remote_info:
            remote_id = action.remote_info.get("id")
        elif action.stored_entry:
            remote_id = action.stored_entry.remote_id

        if not remote_id:
            raise ValueError(f"No remote ID for download: {action.path}")

        local_path = self._local_root / action.path
        await self._ops.download_file(remote_id, local_path)

        from gdrive_sync.local.hasher import md5_hash

        md5 = await md5_hash(local_path)
        stat = local_path.stat()

        now = datetime.now(timezone.utc)
        entry = SyncEntry(
            path=action.path,
            pair_id=self._pair_id,
            local_md5=md5,
            remote_md5=action.remote_info.get("md5Checksum") if action.remote_info else md5,
            remote_id=remote_id,
            state=FileState.SYNCED,
            local_mtime=stat.st_mtime,
            remote_mtime=stat.st_mtime,
            last_synced=now,
        )
        await self._db.upsert_sync_entry(entry)
        log.debug("Downloaded %s <- %s", action.path, remote_id)

    async def _do_delete_local(self, action: SyncAction) -> None:
        local_path = self._local_root / action.path
        if local_path.exists():
            if local_path.is_dir():
                import shutil

                shutil.rmtree(local_path)
            else:
                local_path.unlink()
            log.debug("Deleted local %s", action.path)
        await self._db.delete_sync_entry(action.path, self._pair_id)
        # Also clean up any child entries if this was a directory
        await self._db.delete_sync_entries_by_prefix(action.path, self._pair_id)

    async def _do_delete_remote(self, action: SyncAction) -> None:
        remote_id = action.stored_entry.remote_id if action.stored_entry else None
        if not remote_id:
            raise ValueError(f"No remote ID for deletion: {action.path}")
        await self._ops.delete_remote(remote_id, trash=True)
        await self._db.delete_sync_entry(action.path, self._pair_id)
        # Also clean up any child entries if this was a directory
        await self._db.delete_sync_entries_by_prefix(action.path, self._pair_id)
        log.debug("Deleted remote %s (%s)", action.path, remote_id)

    async def _mark_conflict(self, action: SyncAction) -> None:
        if action.stored_entry:
            action.stored_entry.state = FileState.CONFLICT
            await self._db.upsert_sync_entry(action.stored_entry)
        log.warning("Conflict flagged: %s", action.path)

    async def _log_action(self, action: SyncAction, status: str, detail: str) -> None:
        entry = SyncLogEntry(
            action=action.action.value,
            path=action.path,
            pair_id=self._pair_id,
            status=status,
            detail=detail,
        )
        await self._db.add_log_entry(entry)
