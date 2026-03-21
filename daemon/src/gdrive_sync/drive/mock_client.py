"""Mock Google Drive client and operations for demo/testing without real credentials."""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gdrive_sync.drive.changes import RemoteChange
from gdrive_sync.util.logging import get_logger

log = get_logger("drive.mock")


class MockDriveClient:
    """Drop-in replacement for DriveClient that uses a local directory as fake Drive storage.

    Metadata is tracked in memory; files are stored in a temp directory.
    """

    def __init__(self, remote_root: Path) -> None:
        self._remote_root = remote_root
        self._remote_root.mkdir(parents=True, exist_ok=True)
        # In-memory metadata store: file_id -> metadata dict
        self._files: dict[str, dict[str, Any]] = {}
        # Track folder structure: folder_id -> set of child file_ids
        self._children: dict[str, set[str]] = {"root": set()}
        # Snapshot for change detection: file_id -> md5 at last poll
        self._last_snapshot: dict[str, str | None] = {}
        self._change_token_counter = 0

        # Scan existing files in remote_root to populate initial state
        self._scan_existing()

    def _scan_existing(self) -> None:
        """Populate metadata from files already on disk."""
        for path in self._remote_root.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self._remote_root))
            file_id = f"mock_{uuid.uuid4().hex[:12]}"
            md5 = self._compute_md5(path)
            meta = {
                "id": file_id,
                "name": path.name,
                "mimeType": "application/octet-stream",
                "md5Checksum": md5,
                "modifiedTime": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
                "parents": ["root"],
                "size": str(path.stat().st_size),
                "trashed": False,
                "relativePath": rel,
                "_local_path": str(path),
            }
            self._files[file_id] = meta
            self._children.setdefault("root", set()).add(file_id)

    @staticmethod
    def _compute_md5(path: Path) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _generate_id(self) -> str:
        return f"mock_{uuid.uuid4().hex[:12]}"

    async def list_files(
        self,
        folder_id: str = "root",
        page_token: str | None = None,
        page_size: int = 100,
        query: str | None = None,
    ) -> dict[str, Any]:
        # Parse parent from query if present
        effective_folder_id = folder_id
        if query:
            parent_match = re.search(r"'([^']+)'\s+in\s+parents", query)
            if parent_match:
                effective_folder_id = parent_match.group(1)

        child_ids = self._children.get(effective_folder_id, set())
        files = [
            self._files[fid]
            for fid in child_ids
            if fid in self._files and not self._files[fid].get("trashed")
        ]

        # Apply query filters
        if query:
            mime_match = re.search(r"mimeType\s*=\s*'([^']+)'", query)
            if mime_match:
                mime_type = mime_match.group(1)
                files = [f for f in files if f.get("mimeType") == mime_type]

        return {"files": files}

    async def get_file(self, file_id: str) -> dict[str, Any]:
        if file_id not in self._files:
            raise FileNotFoundError(f"Mock file not found: {file_id}")
        return self._files[file_id]

    async def create_file(
        self,
        name: str,
        parent_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        is_folder: bool = False,
    ) -> dict[str, Any]:
        file_id = self._generate_id()
        now = datetime.now(timezone.utc).isoformat()

        if is_folder:
            meta = {
                "id": file_id,
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
                "modifiedTime": now,
                "trashed": False,
            }
            self._children[file_id] = set()
        else:
            # Copy content to remote dir
            dest = self._remote_root / name
            md5 = None
            size = 0
            if content_path:
                await asyncio.to_thread(shutil.copy2, content_path, str(dest))
                md5 = self._compute_md5(dest)
                size = dest.stat().st_size
            else:
                dest.touch()

            meta = {
                "id": file_id,
                "name": name,
                "mimeType": mime_type or "application/octet-stream",
                "md5Checksum": md5,
                "modifiedTime": now,
                "parents": [parent_id],
                "size": str(size),
                "trashed": False,
                "relativePath": name,
                "_local_path": str(dest),
            }

        self._files[file_id] = meta
        self._children.setdefault(parent_id, set()).add(file_id)
        log.debug("Mock created: %s (%s)", name, file_id)
        return dict(meta)

    async def update_file(
        self,
        file_id: str,
        content_path: str | None = None,
        mime_type: str | None = None,
        new_name: str | None = None,
    ) -> dict[str, Any]:
        if file_id not in self._files:
            raise FileNotFoundError(f"Mock file not found: {file_id}")

        meta = self._files[file_id]
        if new_name:
            old_path = Path(meta.get("_local_path", ""))
            new_path = old_path.parent / new_name
            if old_path.exists():
                await asyncio.to_thread(old_path.rename, new_path)
            meta["name"] = new_name
            meta["_local_path"] = str(new_path)

        if content_path:
            dest = Path(meta.get("_local_path", self._remote_root / meta["name"]))
            await asyncio.to_thread(shutil.copy2, content_path, str(dest))
            meta["md5Checksum"] = self._compute_md5(dest)
            meta["size"] = str(dest.stat().st_size)
            meta["_local_path"] = str(dest)

        meta["modifiedTime"] = datetime.now(timezone.utc).isoformat()
        if mime_type:
            meta["mimeType"] = mime_type

        log.debug("Mock updated: %s (%s)", meta["name"], file_id)
        return dict(meta)

    async def delete_file(self, file_id: str) -> None:
        if file_id in self._files:
            meta = self._files.pop(file_id)
            local_path = Path(meta.get("_local_path", ""))
            if local_path.exists():
                await asyncio.to_thread(local_path.unlink)
            # Remove from parent's children
            for children in self._children.values():
                children.discard(file_id)
            log.debug("Mock deleted: %s", file_id)

    async def trash_file(self, file_id: str) -> dict[str, Any]:
        if file_id not in self._files:
            raise FileNotFoundError(f"Mock file not found: {file_id}")
        self._files[file_id]["trashed"] = True
        log.debug("Mock trashed: %s", file_id)
        return self._files[file_id]

    async def export_file(self, file_id: str, mime_type: str) -> bytes:
        if file_id not in self._files:
            raise FileNotFoundError(f"Mock file not found: {file_id}")
        meta = self._files[file_id]
        local_path = Path(meta.get("_local_path", ""))
        if local_path.exists():
            return await asyncio.to_thread(local_path.read_bytes)
        return b""

    async def get_about(self) -> dict[str, Any]:
        return {
            "user": {
                "displayName": "Demo User",
                "emailAddress": "demo@gdrive-sync.local",
            },
            "storageQuota": {
                "limit": "16106127360",
                "usage": "0",
                "usageInDrive": "0",
                "usageInDriveTrash": "0",
            },
        }

    async def list_all_files(self, folder_id: str = "root") -> list[dict[str, Any]]:
        result = await self.list_files(folder_id=folder_id)
        return result.get("files", [])

    async def list_all_recursive(
        self, folder_id: str = "root", prefix: str = ""
    ) -> list[dict[str, Any]]:
        items = await self.list_all_files(folder_id)
        result: list[dict[str, Any]] = []
        for item in items:
            rel = f"{prefix}/{item['name']}" if prefix else item["name"]
            item["relativePath"] = rel
            if item.get("mimeType") == "application/vnd.google-apps.folder":
                result.append(item)
                children = await self.list_all_recursive(item["id"], rel)
                result.extend(children)
            else:
                result.append(item)
        return result


