"""Execute planned sync actions with concurrency control."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cloud_drive_sync.db.database import Database
from cloud_drive_sync.db.models import FileState, SyncEntry, SyncLogEntry
from cloud_drive_sync.drive.operations import FileOperations, _format_speed, _format_size
from cloud_drive_sync.sync.planner import ActionType, SyncAction
from cloud_drive_sync.util.logging import get_logger

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
        notify_callback=None,
    ) -> None:
        self._ops = ops
        self._db = db
        self._local_root = local_root
        self._pair_id = pair_id
        self._remote_folder_id = remote_folder_id
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_count = 0
        self._drive_client = drive_client
        self._notify_callback = notify_callback
        # Cache of remote folder paths -> Drive folder IDs
        self._folder_cache: dict[str, str] = {}
        # Serialize folder creation to prevent duplicate folders from races
        self._mkdir_lock = asyncio.Lock()
        # Live transfer tracking: path -> {bytes, total, speed, direction}
        self._active_transfers: dict[str, dict] = {}

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
            # Register transfer immediately so the UI can see it
            direction = action.action.value  # upload, download, mkdir, delete_local, delete_remote
            size = 0
            if action.action == ActionType.UPLOAD and action.local_info:
                size = action.local_info.size
            speed_label = "starting..." if action.action in (ActionType.UPLOAD, ActionType.DOWNLOAD) else ""
            self._active_transfers[action.path] = {
                "bytes": 0,
                "total": size,
                "speed": 0,
                "speed_formatted": speed_label,
                "direction": direction,
            }
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
                self._active_transfers.pop(action.path, None)

    async def _ensure_remote_dirs(self, rel_path: str) -> str:
        """Create any intermediate remote folders for a nested path.

        Returns the folder ID of the immediate parent folder.
        Uses client.find_child_folder() for provider-agnostic folder lookup.
        """
        parts = PurePosixPath(rel_path).parts[:-1]  # directory components only
        if not parts:
            return self._remote_folder_id

        if not self._drive_client:
            log.warning("No cloud client, cannot create remote directories for %s", rel_path)
            return self._remote_folder_id

        async with self._mkdir_lock:
            current_parent = self._remote_folder_id
            accumulated = ""
            for part in parts:
                accumulated = f"{accumulated}/{part}" if accumulated else part
                if accumulated in self._folder_cache:
                    current_parent = self._folder_cache[accumulated]
                    continue

                # Use provider-agnostic folder lookup
                folder_id = await self._drive_client.find_child_folder(current_parent, part)

                if folder_id is None:
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
        """Create directory locally and/or remotely and record it as synced."""
        local_path = self._sanitize_path(action.path)
        local_path.mkdir(parents=True, exist_ok=True)

        remote_id = None
        if action.remote_info:
            remote_id = action.remote_info.get("id")
        elif self._drive_client:
            # Local-only directory — create it on the remote side too.
            # _ensure_remote_dirs expects a file path and creates its parent dirs,
            # so we append a dummy child to create the directory itself.
            dummy_child = action.path.replace(os.sep, "/") + "/_"
            remote_id = await self._ensure_remote_dirs(dummy_child)
            # remote_id is now the ID of the directory we wanted to create

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

    def _progress_callback(self, path: str, direction: str, bytes_done: int, total: int, speed: float):
        """Called from upload/download threads to report progress."""
        self._active_transfers[path] = {
            "bytes": bytes_done,
            "total": total,
            "speed": speed,
            "speed_formatted": _format_speed(speed),
            "direction": direction,
        }
        if self._notify_callback:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._notify_callback("transfer_progress", {
                        "pair_id": self._pair_id,
                        "path": path,
                        "direction": direction,
                        "bytes": bytes_done,
                        "total": total,
                        "speed": speed,
                        "speed_formatted": _format_speed(speed),
                    }))
                )
            except RuntimeError:
                pass

    async def _do_upload(self, action: SyncAction) -> None:
        local_path = self._sanitize_path(action.path)
        if not local_path.exists():
            raise FileNotFoundError(f"Local file missing: {local_path}")

        existing_id = action.stored_entry.remote_id if action.stored_entry else None

        # Check if this is a re-upload of a converted native doc
        remote_native_mime = None
        if action.stored_entry and hasattr(action.stored_entry, 'remote_native_mime'):
            remote_native_mime = action.stored_entry.remote_native_mime

        if existing_id:
            # Update in place, no need to resolve parent
            parent_id = self._remote_folder_id
        else:
            # New file: ensure intermediate directories exist in Drive
            parent_id = await self._ensure_remote_dirs(
                action.path.replace(os.sep, "/")
            )

        def on_progress(bytes_sent, total, speed):
            self._progress_callback(action.path, "upload", bytes_sent, total, speed)

        result = await self._ops.upload_file(
            local_path=local_path,
            remote_parent=parent_id,
            remote_name=action.path.replace(os.sep, "/").split("/")[-1],
            existing_id=existing_id,
            progress_callback=on_progress,
        )

        # Extract transfer stats from result
        transfer_speed = result.pop("_transfer_speed", 0)
        transfer_size = result.pop("_transfer_size", 0)
        result.pop("_transfer_elapsed", None)

        # Store speed detail for the log entry
        action._transfer_detail = f"{_format_size(transfer_size)} at {_format_speed(transfer_speed)}"

        # Get the hash from the provider's hash field
        hash_field = "md5Checksum"
        if hasattr(self._drive_client, 'hash_field'):
            hash_field = self._drive_client.hash_field

        now = datetime.now(timezone.utc)
        stat = local_path.stat()
        entry = SyncEntry(
            path=action.path,
            pair_id=self._pair_id,
            local_md5=action.local_info.md5 if action.local_info else None,
            remote_md5=result.get(hash_field),
            remote_id=result["id"],
            state=FileState.SYNCED,
            local_mtime=stat.st_mtime,
            remote_mtime=stat.st_mtime,
            last_synced=now,
            remote_native_mime=remote_native_mime,
        )
        await self._db.upsert_sync_entry(entry)
        log.debug("Uploaded %s -> %s", action.path, result["id"])

    def _sanitize_path(self, rel_path: str) -> Path:
        """Resolve a relative path and ensure it stays within the sync root.

        Prevents path traversal attacks (e.g. '../../../etc/passwd').
        """
        resolved = (self._local_root / rel_path).resolve()
        if not resolved.is_relative_to(self._local_root.resolve()):
            raise ValueError(
                f"Path traversal detected: '{rel_path}' escapes sync root"
            )
        return resolved

    async def _do_download(self, action: SyncAction) -> None:
        remote_id = None
        if action.remote_info:
            remote_id = action.remote_info.get("id")
        elif action.stored_entry:
            remote_id = action.stored_entry.remote_id

        if not remote_id:
            raise ValueError(f"No remote ID for download: {action.path}")

        # Check if this is a native doc export
        remote_mime = None
        if action.remote_info:
            remote_mime = action.remote_info.get("mimeType", "")

        local_path = self._sanitize_path(action.path)
        remote_native_mime = None

        if (
            remote_mime
            and self._drive_client
            and hasattr(self._drive_client, 'supports_export')
            and self._drive_client.supports_export
        ):
            from cloud_drive_sync.providers.gdrive.conversion import get_export_info
            export_info = get_export_info(remote_mime)
            if export_info:
                export_mime, ext = export_info
                # Download via export
                data = await self._drive_client.export_file(remote_id, export_mime)
                export_path = local_path.with_suffix(ext)
                export_path.parent.mkdir(parents=True, exist_ok=True)
                export_path.write_bytes(data)
                remote_native_mime = remote_mime

                from cloud_drive_sync.local.hasher import md5_hash
                md5 = await md5_hash(export_path)
                stat = export_path.stat()

                action._transfer_detail = f"{_format_size(len(data))} (exported)"
                now = datetime.now(timezone.utc)
                entry = SyncEntry(
                    path=action.path,
                    pair_id=self._pair_id,
                    local_md5=md5,
                    remote_md5=None,  # Native docs don't have md5
                    remote_id=remote_id,
                    state=FileState.SYNCED,
                    local_mtime=stat.st_mtime,
                    remote_mtime=stat.st_mtime,
                    last_synced=now,
                    remote_native_mime=remote_native_mime,
                )
                await self._db.upsert_sync_entry(entry)
                log.debug("Exported %s <- %s (as %s)", action.path, remote_id, ext)
                return

        def on_progress(bytes_received, total, speed):
            self._progress_callback(action.path, "download", bytes_received, total, speed)

        _, avg_speed, size, _ = await self._ops.download_file(
            remote_id, local_path, progress_callback=on_progress,
        )
        action._transfer_detail = f"{_format_size(size)} at {_format_speed(avg_speed)}"

        from cloud_drive_sync.local.hasher import md5_hash

        md5 = await md5_hash(local_path)
        stat = local_path.stat()

        # Get the hash from the provider's hash field
        hash_field = "md5Checksum"
        if hasattr(self._drive_client, 'hash_field'):
            hash_field = self._drive_client.hash_field

        now = datetime.now(timezone.utc)
        entry = SyncEntry(
            path=action.path,
            pair_id=self._pair_id,
            local_md5=md5,
            remote_md5=action.remote_info.get(hash_field) if action.remote_info else md5,
            remote_id=remote_id,
            state=FileState.SYNCED,
            local_mtime=stat.st_mtime,
            remote_mtime=stat.st_mtime,
            last_synced=now,
        )
        await self._db.upsert_sync_entry(entry)
        log.debug("Downloaded %s <- %s", action.path, remote_id)

    async def _do_delete_local(self, action: SyncAction) -> None:
        local_path = self._sanitize_path(action.path)
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
        # Include transfer speed info if available
        if not detail and hasattr(action, "_transfer_detail"):
            detail = action._transfer_detail
        entry = SyncLogEntry(
            action=action.action.value,
            path=action.path,
            pair_id=self._pair_id,
            status=status,
            detail=detail,
        )
        await self._db.add_log_entry(entry)
