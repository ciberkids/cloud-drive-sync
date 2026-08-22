"""Main daemon class: component initialization, signal handling, lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
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
        http_port: int = 0,
        http_host: str = "0.0.0.0",
        http_token: str | None = None,
        mcp_token: str | None = None,
        mcp_port: int = 0,
        mcp_host: str = "0.0.0.0",
        mcp_allow_writes: bool = False,
        mcp_allowed_hosts: tuple[str, ...] = (),
    ) -> None:
        self._config_path = config_path
        self._log_level_override = log_level
        self._demo = demo
        self._http_port = http_port
        self._http_host = http_host
        self._http_token = http_token or None
        self._mcp_token = mcp_token or None
        self._mcp_port = mcp_port
        self._mcp_host = mcp_host
        self._mcp_allow_writes = mcp_allow_writes
        self._mcp_allowed_hosts = mcp_allowed_hosts
        self._mcp_server = None
        self._config: Config | None = None
        self._db: Database | None = None
        self._engine: SyncEngine | None = None
        self._handler: RequestHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._webhook_delivery = None
        self._webhook_dispatcher = None
        self._ipc_server: IpcServer | None = None
        self._http_server = None
        self._shutdown_event = asyncio.Event()

    def _resolve_http_token(self, first_run: bool) -> None:
        """Settle which token the HTTP front-end requires, generating one if new.

        Precedence is ``--http-token`` / ``CDS_HTTP_TOKEN``, then the config file.
        On a **fresh install** with neither, a token is generated and stored, so a
        new deployment is protected by default.

        Upgrades are deliberately untouched. Turning auth on for an existing install
        would lock people out of a web UI they have bookmarked, with no way to learn
        the new token except reading the logs of a service they can no longer reach
        through the UI. Those keep the previous behaviour and the startup warning.
        """
        if self._http_token:
            return  # an explicit flag or environment variable always wins

        if self._config.http.token:
            self._http_token = self._config.http.token
            return

        if not first_run or self._demo:
            # Demo mode is excluded because it shares the real config file, and
            # writing a token there as a side effect of a demo run is a surprise.
            return

        from cloud_drive_sync.http.auth import generate_token

        token = generate_token()
        self._config.http.token = token
        try:
            self._config.save()
        except Exception as exc:
            # Keep the token for this session rather than starting unprotected, but
            # be explicit that it will differ after a restart.
            log.warning(
                "Generated an access token but could not save it (%s); it will "
                "change when the daemon restarts",
                exc,
            )
        self._http_token = token

        if self._http_port > 0:
            # Written to stdout, deliberately NOT through the logger.
            #
            # The operator has to see this once — `docker logs`, or the console, or
            # journald — but the logger also writes to a rotating file on disk, and a
            # secret in a log file outlives its usefulness: it survives in rotated
            # copies, and gets swept up by anything shipping logs elsewhere. Tightening
            # that file's mode would not fix either of those. stdout reaches the places
            # the operator actually reads and nowhere else.
            print(
                "\n"
                "  ┌─ First run: an access token was generated ───────────────\n"
                "  │\n"
                f"  │    {token}\n"
                "  │\n"
                f"  │  Open http://<host>:{self._http_port} and paste it in to sign in.\n"
                "  │  Stored in your config file under [http] token.\n"
                "  └──────────────────────────────────────────────────────────",
                flush=True,
            )
            # The log records that it happened, and where to look — never the value.
            log.warning(
                "First run: generated an access token and stored it under [http] token "
                "in the config file. It was printed to stdout; it is deliberately not "
                "written to the log file."
            )
        else:
            log.info(
                "First run: generated an access token and stored it under [http] "
                "token, ready for whenever the HTTP port is enabled"
            )

    async def _load_account_client(
        self, account, clients: dict, load_account_credentials, DriveClient
    ) -> None:
        """Build and register the cloud client for one configured account.

        Raising here is contained by the caller, so a single broken account cannot
        stop the daemon from starting.
        """
        provider_name = account.provider or "gdrive"
        if provider_name == "gdrive":
            acct_creds = load_account_credentials(account.email)
            if acct_creds and acct_creds.valid:
                clients[f"gdrive:{account.email}"] = DriveClient(
                    acct_creds, proxy=self._config.proxy
                )
                log.info("Loaded credentials for %s (gdrive)", account.email)
            else:
                log.warning("No valid credentials for %s", account.email)
            return

        from cloud_drive_sync.providers.registry import get

        try:
            provider = get(provider_name)
        except KeyError:
            log.warning("Unknown provider %s for account %s", provider_name, account.email)
            return

        if not provider.available:
            log.warning(
                "Provider %s is not available yet, skipping %s",
                provider_name,
                account.email,
            )
            return

        creds = provider.auth_cls().load_credentials(account.email)
        if not creds:
            log.warning("No valid credentials for %s (%s)", account.email, provider_name)
            return
        clients[f"{provider_name}:{account.email}"] = await provider.auth_cls().create_client(
            creds
        )
        log.info("Loaded credentials for %s (%s)", account.email, provider_name)

    async def run(self) -> None:
        """Main entry point: initialize all components and run the event loop."""
        self._loop = asyncio.get_running_loop()
        ensure_dirs()

        # Whether this is a fresh install has to be decided before anything writes a
        # config, and Config.load deliberately does not create the file when absent.
        from cloud_drive_sync.util.paths import config_path as _config_path

        first_run = not (self._config_path or _config_path()).exists()

        # Load config
        self._config = Config.load(self._config_path)
        level = self._log_level_override or self._config.general.log_level
        setup_logging(level)

        if self._demo:
            log.info("cloud-drive-sync daemon starting in DEMO mode")
        else:
            log.info("cloud-drive-sync daemon starting")

        self._resolve_http_token(first_run)

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
                import cloud_drive_sync.providers.box
                import cloud_drive_sync.providers.dropbox

                # Ensure all providers are registered (each __init__ guards missing deps)
                import cloud_drive_sync.providers.gdrive
                import cloud_drive_sync.providers.nextcloud
                import cloud_drive_sync.providers.onedrive
                import cloud_drive_sync.providers.proton  # noqa: F401
                from cloud_drive_sync.auth.credentials import (
                    load_account_credentials,
                    load_credentials,
                )
                from cloud_drive_sync.drive.client import DriveClient

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

                # Load each account independently. Only KeyError used to be caught,
                # so anything else — an unreadable credential file, a decryption
                # failure, a provider SDK raising while building its client — escaped
                # the loop and aborted startup for *every* account. One damaged file
                # meant the daemon refused to start, when the right outcome is that the
                # one account shows as disconnected.
                for account in self._config.accounts:
                    try:
                        await self._load_account_client(
                            account, clients, load_account_credentials, DriveClient
                        )
                    except Exception as exc:
                        log.warning(
                            "Could not load account %s (%s): %s — that account will "
                            "show as disconnected; the others are unaffected",
                            account.email,
                            account.provider or "gdrive",
                            exc,
                        )

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
            handler.set_exchange_code_callback(self._exchange_auth_code)
            handler.set_shutdown_callback(lambda: self._shutdown_event.set())
            handler.set_db(self._db)
            self._handler = handler
            self._ipc_server = IpcServer(handler)
            await self._ipc_server.start()

            # Start HTTP REST API server if port specified
            if self._http_port > 0:
                from cloud_drive_sync.http.server import HttpServer
                self._http_server = HttpServer(
                    handler,
                    host=self._http_host,
                    port=self._http_port,
                    auth_token=self._http_token,
                )
                await self._http_server.start()

            # Start MCP server for AI assistants if port specified
            if self._mcp_port > 0:
                await self._start_mcp(handler)

            # Wire up notifications if engine is ready
            if self._engine:
                self._engine.set_notify_callback(self._ipc_server.notify_all)
                await self._start_webhooks()

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

    async def _start_mcp(self, handler) -> None:
        """Start the MCP server, or explain why it cannot start.

        A missing optional extra must not take the daemon down with it — the rest
        of the daemon is unaffected by MCP being unavailable.
        """
        from cloud_drive_sync.mcp import is_available

        if not is_available():
            log.error(
                "--mcp-port %d was given but the MCP extra is not installed; "
                "install it with: pip install 'cloud-drive-sync[mcp]'",
                self._mcp_port,
            )
            return

        from cloud_drive_sync.mcp.server import McpServer

        self._mcp_server = McpServer(
            handler,
            host=self._mcp_host,
            port=self._mcp_port,
            allow_writes=self._mcp_allow_writes,
            allowed_hosts=self._mcp_allowed_hosts or None,
            auth_token=self._mcp_token,
        )
        try:
            await self._mcp_server.start()
        except Exception:
            log.exception("Failed to start MCP server on port %d", self._mcp_port)
            self._mcp_server = None

    async def _shutdown(self) -> None:
        """Gracefully shut down all components."""
        log.info("Shutting down...")

        if self._webhook_delivery is not None:
            # First, so nothing queues more work while the engine winds down. Does not
            # wait for the queue to drain: a daemon that will not exit because a
            # webhook endpoint is slow is a worse problem than a lost notification.
            with contextlib.suppress(Exception):
                await self._webhook_delivery.stop()
            self._webhook_delivery = None
            self._webhook_dispatcher = None

        if self._engine:
            await self._engine.stop()

        if self._mcp_server:
            await self._mcp_server.stop()

        if self._http_server:
            await self._http_server.stop()

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
            # Appended, not inserted at 0. Pairs are identified by position, so
            # inserting at the front renumbered every real pair — the demo pair took
            # `pair_0`, inheriting the first real pair's sync state and change token,
            # and each real pair then pointed at its neighbour's history. Demo mode
            # shares the real config and database, so that damage outlived the demo.
            self._config.sync.pairs.append(
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

    def _do_auth(self, provider: str = "gdrive", headless: bool = False, extra: dict | None = None) -> dict:
        """Run the auth flow for a given provider (called from a thread by IPC handler)."""
        if self._demo:
            log.info("Auth skipped in demo mode")
            return {"status": "ok", "message": "Demo mode — no real auth needed"}

        from cloud_drive_sync.providers.registry import get as get_provider

        self._log_auth_event("auth", f"Authentication started ({provider})", "in_progress")

        try:
            entry = get_provider(provider)
            auth_provider = entry.auth_cls()

            # Run the provider-specific auth flow.
            # extra carries pre-supplied credentials for non-OAuth providers (e.g. Nextcloud
            # server_url/username/app_password) so no TTY prompt is needed.
            # For OAuth providers (gdrive, dropbox…) extra is ignored.
            # If no TTY is available (HTTP API / Docker), _AuthUrlReady is raised
            # with the auth URL — return it so the HTTP client can show it.
            try:
                creds = auth_provider.run_auth_flow(headless=headless, extra=extra)
            except Exception as auth_exc:
                if type(auth_exc).__name__ == "_AuthUrlReady":
                    return {"status": "auth_url", "auth_url": str(auth_exc), "provider": provider}
                raise

            # Create client from credentials
            loop = self._loop

            async def _setup():
                client = await auth_provider.create_client(creds)
                email = await auth_provider.get_account_email(client)
                return client, email

            future = asyncio.run_coroutine_threadsafe(_setup(), loop)
            client, email = future.result(timeout=30)

            # Save credentials
            auth_provider.save_credentials(creds, email)

            # Add account to config if not exists
            if not any(a.email == email and a.provider == provider for a in self._config.accounts):
                self._config.accounts.append(
                    Account(email=email, display_name=email, provider=provider)
                )
                self._config.save()

            self._log_auth_event("auth", f"Authentication successful ({email})", "success")

            # Initialize or update engine with the new client
            if self._engine is None and self._db is not None:
                clients = {f"{provider}:{email}": client}
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
                    lambda: asyncio.ensure_future(self._start_engine_with_webhooks())
                )
                log.info("Sync engine initialized after authentication")

            elif self._engine is not None:
                self._engine._clients[f"{provider}:{email}"] = client
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

    def _exchange_auth_code(self, provider: str = "gdrive", code: str = "") -> dict:
        """Complete a two-step auth flow by exchanging the authorization code."""
        from cloud_drive_sync.providers.registry import get as get_provider

        try:
            entry = get_provider(provider)
            auth_provider = entry.auth_cls()

            # Call the provider's code exchange method
            if hasattr(auth_provider, "exchange_code"):
                creds = auth_provider.exchange_code(code)
            else:
                return {"status": "error", "message": f"Provider {provider} does not support code exchange"}

            # Complete setup (same as _do_auth after getting creds)
            loop = self._loop

            async def _setup():
                client = await auth_provider.create_client(creds)
                email = await auth_provider.get_account_email(client)
                return client, email

            future = asyncio.run_coroutine_threadsafe(_setup(), loop)
            client, email = future.result(timeout=30)

            auth_provider.save_credentials(creds, email)

            if not any(a.email == email and a.provider == provider for a in self._config.accounts):
                self._config.accounts.append(
                    Account(email=email, display_name=email, provider=provider)
                )
                self._config.save()

            self._log_auth_event("auth", f"Authentication successful ({email})", "success")

            if self._engine is None and self._db is not None:
                clients = {f"{provider}:{email}": client}
                self._engine = SyncEngine(self._config, self._db, client, clients=clients)
                self._handler.set_engine(self._engine)
                self._handler.set_drive_client(client)
                if self._ipc_server:
                    self._engine.set_notify_callback(self._ipc_server.notify_all)
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._start_engine_with_webhooks())
                )
            elif self._engine is not None:
                self._engine._clients[f"{provider}:{email}"] = client
                self._handler.set_drive_client(client)
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._restart_engine()))

            return {"status": "ok", "email": email}

        except Exception as exc:
            log.error("Code exchange failed: %s", exc)
            return {"status": "error", "message": str(exc)}

    async def _restart_engine(self) -> None:
        """Stop and restart the sync engine (e.g. after credential refresh)."""
        if self._engine:
            await self._engine.stop()
            await self._engine.start()

    async def _start_webhooks(self) -> None:
        """Stand up webhook delivery and subscribe it to the engine's event bus.

        Called from every path that creates an engine. The dispatcher is a second
        subscriber alongside the IPC notifier -- which is the whole reason the engine
        grew a bus instead of a single callback slot.
        """
        if self._engine is None or self._webhook_delivery is not None:
            return
        from cloud_drive_sync import __version__
        from cloud_drive_sync.webhooks.delivery import WebhookDelivery
        from cloud_drive_sync.webhooks.dispatcher import WebhookDispatcher
        from cloud_drive_sync.webhooks.identity import instance_id
        from cloud_drive_sync.webhooks.payload import PayloadContext

        context = PayloadContext(
            app="cloud-drive-sync",
            version=__version__,
            instance_id=instance_id(),
        )
        self._webhook_delivery = WebhookDelivery(context)
        await self._webhook_delivery.start()
        self._webhook_dispatcher = WebhookDispatcher(self._config, self._webhook_delivery)
        self._engine.bus.subscribe(self._webhook_dispatcher)

        configured = len(self._config.webhooks.targets) + sum(
            len(p.webhooks.targets) for p in self._config.sync.pairs
        )
        if configured:
            # Named at startup so an unexpected destination is discoverable rather
            # than only visible by reading the config.
            from cloud_drive_sync.webhooks.redaction import safe_endpoint
            hosts = sorted({
                safe_endpoint(t.url)
                for t in self._config.webhooks.targets
                if t.url
            } | {
                safe_endpoint(t.url)
                for pair in self._config.sync.pairs
                for t in pair.webhooks.targets
                if t.url
            })
            log.info(
                "Webhooks enabled: %d target definition(s), posting to %s",
                configured,
                ", ".join(hosts) or "(no url configured)",
            )

    async def _start_engine_with_webhooks(self) -> None:
        """Subscribe webhook delivery, then start the engine.

        Order matters: ``_initial_sync`` emits ``sync_complete`` for each pair, and a
        dispatcher subscribed afterwards would miss it. Used by the two authentication
        paths, which construct the engine on a worker thread and schedule its start
        back onto the loop.
        """
        await self._start_webhooks()
        if self._engine is not None:
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
        # `self._loop`, not `asyncio.get_event_loop()` (#58). This runs on a worker
        # thread -- `_do_auth` is invoked via `asyncio.to_thread` from the IPC and
        # HTTP handlers -- where `get_event_loop()` raises RuntimeError. The bare
        # `except RuntimeError: pass` then swallowed it, so every auth lifecycle row
        # raised through those paths was silently discarded, including the failures.
        loop = self._loop
        if loop is None:
            log.warning("Cannot record auth event %r: the event loop is not running yet", action)
            return
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._db.add_log_entry(entry))
        )

    @staticmethod
    def _discard_pid_file(pf) -> None:
        """Delete a pid file we have decided is stale, tolerating a read-only dir.

        A pid file that cannot be removed must not stop the daemon from starting.
        ``unlink`` used to raise straight out of ``is_running``, so ``status`` and
        ``start`` both died with a bare PermissionError when the runtime directory was
        not writable — which is a common state after the container's PUID remap.
        """
        try:
            pf.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not remove the stale pid file %s: %s", pf, exc)

    @staticmethod
    def _pid_is_this_daemon(pid: int) -> bool | None:
        """Whether ``pid`` looks like a cloud-drive-sync daemon.

        ``True`` yes, ``False`` definitely not, ``None`` cannot tell on this platform.

        Liveness alone is not enough to trust a pid file, for two reasons that both
        bite in practice:

        * **In a container the daemon is PID 1.** A pid file left behind by an unclean
          stop says ``1``, and on the next start ``os.kill(1, 0)`` succeeds — because
          it is asking about *itself*. Every start then refuses with "Daemon is already
          running", and since the run directory is a named volume the file survives
          restarts, so a restart policy loops forever on a daemon that never starts.
        * **Pids get reused.** After an unclean death the number in the file may belong
          to something else entirely, and ``stop`` would then SIGTERM an unrelated
          process.
        """
        if pid <= 1 or pid == os.getpid():
            # <=1 is never a daemon we started, and our own pid means the file is
            # describing this very process.
            return False
        if sys.platform != "linux":
            return None
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return False

        # Match the *arguments*, not any occurrence of the name in the blob.
        #
        # A substring test over the whole cmdline is wrong because the interpreter path
        # is part of it: a checkout or virtualenv at ~/cloud-drive-sync/.venv/bin/python
        # makes every unrelated script run from that venv look like the daemon — and
        # `stop` would then SIGTERM it. CI found this, because its workspace path is
        # /home/runner/work/cloud-drive-sync/...; a local run passed only because the
        # directory happened to be named differently.
        args = [a for a in cmdline.split(b"\0") if a]
        if not args:
            return False
        # `python -m cloud_drive_sync start ...` — the module name is its own argument,
        # matched exactly. The underscore form does not appear in ordinary paths.
        if b"cloud_drive_sync" in args:
            return True
        # The console script, where argv[0] *is* the executable. Only argv[0] is
        # considered: checking every argument would match `tar -czf b.tgz
        # ~/cloud-drive-sync` or `vim ~/cloud-drive-sync/notes.txt`, and treating
        # those as the daemon is how `stop` ends up signalling the wrong process.
        return args[0].rsplit(b"/", 1)[-1] in (b"cloud-drive-sync", b"cloud-drive-sync-daemon")

    @staticmethod
    def _pid_from_file(pf) -> int | None:
        try:
            return int(pf.read_text().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def is_running() -> bool:
        """Check whether a daemon instance is already running."""
        pf = pid_path()
        if not pf.exists():
            return False
        pid = Daemon._pid_from_file(pf)
        if pid is None:
            Daemon._discard_pid_file(pf)
            return False

        identity = Daemon._pid_is_this_daemon(pid)
        if identity is False:
            # Either our own pid (the container PID 1 case) or a process that is not
            # this daemon. Treat the file as stale so startup is not blocked forever.
            log.info(
                "Ignoring pid file %s: pid %d is not a cloud-drive-sync daemon", pf, pid
            )
            Daemon._discard_pid_file(pf)
            return False

        try:
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                Daemon._discard_pid_file(pf)
                return False
            os.kill(pid, 0)
            return True
        except OSError:
            Daemon._discard_pid_file(pf)
            return False

    @staticmethod
    def stop_running() -> bool:
        """Signal a running daemon to stop.

        Refuses to signal a pid it cannot identify as this daemon: the number in a
        stale pid file may since have been reused, and SIGTERM to an unrelated process
        is a far worse outcome than declining to stop.
        """
        pf = pid_path()
        if not pf.exists():
            return False
        pid = Daemon._pid_from_file(pf)
        if pid is None:
            Daemon._discard_pid_file(pf)
            return False

        if Daemon._pid_is_this_daemon(pid) is False:
            log.warning(
                "Refusing to signal pid %d from %s: it is not a cloud-drive-sync "
                "daemon. Discarding the stale pid file.",
                pid,
                pf,
            )
            Daemon._discard_pid_file(pf)
            return False

        try:
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(1, False, pid)
                if handle:
                    kernel32.TerminateProcess(handle, 0)
                    kernel32.CloseHandle(handle)
                else:
                    return False
            else:
                os.kill(pid, signal.SIGTERM)
            return True
        except OSError as exc:
            log.warning("Could not signal pid %d: %s", pid, exc)
            Daemon._discard_pid_file(pf)
            return False
