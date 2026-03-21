"""Filesystem watcher using watchdog with debounced event queueing."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from cloud_drive_sync.util.logging import get_logger

log = get_logger("local.watcher")


class ChangeType(Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"


@dataclass
class LocalChange:
    """A debounced local filesystem change."""

    change_type: ChangeType
    path: str
    is_directory: bool = False
    dest_path: str | None = None  # For moves


@dataclass
class _PendingEvent:
    change: LocalChange
    timestamp: float = field(default_factory=time.monotonic)


class _EventHandler(FileSystemEventHandler):
    """Watchdog handler that feeds events into an asyncio queue."""

    def __init__(self, root: Path, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, ignore_hidden: bool = False, ignore_patterns: list[str] | None = None) -> None:
        super().__init__()
        self._root = root
        self._loop = loop
        self._queue = queue
        self._ignore_hidden = ignore_hidden
        self._ignore_patterns = ignore_patterns or []

    def _rel(self, path: str) -> str:
        try:
            return str(Path(path).relative_to(self._root))
        except ValueError:
            return path

    def _enqueue(self, change: LocalChange) -> None:
        if self._ignore_hidden and any(part.startswith(".") for part in Path(change.path).parts):
            return
        if self._ignore_patterns:
            from cloud_drive_sync.local.scanner import _is_ignored
            if _is_ignored(change.path, self._ignore_patterns):
                return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, change)

    def on_created(self, event: FileCreatedEvent | DirCreatedEvent) -> None:
        self._enqueue(
            LocalChange(ChangeType.CREATED, self._rel(event.src_path), event.is_directory)
        )

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        self._enqueue(LocalChange(ChangeType.MODIFIED, self._rel(event.src_path)))

    def on_deleted(self, event: FileDeletedEvent | DirDeletedEvent) -> None:
        self._enqueue(
            LocalChange(ChangeType.DELETED, self._rel(event.src_path), event.is_directory)
        )

    def on_moved(self, event: FileMovedEvent | DirMovedEvent) -> None:
        self._enqueue(
            LocalChange(
                ChangeType.MOVED,
                self._rel(event.src_path),
                event.is_directory,
                self._rel(event.dest_path),
            )
        )


class DirectoryWatcher:
    """Watches a directory tree and produces debounced change events."""

    def __init__(self, root: Path, debounce_delay: float = 1.0, ignore_hidden: bool = False, ignore_patterns: list[str] | None = None) -> None:
        self._root = root
        self._debounce_delay = debounce_delay
        self._ignore_hidden = ignore_hidden
        self._ignore_patterns = ignore_patterns or []
        self._observer: Observer | None = None
        self._raw_queue: asyncio.Queue[LocalChange] = asyncio.Queue()
        self._output_queue: asyncio.Queue[LocalChange] = asyncio.Queue()
        self._debounce_task: asyncio.Task | None = None

    @property
    def changes(self) -> asyncio.Queue[LocalChange]:
        """Queue of debounced changes to consume."""
        return self._output_queue

    async def start(self) -> None:
        """Start watching the directory."""
        loop = asyncio.get_running_loop()
        handler = _EventHandler(self._root, loop, self._raw_queue, self._ignore_hidden, self._ignore_patterns)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._root), recursive=True)
        self._observer.start()
        self._debounce_task = asyncio.create_task(self._debounce_loop())
        log.info("Watching %s (debounce=%.1fs)", self._root, self._debounce_delay)

    async def stop(self) -> None:
        """Stop the watcher and debounce loop."""
        if self._debounce_task:
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        log.info("Watcher stopped for %s", self._root)

    async def _debounce_loop(self) -> None:
        """Debounce raw events: coalesce rapid changes to the same path."""
        pending: dict[str, _PendingEvent] = {}

        while True:
            # Drain everything available right now
            try:
                change = await asyncio.wait_for(self._raw_queue.get(), timeout=0.2)
                key = change.path
                pending[key] = _PendingEvent(change)
            except asyncio.TimeoutError:
                pass

            # Flush events older than debounce_delay
            now = time.monotonic()
            flushed: list[str] = []
            for key, pe in pending.items():
                if now - pe.timestamp >= self._debounce_delay:
                    await self._output_queue.put(pe.change)
                    flushed.append(key)

            for key in flushed:
                del pending[key]
