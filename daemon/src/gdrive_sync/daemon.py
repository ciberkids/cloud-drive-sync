"""Main daemon class: component initialization, signal handling, lifecycle."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from gdrive_sync.auth.credentials import get_credentials, save_credentials
from gdrive_sync.auth.oauth import run_oauth_flow
from gdrive_sync.config import Config
from gdrive_sync.db.database import Database
from gdrive_sync.drive.client import DriveClient
from gdrive_sync.ipc.handlers import RequestHandler
from gdrive_sync.ipc.server import IpcServer
from gdrive_sync.sync.engine import SyncEngine
from gdrive_sync.util.logging import get_logger, setup_logging
from gdrive_sync.util.paths import ensure_dirs, pid_path

log = get_logger("daemon")


class Daemon:
    """The gdrive-sync daemon process."""

    def __init__(self, config_path: Path | None = None, log_level: str | None = None) -> None:
        self._config_path = config_path
        self._log_level_override = log_level
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
        log.info("gdrive-sync daemon starting")

        # Write PID file
        pid_file = pid_path()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))

        try:
            # Open database
            self._db = Database()
            await self._db.open()

            # Load credentials
            creds = get_credentials()
            client = DriveClient(creds)

            # Initialize sync engine
            self._engine = SyncEngine(self._config, self._db, client)

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

    def _do_auth(self) -> None:
        """Run the OAuth flow (called from a thread by IPC handler)."""
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