class MockChangePoller:
    """Drop-in replacement for ChangePoller that detects changes by scanning the mock remote dir."""

    def __init__(self, client: MockDriveClient) -> None:
        self._client = client
        self._token_counter = 0

    async def get_start_page_token(self) -> str:
        """Snapshot the current state and return a token."""
        self._client._last_snapshot = {
            fid: meta.get("md5Checksum")
            for fid, meta in self._client._files.items()
            if not meta.get("trashed")
        }
        self._token_counter += 1
        return str(self._token_counter)

    async def poll_changes(self, page_token: str) -> tuple[list[RemoteChange], str]:
        """Detect changes by comparing current mock FS state to last snapshot."""
        changes: list[RemoteChange] = []

        # Rescan the remote directory for new/modified files
        current_on_disk: dict[str, Path] = {}
        for path in self._client._remote_root.rglob("*"):
            if path.is_file():
                rel = str(path.relative_to(self._client._remote_root))
                current_on_disk[rel] = path

        # Build a set of known relative paths from metadata
        known_paths: dict[str, str] = {}  # rel_path -> file_id
        for fid, meta in self._client._files.items():
            if not meta.get("trashed"):
                rel = meta.get("relativePath", meta.get("name", ""))
                known_paths[rel] = fid

        # Detect new files on disk not yet in metadata
        for rel, path in current_on_disk.items():
            if rel not in known_paths:
                # New file appeared in remote dir — register it
                file_id = self._client._generate_id()
                md5 = self._client._compute_md5(path)
                meta = {
                    "id": file_id,
                    "name": path.name,
                    "mimeType": "application/octet-stream",
                    "md5Checksum": md5,
                    "modifiedTime": datetime.fromtimestamp(
                        path.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "parents": ["root"],
                    "size": str(path.stat().st_size),
                    "trashed": False,
                    "relativePath": rel,
                    "_local_path": str(path),
                }
                self._client._files[file_id] = meta
                self._client._children.setdefault("root", set()).add(file_id)

                changes.append(
                    RemoteChange(
                        file_id=file_id,
                        file_name=path.name,
                        mime_type="application/octet-stream",
                        md5=md5,
                        modified_time=meta["modifiedTime"],
                        parents=["root"],
                    )
                )

        # Detect modified files (md5 changed since snapshot)
        old_snapshot = self._client._last_snapshot
        for fid, meta in self._client._files.items():
            if meta.get("trashed"):
                if fid in old_snapshot:
                    # Was not trashed before → removal
                    changes.append(
                        RemoteChange(file_id=fid, removed=True, trashed=True)
                    )
                continue

            local_path = Path(meta.get("_local_path", ""))
            if local_path.exists():
                current_md5 = self._client._compute_md5(local_path)
                if current_md5 != meta.get("md5Checksum"):
                    # File content changed on disk
                    meta["md5Checksum"] = current_md5
                    meta["modifiedTime"] = datetime.now(timezone.utc).isoformat()

                old_md5 = old_snapshot.get(fid)
                if old_md5 is not None and current_md5 != old_md5:
                    changes.append(
                        RemoteChange(
                            file_id=fid,
                            file_name=meta.get("name"),
                            mime_type=meta.get("mimeType"),
                            md5=current_md5,
                            modified_time=meta.get("modifiedTime"),
                            parents=meta.get("parents", []),
                        )
                    )
            elif fid in old_snapshot:
                # File disappeared from disk
                changes.append(
                    RemoteChange(file_id=fid, removed=True, trashed=False)
                )

        # Update snapshot
        self._client._last_snapshot = {
            fid: meta.get("md5Checksum")
            for fid, meta in self._client._files.items()
            if not meta.get("trashed")
        }

        self._token_counter += 1
        new_token = str(self._token_counter)
        if changes:
            log.debug("Mock poll found %d changes", len(changes))
        return changes, new_token

    async def poll_loop(
        self,
        page_token: str,
        interval: float,
        callback,
        stop_event: asyncio.Event,
    ) -> str:
        current_token = page_token
        while not stop_event.is_set():
            try:
                changes, current_token = await self.poll_changes(current_token)
                if changes:
                    await callback(changes, current_token)
            except Exception:
                log.exception("Error during mock change polling")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

        return current_token


class MockFileOperations:
    """Drop-in replacement for FileOperations using the mock client."""

    def __init__(self, client: MockDriveClient) -> None:
        self._client = client

    async def upload_file(
        self,
        local_path: Path,
        remote_parent: str,
        remote_name: str | None = None,
        existing_id: str | None = None,
        progress_callback=None,
    ) -> dict[str, Any]:
        name = remote_name or local_path.name

        if existing_id:
            result = await self._client.update_file(
                existing_id, content_path=str(local_path)
            )
        else:
            result = await self._client.create_file(
                name, remote_parent, content_path=str(local_path)
            )

        log.info("Mock upload: %s -> %s", local_path.name, result["id"])
        return result

    async def download_file(
        self,
        remote_id: str,
        local_path: Path,
        progress_callback=None,
    ) -> tuple[Path, float, int, float]:
        meta = await self._client.get_file(remote_id)
        remote_file = Path(meta.get("_local_path", ""))

        local_path.parent.mkdir(parents=True, exist_ok=True)

        if remote_file.exists():
            await asyncio.to_thread(shutil.copy2, str(remote_file), str(local_path))
        else:
            local_path.touch()

        size = local_path.stat().st_size if local_path.exists() else 0
        log.info("Mock download: %s -> %s", remote_id, local_path)
        return local_path, 0.0, size, 0.0

    async def delete_remote(self, remote_id: str, trash: bool = True) -> None:
        if trash:
            await self._client.trash_file(remote_id)
        else:
            await self._client.delete_file(remote_id)
        log.info("Mock delete remote: %s (trash=%s)", remote_id, trash)
