"""Main daemon class: component initialization, signal handling, lifecycle."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from cloud_drive_sync.config import Account, Config, SyncPair
from cloud_drive_sync.db.database import Database
from cloud_drive_sync.ipc.handlers import RequestHandler
from cloud_drive_sync.ipc.server import IpcServer
from cloud_drive_sync.providers.base import CloudClient
from cloud_drive_sync.sync.engine import SyncEngine
from cloud_drive_sync.util.logging import get_logger, setup_logging
from cloud_drive_sync.util.paths import ensure_dirs, pid_path

log = get_logger("daemon")

DEMO_BASE = Path.home() / "cloud-drive-sync-demo"
DEMO_LOCAL = DEMO_BASE / "local"
DEMO_REMOTE = DEMO_BASE / "remote"


class Daemon:
    """The cloud-drive-sync daemon process."""

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
        self._handler: RequestHandler | None = None
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
            log.info("cloud-drive-sync daemon starting in DEMO mode")
        else:
            log.info("cloud-drive-sync daemon starting")

        # Write PID file
        pid_file = pid_path()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))

        try:
            # Open database
            self._db = Database()
            await self._db.open()

            if self._demo:
                client, file_ops, change_poller = await self._setup_demo()
                clients = {"": client} if client else {}
            else:
                from cloud_drive_sync.auth.credentials import load_account_credentials, load_credentials
                from cloud_drive_sync.drive.client import DriveClient

                # Ensure Google Drive provider is registered
                import cloud_drive_sync.providers.gdrive  # noqa: F401

                client = None
                file_ops = None
                change_poller = None
                clients: dict[str, CloudClient] = {}

                # Migration: if old single credentials.enc exists but no accounts configured
                from cloud_drive_sync.util.paths import credentials_path
                old_creds_path = credentials_path()
                if old_creds_path.exists() and not self._config.accounts:
                    log.info("Migrating single-account credentials to multi-account format")
                    creds = load_credentials()
                    if creds and creds.valid:
                        try:
                            from cloud_drive_sync.auth.credentials import save_account_credentials

                            temp_client = DriveClient(creds, proxy=self._config.proxy)
                            about = await temp_client.get_about()
                            email = about.get("user", {}).get("emailAddress", "unknown")

                            save_account_credentials(creds, email)
                            self._config.accounts.append(Account(email=email, display_name=email))

                            for pair in self._config.sync.pairs:
                                if not pair.account_id:
                                    pair.account_id = email

                            self._config.save()
                            log.info("Migration complete: account=%s", email)
                        except Exception as exc:
                            log.warning("Migration failed, keeping legacy mode: %s", exc)

                # Load per-account credentials using provider registry
                for account in self._config.accounts:
                    provider_name = account.provider or "gdrive"
                    if provider_name == "gdrive":
                        acct_creds = load_account_credentials(account.email)
                        if acct_creds and acct_creds.valid:
                            clients[account.email] = DriveClient(acct_creds, proxy=self._config.proxy)
                            log.info("Loaded credentials for %s (gdrive)", account.email)
                        else:
                            log.warning("No valid credentials for %s", account.email)
                    else:
                        # Use provider registry for non-Google providers
                        try:
                            from cloud_drive_sync.providers.registry import get
                            provider = get(provider_name)
                            if not provider.available:
                                log.warning(
                                    "Provider %s is not available yet, skipping %s",
                                    provider_name, account.email,
                                )
                                continue
                            auth = provider.auth_cls()
                            creds = auth.load_credentials(account.email)
                            if creds:
                                provider_client = await auth.create_client(creds)
                                clients[account.email] = provider_client
                                log.info("Loaded credentials for %s (%s)", account.email, provider_name)
                            else:
                                log.warning("No valid credentials for %s (%s)", account.email, provider_name)
                        except KeyError:
                            log.warning("Unknown provider %s for account %s", provider_name, account.email)

                # Legacy: if no accounts configured but old credentials exist
                if not self._config.accounts:
                    creds = load_credentials()
                    if creds and creds.valid:
                        log.info("Loaded existing legacy credentials")
                        client = DriveClient(creds, proxy=self._config.proxy)
                        clients[""] = client

                if clients:
                    client = next(iter(clients.values()))

            # Initialize sync engine only if we have a client
            if client is not None:
                self._engine = SyncEngine(
                    self._config,
                    self._db,
                    client,
                    clients=clients,
                    file_ops=file_ops,
                    change_poller=change_poller,
                )

            # Initialize IPC (works with or without engine)
            handler = RequestHandler(self._engine, self._config)
            handler.set_auth_callback(self._do_auth)
            handler.set_db(self._db)
            self._handler = handler
            self._ipc_server = IpcServer(handler)
            await self._ipc_server.start()

            # Wire up notifications if engine is ready
            if self._engine:
                self._engine.set_notify_callback(self._ipc_server.notify_all)

            # Install signal handlers
            loop = asyncio.get_running_loop()
            if sys.platform != "win32":
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, self._signal_handler)

            # Start the sync engine if we have credentials
            if self._engine:
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

    async def _setup_demo(self):
        """Set up mock Drive components for demo mode.

        Creates demo directories and injects a demo sync pair into the config.
        Returns (mock_client, mock_ops, mock_poller).
        """
        from cloud_drive_sync.drive.mock_client import (
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

        # Seed sample folders so the remote folder browser has something to show
        await client.create_file("Documents", "root", is_folder=True)
        await client.create_file("Photos", "root", is_folder=True)

        file_ops = MockFileOperations(client)
        change_poller = MockChangePoller(client)

        log.info("Demo mode: local=%s, remote=%s", DEMO_LOCAL, DEMO_REMOTE)
        return client, file_ops, change_poller

    def _do_auth(self, provider: str = "gdrive", headless: bool = False) -> dict:
        """Run the auth flow for a given provider (called from a thread by IPC handler)."""
        if self._demo:
            log.info("Auth skipped in demo mode")
            return {"status": "ok", "message": "Demo mode — no real auth needed"}

        from cloud_drive_sync.providers.registry import get as get_provider

        self._log_auth_event("auth", f"Authentication started ({provider})", "in_progress")

        try:
            entry = get_provider(provider)
            auth_provider = entry.auth_cls()

            # Run the provider-specific auth flow
            creds = auth_provider.run_auth_flow(headless=headless)

            # Create client from credentials
            import asyncio
            loop = asyncio.get_event_loop()

            async def _setup():
                client = await auth_provider.create_client(creds)
                email = await auth_provider.get_account_email(client)
                return client, email

            future = asyncio.run_coroutine_threadsafe(_setup(), loop)
            client, email = future.result(timeout=30)

            # Save credentials
            auth_provider.save_credentials(creds, email)

            # Add account to config if not exists
            if not any(a.email == email for a in self._config.accounts):
                self._config.accounts.append(
                    Account(email=email, display_name=email, provider=provider)
                )
                self._config.save()

            self._log_auth_event("auth", f"Authentication successful ({email})", "success")

            # Initialize or update engine with the new client
            if self._engine is None and self._db is not None:
                clients = {email: client}
                self._engine = SyncEngine(
                    self._config,
                    self._db,
                    client,
                    clients=clients,
                )
                self._handler.set_engine(self._engine)
                self._handler.set_drive_client(client)

                if self._ipc_server:
                    self._engine.set_notify_callback(self._ipc_server.notify_all)

                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._engine.start())
                )
                log.info("Sync engine initialized after authentication")

            elif self._engine is not None:
                self._engine._clients[email] = client
                if not self._engine._client:
                    self._engine._client = client

                self._handler.set_drive_client(client)

                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._restart_engine())
                )
                log.info("Added %s account %s, engine restarted", provider, email)

            return {"status": "ok", "email": email}

        except Exception as exc:
            log.error("Authentication failed: %s", exc)
            self._log_auth_event("auth", f"Authentication failed: {exc}", "error")
            return {"status": "error", "message": str(exc)}

    async def _restart_engine(self) -> None:
        """Stop and restart the sync engine (e.g. after credential refresh)."""
        if self._engine:
            await self._engine.stop()
            await self._engine.start()

    def _log_auth_event(self, action: str, detail: str, status: str) -> None:
        """Log an auth event to the activity database."""
        if self._db is None:
            return
        import asyncio
        from cloud_drive_sync.db.models import SyncLogEntry

        entry = SyncLogEntry(
            action=action,
            path="",
            pair_id="_system",
            status=status,
            detail=detail,
        )
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._db.add_log_entry(entry))
            )
        except RuntimeError:
            pass

    @staticmethod
    def is_running() -> bool:
        """Check whether a daemon instance is already running."""
        pf = pid_path()
        if not pf.exists():
            return False
        try:
            pid = int(pf.read_text().strip())
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            else:
                os.kill(pid, 0)
                return True
        except (ValueError, OSError):
            # Stale PID file
            pf.unlink(missing_ok=True)
            return False

    @staticmethod
    def stop_running() -> bool:
        """Send stop signal to a running daemon."""
        pf = pid_path()
        if not pf.exists():
            return False
        try:
            pid = int(pf.read_text().strip())
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(1, False, pid)
                if handle:
                    kernel32.TerminateProcess(handle, 0)
                    kernel32.CloseHandle(handle)
            else:
                os.kill(pid, signal.SIGTERM)
            return True
        except (ValueError, OSError):
            return False
