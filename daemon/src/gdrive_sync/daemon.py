"""Main daemon class: component initialization, signal handling, lifecycle."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from gdrive_sync.config import Config, SyncPair
from gdrive_sync.db.database import Database
from gdrive_sync.ipc.handlers import RequestHandler
from gdrive_sync.ipc.server import IpcServer
from gdrive_sync.sync.engine import SyncEngine
from gdrive_sync.util.logging import get_logger, setup_logging
from gdrive_sync.util.paths import ensure_dirs, pid_path

log = get_logger("daemon")

DEMO_BASE = Path.home() / "gdrive-sync-demo"
DEMO_LOCAL = DEMO_BASE / "local"
DEMO_REMOTE = DEMO_BASE / "remote"


class Daemon:
    """The gdrive-sync daemon process."""

    def __init__(
        self,
        config_path: Path | None = None,
        log_level: str | None = None,
        demo: bool = False,
    ) -> None:
        self._config_path = config_path
        self._log_level_override = log_level
        self._demo = demo
        self._config: Config | None = None
        self._db: Database | None = None
        self._engine: SyncEngine | None = None
        self._ipc_server: IpcServer | None = None
        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        """Main entry point: initialize all components and run the event loop."""
        ensure_dirs()

        # Load config
        self._config = Config.load(self._config_path)
        level = self._log_level_override or self._config.general.log_level
        setup_logging(level)

        if self._demo:
            log.info("gdrive-sync daemon starting in DEMO mode")
        else:
            log.info("gdrive-sync daemon starting")

        # Write PID file
        pid_file = pid_path()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))

        try:
            # Open database
            self._db = Database()
            await self._db.open()

            if self._demo:
                client, file_ops, change_poller = self._setup_demo()
            else:
                from gdrive_sync.auth.credentials import get_credentials
                from gdrive_sync.drive.client import DriveClient

                creds = get_credentials()
                client = DriveClient(creds)
                file_ops = None
                change_poller = None

            # Initialize sync engine
            self._engine = SyncEngine(
                self._config,
                self._db,
                client,
                file_ops=file_ops,
                change_poller=change_poller,
            )

            # Initialize IPC
            handler = RequestHandler(self._engine, self._config)
            handler.set_auth_callback(self._do_auth)
            self._ipc_server = IpcServer(handler)
            await self._ipc_server.start()

            # Wire up notifications
            self._engine.set_notify_callback(self._ipc_server.notify_all)

            # Install signal handlers
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._signal_handler)

            # Start the sync engine
            await self._engine.start()

            # Wait for shutdown
            log.info("Daemon running (PID %d)", os.getpid())
            await self._shutdown_event.wait()

        finally:
            await self._shutdown()

    def _signal_handler(self) -> None:
        log.info("Received shutdown signal")
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        """Gracefully shut down all components."""
        log.info("Shutting down...")

        if self._engine:
            await self._engine.stop()

        if self._ipc_server:
            await self._ipc_server.stop()

        if self._db:
            await self._db.close()

        # Remove PID file
        pf = pid_path()
        if pf.exists():
            pf.unlink()

        log.info("Daemon stopped")

    def _setup_demo(self):
        """Set up mock Drive components for demo mode.

        Creates demo directories and injects a demo sync pair into the config.
        Returns (mock_client, mock_ops, mock_poller).
        """
        from gdrive_sync.drive.mock_client import (
            MockChangePoller,
            MockDriveClient,
            MockFileOperations,
        )

        DEMO_LOCAL.mkdir(parents=True, exist_ok=True)
        DEMO_REMOTE.mkdir(parents=True, exist_ok=True)

        # Inject demo sync pair if not already present
        demo_path = str(DEMO_LOCAL)
        has_demo_pair = any(
            p.local_path == demo_path for p in self._config.sync.pairs
        )
        if not has_demo_pair:
            self._config.sync.pairs.insert(
                0,
                SyncPair(local_path=demo_path, remote_folder_id="root", enabled=True),
            )

        client = MockDriveClient(DEMO_REMOTE)
        file_ops = MockFileOperations(client)
        change_poller = MockChangePoller(client)

        log.info("Demo mode: local=%s, remote=%s", DEMO_LOCAL, DEMO_REMOTE)
        return client, file_ops, change_poller

    def _do_auth(self) -> None:
        """Run the OAuth flow (called from a thread by IPC handler)."""
        if self._demo:
            log.info("Auth skipped in demo mode")
            return

        from gdrive_sync.auth.credentials import save_credentials
        from gdrive_sync.auth.oauth import run_oauth_flow

        creds = run_oauth_flow()
        save_credentials(creds)
        log.info("Re-authenticated via IPC")

    @staticmethod
    def is_running() -> bool:
        """Check whether a daemon instance is already running."""
        pf = pid_path()
        if not pf.exists():
            return False
        try:
            pid = int(pf.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, OSError):
            # Stale PID file
            pf.unlink(missing_ok=True)
            return False

    @staticmethod
    def stop_running() -> bool:
        """Send SIGTERM to a running daemon."""
        pf = pid_path()
        if not pf.exists():
            return False
        try:
            pid = int(pf.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            return True
        except (ValueError, OSError):
            return False
